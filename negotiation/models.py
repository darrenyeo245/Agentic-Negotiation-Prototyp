"""Unveraenderliche Datenobjekte; Preise werden in ganzen Cent gespeichert."""

from dataclasses import asdict, dataclass
from typing import Literal

Role = Literal["buyer", "seller"]
Action = Literal["offer", "accept", "withdraw"]


@dataclass(frozen=True)
class AgentProfile:
    role: Role
    name: str
    target_cents: int
    limit_cents: int

    def allows(self, price: int) -> bool:
        if type(price) is not int or price <= 0:
            return False
        return price <= self.limit_cents if self.role == "buyer" else price >= self.limit_cents


@dataclass(frozen=True)
class Scenario:
    product: str
    currency: str
    buyer: AgentProfile
    seller: AgentProfile
    max_turns: int = 12
    max_attempts_per_turn: int = 2

    def profile(self, role: Role) -> AgentProfile:
        return self.buyer if role == "buyer" else self.seller


@dataclass(frozen=True)
class Decision:
    action: Action
    price_cents: int | None
    message: str
    rationale: str

    @classmethod
    def parse(cls, raw: object) -> "Decision":
        fields = {"action", "price_cents", "message", "rationale"}
        if not isinstance(raw, dict) or set(raw) != fields:
            raise ValueError("Genau action, price_cents, message und rationale sind erforderlich.")
        if raw["action"] not in ("offer", "accept", "withdraw"):
            raise ValueError("Unbekannte Aktion.")
        price = raw["price_cents"]
        if raw["action"] == "withdraw":
            if price is not None:
                raise ValueError("withdraw erfordert price_cents=null.")
        elif type(price) is not int or not 0 < price <= 1_000_000_000:
            raise ValueError("Der Preis muss eine positive ganze Cent-Zahl bis 1000000000 sein.")
        for field in ("message", "rationale"):
            if not isinstance(raw[field], str) or not 1 <= len(raw[field].strip()) <= 1000:
                raise ValueError(f"{field} muss 1 bis 1000 Zeichen enthalten.")
            if any(ord(char) < 32 and char not in "\n\t" for char in raw[field]):
                raise ValueError(f"{field} enthaelt unzulaessige Steuerzeichen.")
        return cls(**raw)


@dataclass(frozen=True)
class PublicTurn:
    turn: int
    actor: Role
    action: Action
    price_cents: int | None
    message: str


@dataclass(frozen=True)
class Observation:
    """Nur eigenes Profil und oeffentliche Historie: keine Gegnergrenzen."""

    product: str
    currency: str
    self_profile: AgentProfile
    turn: int
    max_turns: int
    history: tuple[PublicTurn, ...]
    feedback: tuple[str, ...] = ()

    @property
    def pending_offer(self) -> PublicTurn | None:
        if self.history and self.history[-1].action == "offer":
            return self.history[-1]
        return None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Result:
    status: Literal["agreement", "aborted"]
    reason: str
    price_cents: int | None
    turns: int
    attempts: int


def money(cents: int | None, currency: str = "EUR") -> str:
    if cents is None:
        return "-"
    return f"{cents // 100},{cents % 100:02d} {currency}"
