"""Runs the whole pipeline against a local fake of the gateway; no network, no key."""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vet.gateway import BudgetExceeded, Gateway  # noqa: E402
from vet.mcp_leads import handle_rpc, lead_history  # noqa: E402
from vet.pipeline import Pipeline, cash_check, check_quotes, parse_json  # noqa: E402

PAGE = ("Prizes: first place 1,000 USD and second place 200 USD. Open to anyone aged 18 or over worldwide. "
        "Submit by Nov 11 2026. " * 20)
FACTS = {"title": "Demo Hack", "sponsor": "Demo", "deadline_iso": "2026-11-11T23:59:00+00:00",
         "prizes": [{"label": "1st", "amount": 1000, "currency": "USD", "kind": "cash", "conditional": False},
                    {"label": "2nd", "amount": 200, "currency": "USD", "kind": "cash", "conditional": False},
                    {"label": "Grant", "amount": 5000, "currency": "USD", "kind": "grant", "conditional": True}]}
COMPUTED = {"real_cash_usd": 1200, "other_value_usd": 5000, "deadline_local": "2026-11-12 10:29", "hours_left": 80.0}
FOUND = {"sponsor_real": True, "paid_before": None, "complaints": [], "seen_before": None, "sources": [], "notes": ""}
JUDGED = {"verdict": "GO", "reason": "Real cash and open to him.", "doubts": [], "effort_hours": 12,
          "evidence": ["first place 1,000 USD", "this sentence is not on the page"]}


class Fake:
    """Scripted gateway. Set .deny to a stage's model substring and an error body to refuse that stage."""

    def __init__(self):
        self.requests, self.deny = [], None
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append((self.path, body, dict(self.headers)))
                code, out, headers = fake.answer(self.path, body)
                data = json.dumps(out).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}/api/v1"

    def answer(self, path, body):
        if path.endswith("/fetch"):
            return 200, {"object": "fetch", "text": PAGE, "url": body["url"]}, {}
        if self.deny and self.deny[0](body):
            return self.deny[1], self.deny[2], {}
        if body.get("guardrails"):
            content, calls = FACTS, []
        elif any(t.get("type") == "otari_code_execution" for t in body.get("tools") or []):
            content, calls = COMPUTED, [{"type": "code_execution", "code": "print(1)", "exit_code": 0}]
        elif any(t.get("type") == "otari_web_search" for t in body.get("tools") or []):
            content, calls = FOUND, [{"type": "web_search"}, {"type": "mcp", "tool": "lead_history"}]
        else:
            content, calls = JUDGED, []
        return (200, {"model": body["model"], "otari_tool_calls": calls,
                      "choices": [{"message": {"content": "```json\n" + json.dumps(content) + "\n```"}}]},
                {"Otari-Model": body["model"], "Otari-Cost-USD": "0.001", "Otari-Fallback": "false"})


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.fake = Fake()
        self.addCleanup(self.fake.server.shutdown)
        self.gw = Gateway(key="otk_test", base=self.fake.base, user="alice", end_user_budget="b1")
        self.p = Pipeline(self.gw, mcp_url="https://mcp.example/x", mcp_token="t", log=lambda *a: None)

    def chats(self):
        return [b for p, b, _ in self.fake.requests if p.endswith("/chat/completions")]

    def test_full_run_uses_every_feature(self):
        r = self.p.run("https://example.com/rules")
        self.assertEqual(r["verdict"], "GO")
        self.assertEqual(r["computed"]["real_cash_usd"], 1200)
        self.assertTrue(r["computed"]["ran_in_sandbox"])
        self.assertAlmostEqual(r["cost_usd"], 0.004)
        chats = self.chats()
        self.assertEqual(chats[0]["guardrails"][0]["profile"], "prompt-injection")
        self.assertEqual(chats[2]["mcp_servers"][0]["allowed_tools"], ["lead_history"])
        self.assertEqual(chats[2]["mcp_servers"][0]["authorization_token"], "t")
        self.assertGreaterEqual(len({c["model"] for c in chats}), 3)  # distinct roles, distinct models
        self.assertTrue(all(c["user"] == "alice" for c in chats))
        self.assertEqual(self.fake.requests[0][2].get("Otari-End-User-Budget"), "b1")

    def test_invented_quote_is_dropped(self):
        r = self.p.run("https://example.com/rules")
        self.assertEqual(r["evidence"], ["first place 1,000 USD"])
        self.assertEqual(r["unverified_quotes"], ["this sentence is not on the page"])

    def test_wrong_sandbox_sum_is_replaced(self):
        COMPUTED["real_cash_usd"] = 6200
        self.addCleanup(COMPUTED.__setitem__, "real_cash_usd", 1200)
        r = self.p.run("https://example.com/rules")
        self.assertEqual(r["computed"]["real_cash_usd"], 1200)
        self.assertTrue(r["computed"]["sum_mismatch"])

    def test_guardrail_block_stops_before_any_model_reads_it(self):
        self.fake.deny = (lambda b: b.get("guardrails"), 403, {"error": {"code": "guardrail_violation", "message": "flagged"}})
        r = self.p.run("https://example.com/rules")
        self.assertEqual((r["verdict"], r["stopped"]), ("NO", "guardrail"))
        self.assertEqual(len(self.chats()), 1)  # no compute, investigate or judge

    def test_budget_stop_is_a_result_not_a_crash(self):
        self.fake.deny = (lambda b: "tools" in b, 403, {"error": {"code": "budget_exceeded", "message": "over"}})
        r = self.p.run("https://example.com/rules")
        self.assertEqual(r["stopped"], "budget")
        self.assertIsNone(r["verdict"])

    def test_budget_error_shape_with_detail_object(self):
        self.fake.deny = (lambda b: True, 403, {"detail": {"code": "budget_exceeded", "message": "over"}})
        with self.assertRaises(BudgetExceeded):
            self.gw.chat("m", [{"role": "user", "content": "x"}])


class UnitTest(unittest.TestCase):
    def test_cash_check_counts_only_unconditional_real_money(self):
        fx = {"USD": 1, "EUR": 2}
        self.assertEqual(cash_check([
            {"amount": 100, "currency": "USD", "kind": "cash", "conditional": False},
            {"amount": 100, "currency": "EUR", "kind": "stablecoin", "conditional": False},
            {"amount": 999, "currency": "USD", "kind": "project_token", "conditional": False},
            {"amount": 999, "currency": "USD", "kind": "cash", "conditional": True},
        ], fx), 300)

    def test_parse_json_with_prose(self):
        self.assertEqual(parse_json('Here you go:\n{"a": 1}\nThanks'), {"a": 1})

    def test_check_quotes_ignores_punctuation_and_case(self):
        ok, bad = check_quotes(["FIRST place: $1,000"], "first place $1,000 for the winner")
        self.assertEqual((ok, bad), (["FIRST place: $1,000"], []))

    def test_mcp_protocol(self):
        init = handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-03-26")
        self.assertIsNone(handle_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(handle_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"][0]["name"], "lead_history")
        self.assertIn("error", handle_rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nope"}}))

    def test_lead_history_empty_query(self):
        self.assertEqual(lead_history("")["matches"], [])


if __name__ == "__main__":
    unittest.main()
