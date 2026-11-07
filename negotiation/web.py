"""Lokale Browser-Oberflaeche. Nur Python-Standardbibliothek; erreichbar nur ueber 127.0.0.1.

Der API-Schluessel bleibt im Serverprozess. Der Browser sieht nur Szenario, Verlauf und Ergebnis.
"""

import argparse
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
from urllib.parse import urlsplit
from uuid import uuid4
import webbrowser

from . import strategies
from .audit import AuditLog
from .cli import ROOT, load_env
from .config import load_scenario, parse_scenario
from .engine import Negotiation
from .json_support import loads
from .scenario_text import MAX_DESCRIPTION, ScenarioParser
from .strategies import DEFAULT_MODEL, ClaudeStrategy, LinearStrategy, StrategyError

PAGE = Path(__file__).resolve().parent / "web" / "index.html"
MAX_BODY = 32_000
KEEPALIVE_SECONDS = 15


class LiveRun:
    """Ereignisliste eines Laufs; SSE-Clients lesen ab einer Position und warten auf Neues."""

    def __init__(self):
        self.id = uuid4().hex
        self.events: list[dict] = []
        self.done = False
        self.cancelled = threading.Event()
        self._cond = threading.Condition()

    def push(self, event: dict) -> None:
        with self._cond:
            self.events.append(event)
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self.done = True
            self._cond.notify_all()

    def wait_from(self, index: int, timeout: float) -> tuple[list[dict], bool]:
        with self._cond:
            self._cond.wait_for(lambda: len(self.events) > index or self.done, timeout)
            return self.events[index:], self.done


class StreamingAudit(AuditLog):
    """Schreibt wie gewohnt auf die Platte und spiegelt jedes Ereignis in den Live-Lauf."""

    def __init__(self, root: Path, live: LiveRun):
        super().__init__(root)
        self.live = live

    def record(self, event: str, **data) -> None:
        super().record(event, **data)
        public = {key: value for key, value in data.items() if key not in ("observation", "metadata")}
        metadata = data.get("metadata")
        if isinstance(metadata, dict) and metadata.get("model"):
            public["model"] = metadata["model"]
        if event == "finished":
            public["log_dir"] = str(self.directory.resolve())
        self.live.push({"event": event, **public})


class Cancellable:
    """Bricht vor dem naechsten Strategieaufruf ab, ohne laufende API-Aufrufe zu kappen."""

    def __init__(self, inner, flag: threading.Event):
        self.inner = inner
        self.flag = flag
        self.name = inner.name

    def propose(self, observation):
        if self.flag.is_set():
            raise StrategyError("cancelled")
        return self.inner.propose(observation)


