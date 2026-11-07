"""Prueft echte Programmstarts und den kompletten LLM-Ablauf mit simuliertem API-Client."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from negotiation.cli import main
from test_negotiation import FakeClient, anthropic, envelope

ROOT = Path(__file__).resolve().parent.parent


class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.logs = Path(self.tmp.name) / "runs"

    def launch(self, *args):
        environment = dict(os.environ, ANTHROPIC_API_KEY="", NEGOTIATION_MODEL="", PYTHONUTF8="1")
        return subprocess.run(
            [sys.executable, str(ROOT / "main.py"), "--log-dir", str(self.logs), *args],
            cwd=self.tmp.name, env=environment, capture_output=True, text=True,
            encoding="utf-8", timeout=15)

    def test_default_demo_starts_from_another_directory(self):
        completed = self.launch("--quiet")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("OFFLINE-DEMO (kein LLM)", completed.stdout)
        self.assertIn("EINIGUNG: 112,00 EUR", completed.stdout)
        self.assertEqual(len(list(self.logs.glob("*/result.json"))), 1)

    def test_no_deal_is_regular_outcome(self):
        completed = self.launch("--quiet", "--config", str(ROOT / "config/no_deal.json"))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("ABBRUCH", completed.stdout)
        self.assertIn("max_turns", completed.stdout)

    def test_missing_key_is_explained_without_starting_negotiation(self):
        completed = self.launch("--mode", "claude")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("ANTHROPIC_API_KEY fehlt", completed.stderr)
        self.assertFalse(self.logs.exists())

    @unittest.skipUnless(anthropic, "Anthropic SDK nicht installiert")
    def test_two_llm_agents_end_to_end_with_mocked_client(self):
        def response(action, price):
            decision = {"action": action, "price_cents": price,
                        "message": "Testnachricht", "rationale": "Private Testbegruendung"}
            return envelope(decision)

        client = FakeClient(response("offer", 11000), response("accept", 11000))
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "LOCAL_TEST_SECRET"}), \
                patch("negotiation.strategies.create_client", return_value=client) as factory, \
                redirect_stdout(io.StringIO()):
            code = main(["--mode", "claude", "--model", "test-model",
                         "--log-dir", str(self.logs)])
        self.assertEqual(code, 0)
        self.assertEqual(factory.call_args.args[0], "LOCAL_TEST_SECRET")
        self.assertEqual(len(client.calls), 2)
        observations = [json.loads(call["messages"][0]["content"]) for call in client.calls]
        self.assertEqual(observations[0]["self_profile"]["role"], "buyer")
        self.assertEqual(observations[1]["self_profile"]["role"], "seller")
        self.assertNotIn("rationale", observations[1]["history"][0])
        run = next(self.logs.iterdir())
        result = json.loads((run / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "agreement")
        self.assertEqual(result["price_cents"], 11000)
        self.assertNotIn("LOCAL_TEST_SECRET", (run / "events.jsonl").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
