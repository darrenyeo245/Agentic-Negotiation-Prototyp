from dataclasses import asdict, replace
import json
from pathlib import Path
import random
from types import SimpleNamespace
import tempfile
import unittest

try:
    import anthropic
    import httpx2
    from anthropic.types.beta import BetaMessage
except ImportError:
    anthropic = None

from negotiation.audit import AuditLog
from negotiation.config import load_scenario
from negotiation.engine import Negotiation
from negotiation.models import AgentProfile, Decision, Observation, PublicTurn, Scenario
from negotiation.rules import RuleBook
from negotiation.strategies import ClaudeStrategy, LinearStrategy, StrategyError, StrategyOutput

ROOT = Path(__file__).resolve().parent.parent


def output(action="offer", price=8000, message="Mein Angebot.", rationale="Testentscheidung."):
    return StrategyOutput(asdict(Decision(action, price, message, rationale)), {})


class Scripted:
    name = "scripted_test"

    def __init__(self, *responses):
        self.responses = iter(responses)
        self.observations = []

    def propose(self, observation):
        self.observations.append(observation)
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load_scenario(ROOT / "config/default.json")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_case(self, buyer=None, seller=None, scenario=None):
        with AuditLog(Path(self.tmp.name)) as audit:
            engine = Negotiation(scenario or self.scenario, buyer or LinearStrategy(),
                                 seller or LinearStrategy(), audit)
            result = engine.run()
        events = [json.loads(line) for line in (audit.directory / "events.jsonl").read_text(
            encoding="utf-8").splitlines()]
        saved = json.loads((audit.directory / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], result.status)
        self.assertEqual(saved["price_cents"], result.price_cents)
        self.assertEqual(len([e for e in events if e["event"] == "finished"]), 1)
        self.assertEqual(events[-1]["event"], "finished")
        self.assertEqual([e["sequence"] for e in events], list(range(1, len(events) + 1)))
        self.assertTrue((audit.directory / "transcript.txt").exists())
        return result, events, engine

    def test_demo_agreement(self):
        result, _, _ = self.run_case()
        self.assertEqual(result.status, "agreement")
        self.assertEqual(result.price_cents, 11200)
        self.assertEqual(result.turns, 10)

    def test_no_overlap_aborts(self):
        scenario = load_scenario(ROOT / "config/no_deal.json")
        result, _, _ = self.run_case(scenario=scenario)
        self.assertEqual((result.status, result.reason, result.price_cents), ("aborted", "max_turns", None))
        self.assertEqual(result.turns, scenario.max_turns)

    def test_invalid_offer_never_reaches_opponent_and_retry_has_feedback(self):
        buyer = Scripted(output(price=13000, message="Nicht weitergeben"), output(price=11000))
        seller = Scripted(output("accept", 11000))
        result, events, _ = self.run_case(buyer, seller)
        self.assertEqual(result.status, "agreement")
        self.assertEqual(result.attempts, 3)
        self.assertTrue(buyer.observations[1].feedback)
        self.assertEqual(len(seller.observations[0].history), 1)
        self.assertEqual(seller.observations[0].history[0].price_cents, 11000)
        self.assertNotIn("Nicht weitergeben", json.dumps(seller.observations[0].to_dict()))
        self.assertEqual(sum(e["event"] == "proposed" for e in events), 3)
        self.assertEqual(sum(e["event"] == "rule_checked" and not e["valid"] for e in events), 1)

    def test_retry_exhaustion_aborts(self):
        buyer = Scripted(output(price=13000), output(price=13001))
        result, _, engine = self.run_case(buyer)
        self.assertEqual(result.reason, "invalid_decision_limit")
        self.assertEqual(engine.history, [])
        self.assertEqual(result.attempts, 2)

    def test_malformed_model_json_is_logged_then_rejected(self):
        buyer = Scripted(StrategyOutput("kein JSON", {}), output("withdraw", None))
        result, events, _ = self.run_case(buyer)
        self.assertEqual(result.reason, "agent_withdrew")
        self.assertTrue(any(e.get("raw") == "kein JSON" for e in events))

    def test_cannot_accept_before_an_offer(self):
        buyer = Scripted(output("accept", 11000), output("accept", 11000))
        result, _, _ = self.run_case(buyer)
        self.assertEqual(result.reason, "invalid_decision_limit")

    def test_cannot_change_price_while_accepting(self):
        seller = Scripted(output("accept", 11500), output("accept", 11500))
        result, _, _ = self.run_case(Scripted(output(price=11000)), seller)
        self.assertEqual(result.reason, "invalid_decision_limit")

    def test_replaced_offer_cannot_be_accepted(self):
        buyer = Scripted(output(price=11000), output("accept", 11000), output("withdraw", None))
        seller = Scripted(output(price=11500))
        result, _, _ = self.run_case(buyer, seller)
        self.assertEqual(result.reason, "agent_withdrew")

    def test_withdrawal(self):
        result, _, _ = self.run_case(Scripted(output("withdraw", None)))
        self.assertEqual((result.status, result.reason), ("aborted", "agent_withdrew"))

    def test_api_failure_aborts_without_demo_fallback(self):
        result, events, engine = self.run_case(Scripted(StrategyError("timeout")))
        self.assertEqual(result.reason, "strategy_error:timeout")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(engine.history, [])
        self.assertTrue(any(e["event"] == "strategy_error" for e in events))

    def test_plugin_failure_does_not_log_secret(self):
        result, events, _ = self.run_case(Scripted(RuntimeError("SECRET_API_KEY")))
        self.assertEqual(result.reason, "strategy_error:unexpected_strategy_error")
        self.assertNotIn("SECRET_API_KEY", json.dumps(events))

    def test_invalid_plugin_outputs_end_with_audited_abort(self):
        for bad_output in (None, {}, StrategyOutput({"price": float("nan")}, {}),
                           StrategyOutput(object(), {}), StrategyOutput({}, None)):
            with self.subTest(output_type=type(bad_output).__name__):
                result, _, engine = self.run_case(Scripted(bad_output))
                self.assertEqual(result.reason, "strategy_error:invalid_strategy_output")
                self.assertEqual(engine.history, [])

    def test_nonstandard_model_json_is_logged_and_rejected(self):
        strategy = ClaudeStrategy("TEST_SECRET", "test-model", client=object())
        for text in ('{"price_cents":NaN}', '{"price_cents":1e999}',
                     '{"action":"offer","action":"accept"}'):
            with self.subTest(text=text):
                decoded = strategy.decode_response({"stop_reason": "end_turn", "content": [
                    {"type": "text", "text": text}]})
                result, events, _ = self.run_case(Scripted(decoded, output("withdraw", None)))
                self.assertEqual(result.reason, "agent_withdrew")
                self.assertTrue(any(e["event"] == "rule_checked" and not e["valid"] for e in events))
                self.assertTrue(any(e.get("metadata", {}).get("raw_output_text") == text for e in events))

    def test_keyboard_interrupt_is_audited(self):
        result, _, _ = self.run_case(Scripted(KeyboardInterrupt()))
        self.assertEqual(result.reason, "interrupted")

    def test_boundary_deal_is_allowed(self):
        scenario = replace(self.scenario, buyer=replace(self.scenario.buyer, limit_cents=10000))
        result, _, _ = self.run_case(Scripted(output(price=10000)),
                                     Scripted(output("accept", 10000)), scenario)
        self.assertEqual(result.price_cents, 10000)

    def test_private_profiles_and_rationale_are_not_forwarded(self):
        seller = Scripted(output("accept", 11000))
        self.run_case(Scripted(output(price=11000, rationale="PrivateReasonXYZ")), seller)
        visible = seller.observations[0].to_dict()
        serialized = json.dumps(visible)
        self.assertEqual(visible["self_profile"]["role"], "seller")
        self.assertNotIn("PrivateReasonXYZ", serialized)
        self.assertNotIn("12000", serialized)
        self.assertNotIn("8000", serialized)

    def test_different_runs_do_not_overwrite(self):
        self.run_case()
        self.run_case()
        self.assertEqual(len(list(Path(self.tmp.name).iterdir())), 2)

    def test_single_use_engine(self):
        _, _, engine = self.run_case()
        with self.assertRaises(RuntimeError):
            engine.run()

    def test_100_scenarios_preserve_limits_and_always_terminate(self):
        rng = random.Random(314159)
        for _ in range(100):
            buyer_limit = rng.randint(100, 30000)
            seller_limit = rng.randint(100, 30000)
            scenario = Scenario("Testprodukt", "EUR",
                AgentProfile("buyer", "B", rng.randint(1, buyer_limit), buyer_limit),
                AgentProfile("seller", "S", rng.randint(seller_limit, 40000), seller_limit),
                max_turns=rng.randint(2, 21))
            result, _, engine = self.run_case(scenario=scenario)
            self.assertLessEqual(result.turns, scenario.max_turns)
            self.assertIn(result.status, ("agreement", "aborted"))
            for item in engine.history:
                if item.action in ("offer", "accept"):
                    self.assertTrue(scenario.profile(item.actor).allows(item.price_cents))
            if result.status == "agreement":
                self.assertLessEqual(seller_limit, result.price_cents)
                self.assertLessEqual(result.price_cents, buyer_limit)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load_scenario(ROOT / "config/default.json")

    def test_price_types_rejected(self):
        for price in (True, False, 1.5, "10000", None, -1, 0, 1_000_000_001):
            with self.subTest(price=price), self.assertRaises(ValueError):
                Decision.parse(output(price=price).raw)

    def test_extra_fields_and_unknown_actions_rejected(self):
        for raw in ({**output().raw, "new_limit": 999999}, output("override", 10000).raw,
                    output("withdraw", 10000).raw, output(message="\x1b[31m").raw):
            with self.assertRaises(ValueError):
                Decision.parse(raw)

    def test_seller_minimum_and_buyer_maximum(self):
        for role, price in (("seller", 9999), ("buyer", 12001)):
            obs = Observation("P", "EUR", self.scenario.profile(role), 1, 12, ())
            errors = RuleBook().validate(Decision("offer", price, "Test", "Test"), obs)
            self.assertTrue(errors)

    def test_invalid_config(self):
        raw = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
        bad_cases = [dict(raw, max_turns=0), dict(raw, max_turns=True),
                     dict(raw, max_attempts_per_turn=10), dict(raw, currency="USD"),
                     dict(raw, buyer={"name": "B", "target_cents": 13000, "limit_cents": 12000}),
                     dict(raw, seller={"name": "S", "target_cents": 9000, "limit_cents": 10000})]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            for value in bad_cases:
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_scenario(path)


