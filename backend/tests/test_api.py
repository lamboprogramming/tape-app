import io
import json
import threading
import unittest
import urllib.error
import urllib.request

from tape_app.providers.mock import MockProvider
from tape_app.server import create_server, parse_symbols, stream_tape
import random


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = create_server(MockProvider(rng=random.Random(1)), port=0)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def post(self, path, body):
        data = json.dumps(body).encode("utf-8") if not isinstance(body, bytes) else body
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def test_health(self):
        self.assertEqual(self.get("/api/health"), (200, {"ok": True, "provider": "mock"}))

    def test_quotes_requires_symbols(self):
        status, _ = self.get("/api/quotes")
        self.assertEqual(status, 400)

    def test_quotes_returns_requested_symbols(self):
        status, body = self.get("/api/quotes?symbols=aapl,msft")
        self.assertEqual(status, 200)
        self.assertEqual([q["symbol"] for q in body["quotes"]], ["AAPL", "MSFT"])

    def test_chain_rejects_bad_expiration(self):
        status, _ = self.get("/api/options/AAPL/chain?expiration=next-friday")
        self.assertEqual(status, 400)

    def test_chain_returns_contracts(self):
        status, body = self.get("/api/options/AAPL/chain?expiration=2030-01-18")
        self.assertEqual(status, 200)
        self.assertTrue(any(c["type"] == "put" for c in body["contracts"]))

    def test_screener_returns_sorted_limited_results(self):
        status, body = self.post("/api/screener", {
            "universe": ["AAPL", "MSFT", "NVDA"],
            "filters": [{"field": "volume", "op": ">=", "value": 0}],
            "sort": {"field": "last", "direction": "desc"},
            "limit": 2,
        })
        self.assertEqual(status, 200)
        self.assertEqual(len(body["results"]), 2)
        self.assertGreaterEqual(body["results"][0]["last"], body["results"][1]["last"])

    def test_screener_reports_invalid_screen(self):
        status, body = self.post("/api/screener", {"universe": ["AAPL"], "filters": [{"field": "bogus", "op": ">", "value": 1}]})
        self.assertEqual(status, 400)
        self.assertIn("unknown field", body["details"][0])

    def test_screener_rejects_malformed_json(self):
        status, _ = self.post("/api/screener", b"{not json")
        self.assertEqual(status, 400)

    def test_unknown_route_is_404(self):
        status, _ = self.get("/api/nope")
        self.assertEqual(status, 404)

    def test_chain_summary_has_range_position(self):
        status, body = self.get("/api/options/AAPL/summary?expiration=2030-01-18")
        self.assertEqual(status, 200)
        self.assertIsNotNone(body["spot"])
        self.assertTrue(0 <= body["range_position"] <= 1)
        self.assertTrue(all("distance_pct" in row for row in body["strikes"]))

    def test_size_shares(self):
        status, body = self.post("/api/size", {"mode": "shares", "account": 10000, "risk_pct": 1, "entry": 50, "stop": 48})
        self.assertEqual(status, 200)
        self.assertEqual(body["shares"], 50)

    def test_size_rejects_unknown_mode(self):
        status, _ = self.post("/api/size", {"mode": "futures"})
        self.assertEqual(status, 400)

    def test_size_missing_field_is_400(self):
        status, body = self.post("/api/size", {"mode": "shares", "account": 10000})
        self.assertEqual(status, 400)
        self.assertIn("risk_pct", body["error"])

    def test_vertical_returns_curve_when_requested(self):
        status, body = self.post("/api/strategy/vertical", {
            "strategy": "bull_call", "long_strike": 100, "short_strike": 110,
            "long_premium": 4, "short_premium": 1.5, "curve": {"low": 90, "high": 120, "steps": 4},
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["max_profit"], 750.0)
        self.assertEqual(len(body["curve"]), 4)

    def test_vertical_bad_structure_is_400(self):
        status, body = self.post("/api/strategy/vertical", {
            "strategy": "bull_call", "long_strike": 110, "short_strike": 100,
            "long_premium": 4, "short_premium": 1.5,
        })
        self.assertEqual(status, 400)
        self.assertIn("strike", body["error"])

    def test_alerts_evaluate_fires_on_mock_quotes(self):
        status, body = self.post("/api/alerts/evaluate", {"rules": [
            {"id": "always", "symbol": "AAPL", "field": "last", "op": ">", "value": 0},
            {"id": "never", "symbol": "AAPL", "field": "last", "op": "<", "value": 0},
        ]})
        self.assertEqual(status, 200)
        self.assertEqual([r["id"] for r in body["fired"]], ["always"])

    def test_alerts_reject_invalid_rule(self):
        status, body = self.post("/api/alerts/evaluate", {"rules": [{"id": "x", "field": "nope"}]})
        self.assertEqual(status, 400)
        self.assertTrue(body["details"])


class ParseSymbolsTests(unittest.TestCase):
    def test_normalizes_and_dedupes(self):
        self.assertEqual(parse_symbols(" aapl, MSFT,aapl ,"), ["AAPL", "MSFT"])

    def test_accepts_list_input(self):
        self.assertEqual(parse_symbols(["spy", "qqq"]), ["SPY", "QQQ"])

    def test_caps_symbol_count(self):
        self.assertEqual(len(parse_symbols([f"S{i}" for i in range(300)])), 100)


class TapeTests(unittest.TestCase):
    def test_pushes_only_changed_quotes(self):
        provider = MockProvider(rng=random.Random(3))
        sent = []
        sequence = iter([
            [{"symbol": "AAPL", "last": 1.0}],
            [{"symbol": "AAPL", "last": 1.0}],  # unchanged: no event
            [{"symbol": "AAPL", "last": 1.5}],  # changed: event
        ])
        provider.get_quotes = lambda symbols: next(sequence)

        polls = stream_tape(provider, ["AAPL"], 0, sent.append, sleep=lambda s: None, max_polls=3)

        self.assertEqual(polls, 3)
        self.assertEqual(len(sent), 2)
        self.assertIn('"last": 1.5', sent[1])
        self.assertTrue(sent[0].startswith("event: quotes\ndata: "))

    def test_stops_when_client_disconnects(self):
        provider = MockProvider(rng=random.Random(3))

        def broken(_text):
            raise BrokenPipeError()

        polls = stream_tape(provider, ["AAPL"], 0, broken, sleep=lambda s: None, max_polls=10)
        self.assertEqual(polls, 1)


if __name__ == "__main__":
    unittest.main()
