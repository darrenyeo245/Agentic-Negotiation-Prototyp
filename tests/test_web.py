"""Browser-Server: echte HTTP-Anfragen gegen 127.0.0.1, ohne externe API-Aufrufe."""

import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from negotiation.config import parse_scenario
from negotiation.strategies import LinearStrategy
from negotiation.web import App, LiveRun, make_server
from test_negotiation import FakeClient, anthropic

ROOT = Path(__file__).resolve().parent.parent


def scenario(**changes):
    raw = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
    raw.update(changes)
    return raw


class WebTests(unittest.TestCase):
    def start_server(self, api_key=""):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.logs = Path(tmp.name)
        self.app = App(api_key, "test-model", self.logs)
        server = make_server(self.app, 0)
        self.port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

    def setUp(self):
        self.start_server()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(connection.close)
        data = None if body is None else json.dumps(body).encode()
        base = {"Content-Type": "application/json"} if data is not None else {}
        connection.request(method, path, body=data, headers={**base, **(headers or {})})
        response = connection.getresponse()
        return response.status, response.read()

    def events(self, run_id):
        status, raw = self.request("GET", f"/api/runs/{run_id}/events")
        self.assertEqual(status, 200)
        return [json.loads(line[6:]) for line in raw.decode().splitlines() if line.startswith("data: ")]

    def test_page_and_status(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Agentic Negotiation", page)
        status, raw = self.request("GET", "/api/status")
        info = json.loads(raw)
        self.assertFalse(info["llm_available"])
        self.assertIn("default", info["examples"])
        self.assertIn("ANTHROPIC_API_KEY", info["hint"])

    def test_foreign_host_and_non_json_are_rejected(self):
        status, _ = self.request("GET", "/api/status", headers={"Host": "evil.example:80"})
        self.assertEqual(status, 403)
        status, _ = self.request("POST", "/api/runs", body={}, headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)

    def test_demo_run_streams_until_agreement(self):
        status, raw = self.request("POST", "/api/runs", {"scenario": scenario(), "buyer": "demo", "seller": "demo"})
        self.assertEqual(status, 201)
        events = self.events(json.loads(raw)["run_id"])
        kinds = [e["event"] for e in events]
        self.assertEqual(kinds[-1], "finished")
        self.assertEqual(events[-1]["result"]["price_cents"], 11200)
        self.assertEqual(kinds.count("committed"), 10)
        self.assertFalse(any("observation" in e for e in events))
        self.assertTrue(Path(events[-1]["log_dir"], "result.json").exists())

    def test_stream_resumes_after_last_event_id(self):
        _, raw = self.request("POST", "/api/runs", {"scenario": scenario(), "buyer": "demo", "seller": "demo"})
        run_id = json.loads(raw)["run_id"]
        total = len(self.events(run_id))
        status, rest = self.request("GET", f"/api/runs/{run_id}/events", headers={"Last-Event-ID": str(total - 3)})
        self.assertEqual(rest.decode().count("data: "), 2)

    def test_invalid_scenario_and_unavailable_claude(self):
        bad = scenario(buyer={"name": "B", "target_cents": 13000, "limit_cents": 12000})
        status, raw = self.request("POST", "/api/runs", {"scenario": bad, "buyer": "demo", "seller": "demo"})
        self.assertEqual(status, 400)
        self.assertIn("Zielpreis", json.loads(raw)["error"])
        status, _ = self.request("POST", "/api/runs", {"scenario": scenario(), "buyer": "claude", "seller": "demo"})
        self.assertEqual(status, 400)
        status, _ = self.request("POST", "/api/parse", {"text": "Fahrrad"})
        self.assertEqual(status, 400)

    def test_cancel_stops_before_next_turn(self):
        live = LiveRun()
        live.cancelled.set()
        self.app._execute(live, parse_scenario(scenario()),
                          {"buyer": LinearStrategy(), "seller": LinearStrategy()}, {"buyer": "demo", "seller": "demo"})
        self.assertEqual(live.events[-1]["result"]["reason"], "strategy_error:cancelled")
        self.app.runs[live.id] = live
        status, _ = self.request("POST", f"/api/runs/{live.id}/cancel", {})
        self.assertEqual(status, 200)
        status, _ = self.request("POST", "/api/runs/unbekannt/cancel", {})
        self.assertEqual(status, 404)


@unittest.skipUnless(anthropic, "Anthropic SDK nicht installiert")
class WebClaudeTests(WebTests):
    def setUp(self):
        self.start_server(api_key="LOCAL_TEST_SECRET")

    def test_page_and_status(self):
        _, raw = self.request("GET", "/api/status")
        self.assertTrue(json.loads(raw)["llm_available"])
        self.assertNotIn("LOCAL_TEST_SECRET", raw.decode())

    def test_invalid_scenario_and_unavailable_claude(self):
        pass

    def test_natural_language_becomes_reviewable_draft(self):
        from test_negotiation import envelope
        draft = {"product": "Trekkingrad, 28 Zoll, Abholung Berlin", "buyer_target_eur": 300,
                 "buyer_limit_eur": 420.5, "seller_target_eur": 480, "seller_limit_eur": 380,
                 "max_turns": 12, "assumptions": ["Zugzahl nicht genannt, 12 angenommen."]}
        client = FakeClient(envelope(text=json.dumps(draft)))
        with patch("negotiation.strategies.create_client", return_value=client):
            status, raw = self.request("POST", "/api/parse", {"text": "Ich verkaufe mein Fahrrad ..."})
        self.assertEqual(status, 200)
        result = json.loads(raw)
        self.assertEqual(result["scenario"]["buyer"]["limit_cents"], 42050)
        self.assertEqual(result["scenario"]["seller"]["limit_cents"], 38000)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["assumptions"], draft["assumptions"])
        self.assertEqual(client.calls[0]["output_config"]["effort"], "low")

    def test_inconsistent_draft_is_returned_with_errors(self):
        from test_negotiation import envelope
        draft = {"product": "Rad", "buyer_target_eur": 500, "buyer_limit_eur": 400,
                 "seller_target_eur": 480, "seller_limit_eur": 380, "max_turns": 12, "assumptions": []}
        with patch("negotiation.strategies.create_client", return_value=FakeClient(envelope(text=json.dumps(draft)))):
            status, raw = self.request("POST", "/api/parse", {"text": "Rad"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(raw)["errors"])

    def test_claude_run_with_mocked_client(self):
        from test_negotiation import envelope

        def decision(action, price):
            return envelope({"action": action, "price_cents": price, "message": "Test", "rationale": "Privat"})

        client = FakeClient(decision("offer", 11000), decision("accept", 11000))
        with patch("negotiation.strategies.create_client", return_value=client):
            status, raw = self.request("POST", "/api/runs", {"scenario": scenario(), "buyer": "claude", "seller": "claude"})
        self.assertEqual(status, 201)
        events = self.events(json.loads(raw)["run_id"])
        self.assertEqual(events[-1]["result"]["price_cents"], 11000)
        self.assertNotIn("LOCAL_TEST_SECRET", json.dumps(events))


if __name__ == "__main__":
    unittest.main()
