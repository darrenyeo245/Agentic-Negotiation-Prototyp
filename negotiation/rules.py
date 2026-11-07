"""Autoritative Geschaeftsregeln, unabhaengig vom LLM und der Strategie."""

from .models import Decision, Observation, Scenario


class RuleBook:
    def validate(self, decision: Decision, observation: Observation) -> tuple[str, ...]:
        if decision.action == "withdraw":
            return ()
        errors = []
        if not observation.self_profile.allows(decision.price_cents):
            errors.append("Preis verletzt deine feste Preisgrenze.")
        if decision.action == "accept":
            pending = observation.pending_offer
            if pending is None or pending.actor == observation.self_profile.role:
                errors.append("Annahme erfordert ein offenes Angebot der Gegenseite.")
            elif decision.price_cents != pending.price_cents:
                errors.append("Annahme muss exakt dem letzten Gegenangebot entsprechen.")
        return tuple(errors)

    def settlement_allowed(self, price: int, scenario: Scenario) -> bool:
        """Zweite Pruefung vor einem Abschluss: beide Grenzen sind bindend."""
        return scenario.buyer.allows(price) and scenario.seller.allows(price)
