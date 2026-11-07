"""Strikte Konfiguration, damit Tippfehler nicht stillschweigend wirken."""

from pathlib import Path

from .json_support import loads
from .models import AgentProfile, Scenario


def integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label}: ganze Zahl zwischen {minimum} und {maximum} erforderlich.")
    return value


def profile(role: str, value: object) -> AgentProfile:
    if not isinstance(value, dict) or set(value) != {"name", "target_cents", "limit_cents"}:
        raise ValueError(f"{role}: name, target_cents und limit_cents erforderlich.")
    name = value["name"]
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise ValueError(f"{role}: ungueltiger Name.")
    target = integer(value["target_cents"], f"{role}.target_cents", 1, 1_000_000_000)
    limit = integer(value["limit_cents"], f"{role}.limit_cents", 1, 1_000_000_000)
    if (role == "buyer" and target > limit) or (role == "seller" and target < limit):
        raise ValueError(f"{role}: Zielpreis liegt jenseits der eigenen Preisgrenze.")
    return AgentProfile(role, name.strip(), target, limit)


def load_scenario(path: Path) -> Scenario:
    return parse_scenario(loads(path.read_text(encoding="utf-8-sig")))


def parse_scenario(raw: object) -> Scenario:
    """Prueft ein Szenario aus Datei, Browser oder Sprachauswertung nach denselben Regeln."""
    fields = {"product", "currency", "buyer", "seller", "max_turns", "max_attempts_per_turn"}
    if not isinstance(raw, dict) or set(raw) != fields:
        raise ValueError("Szenariofelder: " + ", ".join(sorted(fields)))
    if not isinstance(raw["product"], str) or not 1 <= len(raw["product"].strip()) <= 500:
        raise ValueError("product muss 1 bis 500 Zeichen enthalten.")
    if raw["currency"] != "EUR":
        raise ValueError("Der Prototyp unterstuetzt ausschliesslich EUR.")
    return Scenario(
        raw["product"].strip(), "EUR", profile("buyer", raw["buyer"]),
        profile("seller", raw["seller"]),
        integer(raw["max_turns"], "max_turns", 2, 100),
        integer(raw["max_attempts_per_turn"], "max_attempts_per_turn", 1, 3),
    )
