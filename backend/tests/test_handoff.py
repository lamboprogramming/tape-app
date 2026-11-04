import http.client
import json
import random
import threading
import unittest
from datetime import datetime, timezone

from tape_app.alerts import evaluate_alerts, required_symbols, validate_rule
from tape_app.cache import CachedProvider
from tape_app.providers.mock import MockProvider
from tape_app.quote_metrics import enrich
from tape_app.server import create_server
from tape_app.sizing import size_scenarios
from tape_app.strategies import build_iron_condor, build_naked, iron_condor_payoff


class RangeMetricTests(unittest.TestCase):
    def test_range_position_is_per_contract_day_range(self):
        q = enrich({"symbol": "X", "last": 1.5, "low": 1.0, "high": 2.0})
        self.assertEqual(q["range_pct"], 50.0)
        self.assertEqual(q["range_dollars"], 1.0)

    def test_zero_is_day_low_and_hundred_is_day_high(self):
        self.assertEqual(enrich({"last": 1.0, "low": 1.0, "high": 2.0})["range_pct"], 0.0)
        self.assertEqual(enrich({"last": 2.0, "low": 1.0, "high": 2.0})["range_pct"], 100.0)

    def test_flags(self):
        near_high = enrich({"last": 1.95, "low": 1.0, "high": 2.0})
        self.assertIn("near_high", near_high["flags"])
        near_low = enrich({"last": 1.05, "low": 1.0, "high": 2.0})
        self.assertIn("near_low", near_low["flags"])
        pullback = enrich({"last": 1.8, "low": 1.0, "high": 2.0})
        self.assertIn("pullback_from_high", pullback["flags"])

    def test_no_range_when_high_equals_low_or_missing(self):
        self.assertIsNone(enrich({"last": 1.0, "low": 1.0, "high": 1.0})["range_pct"])
        self.assertIsNone(enrich({"last": 1.0})["range_pct"])


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.now = [0.0]
        self.inner = MockProvider(rng=random.Random(2), clock=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc))
        self.cache = CachedProvider(self.inner, quote_ttl=30, clock=lambda: self.now[0])

    def test_repeat_reads_within_window_hit_upstream_once(self):
        self.cache.get_quotes(["SPY"])
        self.cache.get_quotes(["SPY"])
        self.assertEqual(self.cache.upstream_calls, 1)

    def test_snapshot_refreshes_after_ttl(self):
        first = self.cache.get_quotes(["SPY"])[0]
        self.now[0] = 31
        second = self.cache.get_quotes(["SPY"])[0]
        self.assertEqual(self.cache.upstream_calls, 2)
        self.assertIsNot(first, second)

    def test_only_missing_symbols_are_fetched(self):
        self.cache.get_quotes(["SPY"])
        self.cache.get_quotes(["SPY", "QQQ"])
        self.assertEqual(self.cache.upstream_calls, 2)

    def test_expirations_cached_longer(self):
        self.cache.get_expirations("SPY")
        self.now[0] = 600
        self.cache.get_expirations("SPY")
        self.assertEqual(self.cache.upstream_calls, 1)


class CombinedAlertTests(unittest.TestCase):
    RULE = {
        "id": "combo",
        "symbol": "NVDA",
        "conditions": [
            {"field": "change_pct", "op": "<=", "value": -1},
            {"symbol": "NVDA_CALL", "field": "change_pct", "op": "<=", "value": -40},
        ],
    }

    def test_fires_only_when_all_conditions_hold(self):
        quotes = [{"symbol": "NVDA", "change_pct": -1.5}, {"symbol": "NVDA_CALL", "change_pct": -45}]
        self.assertEqual([r["id"] for r in evaluate_alerts([self.RULE], quotes)], ["combo"])

    def test_one_failing_condition_blocks_the_rule(self):
        quotes = [{"symbol": "NVDA", "change_pct": -1.5}, {"symbol": "NVDA_CALL", "change_pct": -10}]
        self.assertEqual(evaluate_alerts([self.RULE], quotes), [])

    def test_required_symbols_include_condition_symbols(self):
        self.assertEqual(required_symbols([self.RULE]), ["NVDA", "NVDA_CALL"])

    def test_validation(self):
        self.assertEqual(validate_rule(self.RULE), [])
        self.assertTrue(validate_rule({"id": "x", "conditions": []}))
        self.assertTrue(validate_rule({"id": "x", "conditions": [{"field": "nope", "op": ">", "value": 1}]}))

    def test_range_fields_are_valid_alert_fields(self):
        rule = {"id": "r", "symbol": "SPY", "field": "range_pct", "op": "<=", "value": 20}
        self.assertEqual(validate_rule(rule), [])
        quotes = [enrich({"symbol": "SPY", "last": 1.0, "low": 1.0, "high": 2.0})]
        self.assertEqual(len(evaluate_alerts([rule], quotes)), 1)