class App:
    def __init__(self, api_key: str, model: str, log_dir: Path, timeout: float = 60):
        self.api_key = api_key
        self.model = model
        self.log_dir = log_dir
        self.timeout = timeout
        self.runs: dict[str, LiveRun] = {}
        self._lock = threading.Lock()

    @property
    def llm_available(self) -> bool:
        return bool(self.api_key) and strategies.anthropic is not None

    def status(self) -> dict:
        examples = {}
        for path in sorted((ROOT / "config").glob("*.json")):
            try:
                examples[path.stem] = asdict(load_scenario(path))
            except (ValueError, OSError):
                continue
        hint = None
        if strategies.anthropic is None:
            hint = "Paket 'anthropic' fehlt: python -m pip install -r requirements.txt"
        elif not self.api_key:
            hint = "ANTHROPIC_API_KEY fehlt (.env oder Umgebungsvariable)."
        return {"llm_available": self.llm_available, "model": self.model, "hint": hint,
                "examples": examples}

    def parse_text(self, description: str) -> dict:
        if not self.llm_available:
            raise ValueError("Spracheingabe braucht Claude: " + (self.status()["hint"] or ""))
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Bitte eine Beschreibung eingeben.")
        if len(description) > MAX_DESCRIPTION:
            raise ValueError(f"Beschreibung hoechstens {MAX_DESCRIPTION} Zeichen.")
        draft = ScenarioParser(self.api_key, self.model, self.timeout).parse(description.strip())
        try:
            parse_scenario(draft["scenario"])
            draft["errors"] = []
        except ValueError as exc:
            draft["errors"] = [str(exc)]
        return draft

    def start(self, payload: dict) -> LiveRun:
        if not isinstance(payload, dict):
            raise ValueError("Ungueltige Anfrage.")
        scenario = parse_scenario(payload.get("scenario"))
        modes = {role: payload.get(role) for role in ("buyer", "seller")}
        if any(mode not in ("demo", "claude") for mode in modes.values()):
            raise ValueError("Strategie muss 'demo' oder 'claude' sein.")
        if "claude" in modes.values() and not self.llm_available:
            raise ValueError("Claude ist nicht verfuegbar: " + (self.status()["hint"] or ""))
        live = LiveRun()
        roles = {role: (ClaudeStrategy(self.api_key, self.model, self.timeout) if mode == "claude"
                        else LinearStrategy()) for role, mode in modes.items()}
        with self._lock:
            self.runs[live.id] = live
        threading.Thread(target=self._execute, args=(live, scenario, roles, modes), daemon=True).start()
        return live

    def _execute(self, live: LiveRun, scenario, roles: dict, modes: dict) -> None:
        try:
            with StreamingAudit(self.log_dir, live) as audit:
                audit.record("run_metadata", interface="web", modes=modes,
                             model=self.model if "claude" in modes.values() else None)
                Negotiation(scenario, Cancellable(roles["buyer"], live.cancelled),
                            Cancellable(roles["seller"], live.cancelled), audit).run()
        except Exception as exc:  # Nur der Typ; Meldungen koennten Geheimnisse enthalten.
            live.push({"event": "server_error", "error_type": type(exc).__name__})
        finally:
            live.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "AgenticNegotiation"
    app: App
    allowed_hosts: frozenset

    def log_message(self, *_):
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        # Schutz gegen DNS-Rebinding: fremde Webseiten duerfen den lokalen Server nicht ansprechen.
        if self.headers.get("Host", "") in self.allowed_hosts:
            return True
        self._json(403, {"error": "Unbekannter Host."})
        return False

    def do_GET(self):
        if not self._host_ok():
            return
        path = urlsplit(self.path).path
        if path == "/":
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/status":
            self._json(200, self.app.status())
        elif path.startswith("/api/runs/") and path.endswith("/events"):
            self._stream(path.split("/")[3])
        else:
            self._json(404, {"error": "Nicht gefunden."})

    def do_POST(self):
        if not self._host_ok():
            return
        # Nur JSON: Browser senden das von fremden Seiten nicht ohne CORS-Preflight,
        # den dieser Server nie beantwortet.
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._json(415, {"error": "Content-Type application/json erforderlich."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY:
            return self._json(413, {"error": "Anfrage fehlt oder ist zu gross."})
        try:
            payload = loads(self.rfile.read(length))
        except (ValueError, UnicodeError):
            return self._json(400, {"error": "Ungueltiges JSON."})
        path = urlsplit(self.path).path
        try:
            if path == "/api/parse":
                description = payload.get("text") if isinstance(payload, dict) else None
                self._json(200, self.app.parse_text(description))
            elif path == "/api/runs":
                self._json(201, {"run_id": self.app.start(payload).id})
            elif path.startswith("/api/runs/") and path.endswith("/cancel"):
                live = self.app.runs.get(path.split("/")[3])
                if live is None:
                    return self._json(404, {"error": "Lauf unbekannt."})
                live.cancelled.set()
                self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "Nicht gefunden."})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except StrategyError as exc:
            self._json(502, {"error": f"Claude-Aufruf fehlgeschlagen ({exc.code})."})

    def _stream(self, run_id: str) -> None:
        live = self.app.runs.get(run_id)
        if live is None:
            return self._json(404, {"error": "Lauf unbekannt."})
        try:
            index = int(self.headers.get("Last-Event-ID", "-1")) + 1
        except ValueError:
            index = 0
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            while True:
                events, done = live.wait_from(index, KEEPALIVE_SECONDS)
                for event in events:
                    data = json.dumps(event, ensure_ascii=False)
                    self.wfile.write(f"id: {index}\ndata: {data}\n\n".encode("utf-8"))
                    index += 1
                if not events:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
                if done and index >= len(live.events):
                    return
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return


def make_server(app: App, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    actual = server.server_address[1]
    handler = type("BoundHandler", (Handler,), {
        "app": app, "allowed_hosts": frozenset({f"127.0.0.1:{actual}", f"localhost:{actual}"})})
    server.RequestHandlerClass = handler
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Browser-Oberflaeche fuer die Preisverhandlung.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log-dir", type=Path, default=ROOT / "runs")
    parser.add_argument("--model", help=f"Modellname; sonst NEGOTIATION_MODEL oder {DEFAULT_MODEL}.")
    parser.add_argument("--no-browser", action="store_true", help="Browser nicht automatisch oeffnen.")
    args = parser.parse_args(argv)
    try:
        load_env(ROOT / ".env")
    except (ValueError, OSError) as exc:
        print(f"Start-/Dateifehler: {exc}", file=sys.stderr)
        return 2
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key == "dein-api-schluessel":
        api_key = ""
    model = args.model or os.environ.get("NEGOTIATION_MODEL") or DEFAULT_MODEL
    app = App(api_key, model, args.log_dir)
    try:
        server = make_server(app, args.port)
    except OSError as exc:
        print(f"Port {args.port} nicht verfuegbar ({exc.strerror}). Anderen Port mit --port waehlen.",
              file=sys.stderr)
        return 2
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Agentic Negotiation | Browser-Oberflaeche: {url}")
    print("Claude: " + (f"verfuegbar ({model}) - API-Aufrufe koennen Kosten verursachen."
                        if app.llm_available else "nicht verfuegbar - " + app.status()["hint"]))
    print("Beenden mit Strg+C.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