def envelope(raw=None, text=None, stop_reason="end_turn"):
    return {"id": "msg_test", "type": "message", "role": "assistant", "model": "test-model",
            "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "content": [{"type": "thinking", "thinking": "", "signature": "sig"},
                        {"type": "text", "text": text if text is not None else json.dumps(raw or output().raw)}]}


class FakeClient:
    """Ersetzt den SDK-Client; liefert echte SDK-Antwortobjekte oder wirft SDK-Fehler."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return BetaMessage.model_validate(result)


class ClaudeDecodeTests(unittest.TestCase):
    def setUp(self):
        self.strategy = ClaudeStrategy("TEST_SECRET", "test-model", client=object())

    def test_refusal_and_incomplete_response(self):
        for payload, code in ((envelope(stop_reason="refusal"), "model_refusal"),
                              (envelope(stop_reason="max_tokens"), "incomplete_api_response"),
                              ({"stop_reason": "end_turn", "content": []}, "empty_model_response")):
            with self.subTest(code=code), self.assertRaisesRegex(StrategyError, code):
                self.strategy.decode_response(payload)

    def test_invalid_api_shape(self):
        for payload in ([], {"stop_reason": "end_turn", "content": None},
                        {"stop_reason": "end_turn", "content": [None]},
                        {"stop_reason": "end_turn", "content": [{"type": "text"}]}):
            with self.subTest(payload=payload), self.assertRaises(StrategyError):
                self.strategy.decode_response(payload)

    def test_invalid_json_kept_for_audit(self):
        self.assertEqual(self.strategy.decode_response(envelope(text="INVALID JSON")).raw, "INVALID JSON")

    def test_missing_sdk_is_explained(self):
        from negotiation import strategies
        original = strategies.anthropic
        strategies.anthropic = None
        try:
            with self.assertRaisesRegex(ValueError, "anthropic"):
                ClaudeStrategy("TEST_SECRET", "test-model")
        finally:
            strategies.anthropic = original


@unittest.skipUnless(anthropic, "Anthropic SDK nicht installiert")
class ClaudeAdapterTests(unittest.TestCase):
    def setUp(self):
        scenario = load_scenario(ROOT / "config/default.json")
        self.observation = Observation(scenario.product, "EUR", scenario.buyer, 1, 12, ())

    def strategy(self, *results, model="test-model"):
        client = FakeClient(*results)
        return ClaudeStrategy("TEST_SECRET", model, client=client), client

    def test_request_uses_schema_and_only_own_profile(self):
        strategy, _ = self.strategy()
        body = strategy.request_body(self.observation)
        self.assertEqual(body["output_config"]["format"]["type"], "json_schema")
        self.assertFalse(body["output_config"]["format"]["schema"]["additionalProperties"])
        self.assertNotIn("TEST_SECRET", json.dumps(body))
        observation = json.loads(body["messages"][0]["content"])
        self.assertEqual(observation["self_profile"]["role"], "buyer")
        self.assertNotIn("seller", observation)
        self.assertNotIn("fallbacks", body)

    def test_default_model_opts_into_refusal_fallback(self):
        strategy = ClaudeStrategy("TEST_SECRET", client=object())
        body = strategy.request_body(self.observation)
        self.assertEqual(body["model"], "claude-opus-5-5")
        self.assertEqual(body["fallbacks"], "default")
        self.assertEqual(body["betas"], ["server-side-fallback-2026-07-01"])

    def test_sdk_request_and_response(self):
        strategy, client = self.strategy(envelope())
        result = strategy.propose(self.observation)
        self.assertEqual(result.raw["price_cents"], 8000)
        self.assertTrue(client.calls[0]["system"].startswith("Du bist ein autonomer"))
        self.assertEqual(result.metadata["usage"]["input_tokens"], 100)
        self.assertEqual(result.metadata["response_id"], "msg_test")
        json.dumps(result.metadata)  # Metadaten muessen JSON-kompatibel sein.

    def test_real_client_receives_key_and_timeout(self):
        strategy = ClaudeStrategy("TEST_SECRET", "test-model", timeout=5)
        self.assertEqual(strategy._client.api_key, "TEST_SECRET")
        self.assertEqual(strategy._client.timeout, 5)

    def test_sdk_errors_have_safe_codes(self):
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        failures = [
            (anthropic.APITimeoutError(request=request), "timeout"),
            (anthropic.APIConnectionError(message="TEST_SECRET", request=request), "connection_error"),
            (anthropic.AuthenticationError("TEST_SECRET", response=httpx2.Response(401, request=request),
                                           body=None), "http_401"),
        ]
        for error, code in failures:
            strategy, _ = self.strategy(error)
            with self.subTest(code=code), self.assertRaises(StrategyError) as caught:
                strategy.propose(self.observation)
            self.assertEqual(str(caught.exception), code)
            self.assertIsNone(caught.exception.__cause__)


if __name__ == "__main__":
    unittest.main()