class SizingScenarioTests(unittest.TestCase):
    def test_default_portfolio_table(self):
        rows = size_scenarios(premium=1.0, risk_pct=10)
        self.assertEqual([r["portfolio"] for r in rows], [500, 800, 1000, 1500, 2000])
        self.assertEqual(rows[0]["contracts"], 0)  # $50 budget, $100 contract
        self.assertEqual(rows[4]["contracts"], 2)  # $200 budget, $100 contract


class StrategyTests(unittest.TestCase):
    def test_iron_condor_credit_and_risk(self):
        ic = build_iron_condor(90, 95, 105, 110, 0.2, 0.6, 0.6, 0.2)
        self.assertEqual(ic["max_profit"], 80.0)
        self.assertEqual(ic["max_loss"], 420.0)
        self.assertEqual(ic["breakevens"], [94.2, 105.8])

    def test_iron_condor_payoff_points(self):
        ic = build_iron_condor(90, 95, 105, 110, 0.2, 0.6, 0.6, 0.2)
        self.assertEqual(iron_condor_payoff(ic, 100), 80.0)  # between the short strikes: keep the credit
        self.assertEqual(iron_condor_payoff(ic, 120), -420.0)  # above the long call: max loss

    def test_iron_condor_rejects_bad_order(self):
        with self.assertRaises(ValueError):
            build_iron_condor(95, 90, 105, 110, 0.2, 0.6, 0.6, 0.2)

    def test_naked_short_call_is_unlimited_with_warning(self):
        n = build_naked("call", "short", 100, 2)
        self.assertIsNone(n["max_loss"])
        self.assertTrue(n["warnings"])

    def test_naked_long_put_loses_only_premium(self):
        self.assertEqual(build_naked("put", "long", 100, 2)["max_loss"], 200.0)

    def test_naked_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            build_naked("straddle", "long", 100, 2)


class NewRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = create_server(MockProvider(rng=random.Random(5)), port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        payload = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=payload, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read() or b"{}")
        conn.close()
        return resp.status, data

    def test_quotes_include_range_fields(self):
        status, body = self.request("GET", "/api/quotes?symbols=SPY")
        self.assertEqual(status, 200)
        self.assertIn("range_pct", body["quotes"][0])
        self.assertIn("flags", body["quotes"][0])

    def test_scenario_route(self):
        status, body = self.request("POST", "/api/size/scenarios", {"premium": 1, "risk_pct": 10})
        self.assertEqual(status, 200)
        self.assertEqual(len(body["scenarios"]), 5)

    def test_iron_condor_route(self):
        status, body = self.request("POST", "/api/strategy/iron-condor", {
            "put_long": 90, "put_short": 95, "call_short": 105, "call_long": 110,
            "put_long_premium": 0.2, "put_short_premium": 0.6, "call_short_premium": 0.6, "call_long_premium": 0.2,
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["max_profit"], 80.0)

    def test_naked_route_warns_on_short_call(self):
        status, body = self.request("POST", "/api/strategy/naked", {"kind": "call", "side": "short", "strike": 100, "premium": 2})
        self.assertEqual(status, 200)
        self.assertIsNone(body["max_loss"])

    def test_naked_route_rejects_bad_kind(self):
        status, _ = self.request("POST", "/api/strategy/naked", {"kind": "x", "side": "long", "strike": 1, "premium": 1})
        self.assertEqual(status, 400)

    def test_combined_alert_route(self):
        status, body = self.request("POST", "/api/alerts/evaluate", {"rules": [{
            "id": "c", "symbol": "SPY",
            "conditions": [{"field": "last", "op": ">", "value": 0}, {"field": "range_pct", "op": ">=", "value": 0}],
        }]})
        self.assertEqual(status, 200)
        self.assertEqual([r["id"] for r in body["fired"]], ["c"])


if __name__ == "__main__":
    unittest.main()
