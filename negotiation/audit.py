"""JSONL-Ereignisse plus lesbarem Protokoll und maschinenlesbarem Ergebnis."""

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from .models import PublicTurn, Result, money


class AuditLog:
    def __init__(self, root: Path):
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
        self.directory = root / self.run_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self._file = (self.directory / "events.jsonl").open("x", encoding="utf-8")
        self._sequence = 0

    def record(self, event: str, **data) -> None:
        entry = {"schema_version": 1, "run_id": self.run_id, "sequence": self._sequence + 1,
                 "timestamp_utc": datetime.now(timezone.utc).isoformat(), "event": event, **data}
        self._file.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n")
        self._file.flush()
        self._sequence += 1

    def finish(self, result: Result, history: tuple[PublicTurn, ...]) -> None:
        self.record("finished", result=asdict(result))
        (self.directory / "result.json").write_text(
            json.dumps({"schema_version": 1, "run_id": self.run_id, **asdict(result)},
                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lines = ["Agentic Negotiation - Protokoll", f"Lauf: {self.run_id}", "",
                 "Verbindlich sind Aktion und strukturierter Preis. Nachricht = Freitext.", ""]
        for item in history:
            lines += [f"Zug {item.turn} | {item.actor} | {item.action} | {money(item.price_cents)}",
                      item.message, ""]
        lines += [f"Ergebnis: {result.status}", f"Grund: {result.reason}",
                  f"Preis: {money(result.price_cents)}", f"Versuche: {result.attempts}",
                  "", "Alle Vorschlaege, Ablehnungen und Kurzbegruendungen: events.jsonl"]
        (self.directory / "transcript.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def close(self) -> None:
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
