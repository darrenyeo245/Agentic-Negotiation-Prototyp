"""CLI; die Offline-Demo laeuft ohne externe Python-Pakete, der Claude-Modus braucht `anthropic`."""

import argparse
import json
import math
import os
from pathlib import Path
import sys

from . import __version__
from .audit import AuditLog
from .config import load_scenario
from .engine import Negotiation
from .models import PublicTurn, money
from .strategies import DEFAULT_MODEL, ClaudeStrategy, LinearStrategy, SYSTEM_PROMPT, DECISION_SCHEMA

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path) -> None:
    """Kleines .env-Format: NAME=VALUE, optionale Anfuehrungszeichen, keine Expansion."""
    if not path.exists():
        return
    allowed = {"ANTHROPIC_API_KEY", "NEGOTIATION_MODEL"}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or key not in allowed:
            raise ValueError(".env erlaubt nur ANTHROPIC_API_KEY und NEGOTIATION_MODEL als NAME=VALUE.")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def show_turn(item: PublicTurn) -> None:
    name = "Kaeufer" if item.actor == "buyer" else "Verkaeufer"
    print(f"[{item.turn:02d}] {name:10s} | {item.action:8s} | {money(item.price_cents)}")
    print(f"     {item.message}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zwei Agenten verhandeln einen Produktpreis.")
    parser.add_argument("--mode", choices=("demo", "claude"), default="demo")
    parser.add_argument("--buyer-strategy", choices=("demo", "claude"), help="Ueberschreibt --mode fuer den Kaeufer.")
    parser.add_argument("--seller-strategy", choices=("demo", "claude"), help="Ueberschreibt --mode fuer den Verkaeufer.")
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "default.json")
    parser.add_argument("--log-dir", type=Path, default=ROOT / "runs")
    parser.add_argument("--model", help=f"Modellname; sonst NEGOTIATION_MODEL oder {DEFAULT_MODEL}.")
    parser.add_argument("--timeout", type=float, default=60, help="API-Timeout in Sekunden (1 bis 120).")
    parser.add_argument("--quiet", action="store_true", help="Nur Ergebnis ausgeben.")
    args = parser.parse_args(argv)
    try:
        scenario = load_scenario(args.config)
        if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 120:
            raise ValueError("--timeout muss zwischen 1 und 120 liegen.")
        modes = {"buyer": args.buyer_strategy or args.mode, "seller": args.seller_strategy or args.mode}
        uses_llm = "claude" in modes.values()
        if uses_llm:
            load_env(ROOT / ".env")
        model = args.model or os.environ.get("NEGOTIATION_MODEL") or DEFAULT_MODEL
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if uses_llm and (not api_key or api_key == "dein-api-schluessel"):
            raise ValueError("ANTHROPIC_API_KEY fehlt nach .env kopieren und Schluessel lokal eintragen.")
        strategies = {role: ClaudeStrategy(api_key, model, args.timeout) if mode == "claude"
                      else LinearStrategy() for role, mode in modes.items()}
        print("Agentic Negotiation | " + ("LLM-Modus" if uses_llm else "OFFLINE-DEMO (kein LLM)"))
        print(f"Produkt: {scenario.product}")
        print(f"Strategien: buyer={modes['buyer']}, seller={modes['seller']}")
        if uses_llm:
            print(f"Modell: {model} | API-Aufrufe koennen Kosten verursachen.")
        with AuditLog(args.log_dir) as audit:
            audit.record("run_metadata", prototype_version=__version__, python_version=sys.version.split()[0],
                         config_file=args.config.name, modes=modes, model=model if uses_llm else None,
                         timeout_seconds=args.timeout if uses_llm else None,
                         system_prompt=SYSTEM_PROMPT if uses_llm else None,
                         decision_schema=DECISION_SCHEMA if uses_llm else None)
            result = Negotiation(scenario, strategies["buyer"], strategies["seller"], audit,
                                 None if args.quiet else show_turn).run()
            label = "EINIGUNG" if result.status == "agreement" else "ABBRUCH"
            print(f"\n{label}: {money(result.price_cents)} | Grund: {result.reason}")
            print(f"Zuege: {result.turns} | Versuche: {result.attempts}")
            print(f"Protokoll: {audit.directory.resolve()}")
        if result.reason == "interrupted":
            return 130
        return 1 if result.reason.startswith(("strategy_error", "invalid_decision", "settlement_rule")) else 0
    except (ValueError, OSError) as exc:
        print(f"Start-/Dateifehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
