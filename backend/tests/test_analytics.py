import unittest

from tape_app.alerts import evaluate_alerts, validate_rule
from tape_app.options import range_position, strike_distance_pct, summarize_chain
from tape_app.sizing import size_option_contracts, size_shares
from tape_app.strategies import build_vertical, payoff_at_expiry, payoff_curve


class RangePositionTests(unittest.TestCase):
    def test_spot_at_ends_and_middle(self):
        strikes = [100, 110, 120]
        self.assertEqual(range_position(100, strikes), 0.0)
        self.assertEqual(range_position(120, strikes), 1.0)
        self.assertEqual(range_position(110, strikes), 0.5)

    def test_clamps_outside_the_ladder(self):
        self.assertEqual(range_position(95, [100, 120]), 0.0)
        self.assertEqual(range_position(130, [100, 120]), 1.0)

    def test_single_strike_is_middle(self):
        self.assertEqual(range_position(100, [100]), 0.5)

    def test_missing_inputs_return_none(self):
        self.assertIsNone(range_position(None, [100]))
        self.assertIsNone(range_position(100, []))

    def test_distance_pct(self):
        self.assertEqual(strike_distance_pct(100, 105), 5.0)
        self.assertEqual(strike_distance_pct(100, 95), -5.0)

    def test_summarize_chain_groups_by_strike(self):
        contracts = [
            {"strike": 100, "type": "call", "bid": 2, "ask": 2.2, "volume": 5, "open_interest": 9},
            {"strike": 100, "type": "put", "bid": 1, "ask": 1.2, "volume": 3, "open_interest": 4},
            {"strike": 110, "type": "call", "bid": 0.5, "ask": 0.7, "volume": 1, "open_interest": 2},
        ]
        summary = summarize_chain(100, contracts)
        self.assertEqual([row["strike"] for row in summary["strikes"]], [100, 110])
        self.assertEqual(summary["strikes"][0]["call"]["mid"], 2.1)
        self.assertIsNone(summary["strikes"][1]["put"])
        self.assertEqual(summary["range_position"], 0.0)


class SizingTests(unittest.TestCase):
    def test_shares_round_down_to_risk_budget(self):
        result = size_shares(account=10_000, risk_pct=1, entry=50, stop=48)
        self.assertEqual(result["shares"], 50)  # $100 budget / $2 per share
        self.assertEqual(result["risk_at_size"], 100.0)

    def test_works_for_short_entries(self):
        result = size_shares(account=10_000, risk_pct=1, entry=50, stop=52)
        self.assertEqual(result["shares"], 50)

    def test_contracts_for_long_premium(self):
        # $300 budget, $300 per contract -> exactly 1 contract.
        result = size_option_contracts(account=10_000, risk_pct=3, premium=3.0)
        self.assertEqual(result["contracts"], 1)
        self.assertEqual(result["max_loss"], 300.0)
        # $200 budget cannot buy one $300 contract: rounds down to zero.
        self.assertEqual(size_option_contracts(account=10_000, risk_pct=2, premium=3.0)["contracts"], 0)

    def test_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            size_shares(account=0, risk_pct=1, entry=50, stop=48)
        with self.assertRaises(ValueError):
            size_shares(account=1000, risk_pct=1, entry=50, stop=50)
        with self.assertRaises(ValueError):
            size_shares(account=1000, risk_pct=0, entry=50, stop=48)


