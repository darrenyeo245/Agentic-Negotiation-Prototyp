"""Sequentieller Nachrichtenaustausch mit begrenztem, auditiertem Ablauf."""

from dataclasses import asdict
import json
from typing import Callable

from .audit import AuditLog
from .models import Decision, Observation, PublicTurn, Result, Scenario
from .rules import RuleBook
from .strategies import Strategy, StrategyError, StrategyOutput


class Negotiation:
    def __init__(self, scenario: Scenario, buyer: Strategy, seller: Strategy,
                 audit: AuditLog, on_turn: Callable[[PublicTurn], None] | None = None):
        self.scenario = scenario
        self.strategies = {"buyer": buyer, "seller": seller}
        self.audit = audit
        self.on_turn = on_turn
        self.rules = RuleBook()
        self.history: list[PublicTurn] = []
        self.attempts = 0
        self.started = False
        self.result: Result | None = None

    def _finish(self, reason: str, turn: int, price: int | None = None) -> Result:
        if self.result is not None:
            return self.result
        result = Result("agreement" if price is not None else "aborted", reason,
                        price, turn, self.attempts)
        self.audit.finish(result, tuple(self.history))
        self.result = result
        return result

    def run(self) -> Result:
        if self.started:
            raise RuntimeError("Jede Negotiation-Instanz darf nur einmal laufen.")
        self.started = True
        self.audit.record("started", scenario=asdict(self.scenario),
                          strategies={role: strategy.name for role, strategy in self.strategies.items()})
        turn = 0
        try:
            for turn in range(1, self.scenario.max_turns + 1):
                role = "buyer" if turn % 2 else "seller"
                strategy = self.strategies[role]
                feedback: tuple[str, ...] = ()
                for attempt in range(1, self.scenario.max_attempts_per_turn + 1):
                    observation = Observation(
                        self.scenario.product, self.scenario.currency, self.scenario.profile(role),
                        turn, self.scenario.max_turns, tuple(self.history), feedback)
                    self.attempts += 1
                    self.audit.record("decision_requested", turn=turn, actor=role, attempt=attempt,
                                      observation=observation.to_dict())
                    try:
                        output = strategy.propose(observation)
                        if not isinstance(output, StrategyOutput) or not isinstance(output.metadata, dict):
                            raise StrategyError("invalid_strategy_output")
                        try:
                            json.dumps({"raw": output.raw, "metadata": output.metadata}, allow_nan=False)
                        except (ValueError, TypeError, RecursionError):
                            raise StrategyError("invalid_strategy_output") from None
                    except StrategyError as exc:
                        self.audit.record("strategy_error", turn=turn, actor=role, code=exc.code)
                        return self._finish("strategy_error:" + exc.code, turn)
                    except Exception as exc:
                        # Drittstrategien koennen Fehler mit Geheimnissen im Text werfen.
                        # Nur den Typ protokollieren, niemals die Fehlermeldung.
                        self.audit.record("strategy_error", turn=turn, actor=role,
                                          code="unexpected_strategy_error", error_type=type(exc).__name__)
                        return self._finish("strategy_error:unexpected_strategy_error", turn)
                    self.audit.record("proposed", turn=turn, actor=role, attempt=attempt,
                                      raw=output.raw, metadata=output.metadata)
                    try:
                        decision = Decision.parse(output.raw)
                        feedback = self.rules.validate(decision, observation)
                    except ValueError as exc:
                        feedback = (str(exc),)
                    self.audit.record("rule_checked", turn=turn, actor=role, attempt=attempt,
                                      valid=not feedback, violations=feedback)
                    if not feedback:
                        break
                else:
                    return self._finish("invalid_decision_limit", turn)

                item = PublicTurn(turn, role, decision.action, decision.price_cents, decision.message)
                if decision.action == "accept" and not self.rules.settlement_allowed(
                        decision.price_cents, self.scenario):
                    return self._finish("settlement_rule_violation", turn)
                self.history.append(item)
                self.audit.record("committed", turn=turn, actor=role, decision=asdict(decision))
                if decision.action == "accept":
                    result = self._finish("accepted_offer", turn, decision.price_cents)
                    if self.on_turn:
                        self.on_turn(item)
                    return result
                if decision.action == "withdraw":
                    result = self._finish("agent_withdrew", turn)
                    if self.on_turn:
                        self.on_turn(item)
                    return result
                if self.on_turn:
                    self.on_turn(item)
            return self._finish("max_turns", turn)
        except KeyboardInterrupt:
            return self._finish("interrupted", turn)