class StrategyTests(unittest.TestCase):
    def test_bull_call_debit(self):
        pos = build_vertical("bull_call", long_strike=100, short_strike=110, long_premium=4.0, short_premium=1.5)
        self.assertEqual(pos["net_type"], "debit")
        self.assertEqual(pos["net_premium"], 2.5)
        self.assertEqual(pos["max_loss"], 250.0)
        self.assertEqual(pos["max_profit"], 750.0)  # (10 - 2.5) * 100
        self.assertEqual(pos["breakeven"], 102.5)

    def test_bear_put_debit(self):
        pos = build_vertical("bear_put", long_strike=110, short_strike=100, long_premium=4.0, short_premium=1.5)
        self.assertEqual(pos["breakeven"], 107.5)
        self.assertEqual(pos["max_profit"], 750.0)

    def test_bull_put_credit(self):
        pos = build_vertical("bull_put", long_strike=90, short_strike=100, long_premium=0.8, short_premium=2.3)
        self.assertEqual(pos["net_type"], "credit")
        self.assertEqual(pos["max_profit"], 150.0)  # credit received
        self.assertEqual(pos["max_loss"], 850.0)  # (10 - 1.5) * 100
        self.assertEqual(pos["breakeven"], 98.5)

    def test_bear_call_credit(self):
        pos = build_vertical("bear_call", long_strike=110, short_strike=100, long_premium=0.8, short_premium=2.3)
        self.assertEqual(pos["breakeven"], 101.5)

    def test_payoff_at_expiry_bull_call(self):
        pos = build_vertical("bull_call", long_strike=100, short_strike=110, long_premium=4.0, short_premium=1.5)
        self.assertEqual(payoff_at_expiry(pos, 90), -250.0)  # both expire worthless
        self.assertEqual(payoff_at_expiry(pos, 102.5), 0.0)  # breakeven
        self.assertEqual(payoff_at_expiry(pos, 150), 750.0)  # max profit

    def test_payoff_at_expiry_bull_put_credit(self):
        pos = build_vertical("bull_put", long_strike=90, short_strike=100, long_premium=0.8, short_premium=2.3)
        self.assertEqual(payoff_at_expiry(pos, 120), 150.0)  # keeps the full credit
        self.assertEqual(payoff_at_expiry(pos, 50), -850.0)  # max loss

    def test_curve_covers_the_grid(self):
        pos = build_vertical("bull_call", long_strike=100, short_strike=110, long_premium=4.0, short_premium=1.5)
        curve = payoff_curve(pos, 80, 120, steps=5)
        self.assertEqual([p["underlying"] for p in curve], [80, 90, 100, 110, 120])

    def test_rejects_wrong_strike_order(self):
        with self.assertRaises(ValueError):
            build_vertical("bull_call", long_strike=110, short_strike=100, long_premium=4, short_premium=1)

    def test_rejects_wrong_net_direction(self):
        with self.assertRaises(ValueError):
            build_vertical("bull_call", long_strike=100, short_strike=110, long_premium=1, short_premium=3)
        with self.assertRaises(ValueError):
            build_vertical("bull_put", long_strike=90, short_strike=100, long_premium=3, short_premium=1)

    def test_rejects_unknown_strategy(self):
        with self.assertRaises(ValueError):
            build_vertical("iron_condor", 1, 2, 1, 0)


class AlertTests(unittest.TestCase):
    RULES = [
        {"id": "a1", "symbol": "AAPL", "field": "last", "op": ">=", "value": 240},
        {"id": "a2", "symbol": "MSFT", "field": "change_pct", "op": "<", "value": -2},
        {"id": "a3", "symbol": "NVDA", "field": "last", "op": ">", "value": 1000},
    ]
    QUOTES = [
        {"symbol": "AAPL", "last": 241, "change_pct": 1.0},
        {"symbol": "MSFT", "last": 400, "change_pct": -2.5},
    ]

    def test_fires_matching_rules_only(self):
        fired = evaluate_alerts(self.RULES, self.QUOTES)
        self.assertEqual([r["id"] for r in fired], ["a1", "a2"])
        self.assertEqual(fired[0]["actual"], 241)

    def test_skips_already_fired_rules(self):
        fired = evaluate_alerts(self.RULES, self.QUOTES, already_fired={"a1"})
        self.assertEqual([r["id"] for r in fired], ["a2"])

    def test_missing_quote_does_not_fire(self):
        self.assertEqual(evaluate_alerts(self.RULES, []), [])

    def test_validate_rule(self):
        self.assertEqual(validate_rule(self.RULES[0]), [])
        self.assertTrue(validate_rule({"id": "x", "symbol": "A", "field": "nope", "op": "=", "value": "high"}))


if __name__ == "__main__":
    unittest.main()
