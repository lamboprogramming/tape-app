import io
import json
import random
import unittest
from datetime import datetime, timezone

from tape_app.providers import create_provider
from tape_app.providers.mock import MockProvider
from tape_app.providers.tradier import TradierProvider, map_quote


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_opener(payload):
    calls = []

    def opener(req, timeout=None):
        calls.append(req)
        return FakeResponse(json.dumps(payload).encode("utf-8"))

    opener.calls = calls
    return opener


class TradierTests(unittest.TestCase):
    def test_single_quote_object_is_wrapped_in_a_list(self):
        opener = fake_opener({"quotes": {"quote": {"symbol": "SPY", "last": "580.5", "change_percentage": "0.21"}}})
        quotes = TradierProvider("tok", opener=opener).get_quotes(["SPY"])
        self.assertEqual([q["symbol"] for q in quotes], ["SPY"])
        self.assertEqual(quotes[0]["change_pct"], 0.21)

    def test_quotes_send_bearer_token_and_symbols(self):
        opener = fake_opener({"quotes": {"quote": []}})
        TradierProvider("tok", opener=opener).get_quotes(["AAPL", "MSFT"])
        req = opener.calls[0]
        self.assertEqual(req.get_header("Authorization"), "Bearer tok")
        self.assertIn("symbols=AAPL%2CMSFT", req.full_url)

    def test_null_option_chain_is_empty(self):
        provider = TradierProvider("tok", opener=fake_opener({"options": None}))
        self.assertEqual(provider.get_option_chain("XYZ", "2030-01-18"), [])

    def test_option_chain_maps_fields(self):
        raw = {"option": {"symbol": "AAPL300118C00200000", "option_type": "call", "strike": 200,
                          "expiration_date": "2030-01-18", "bid": 1.2, "ask": 1.4, "open_interest": 55}}
        contracts = TradierProvider("tok", opener=fake_opener({"options": raw})).get_option_chain("AAPL", "2030-01-18")
        self.assertEqual(contracts[0]["type"], "call")
        self.assertEqual(contracts[0]["open_interest"], 55.0)
        self.assertEqual(contracts[0]["underlying"], "AAPL")

    def test_missing_token_is_rejected(self):
        with self.assertRaises(ValueError):
            TradierProvider("")

    def test_map_quote_tolerates_missing_fields(self):
        q = map_quote({"symbol": "X"})
        self.assertIsNone(q["last"])


class MockTests(unittest.TestCase):
    def setUp(self):
        self.clock = lambda: datetime(2030, 1, 1, tzinfo=timezone.utc)
        self.provider = MockProvider(rng=random.Random(42), clock=self.clock)

    def test_quotes_have_canonical_shape(self):
        q = self.provider.get_quotes(["aapl".upper()])[0]
        self.assertEqual(set(q), {"symbol", "last", "change", "change_pct", "bid", "ask", "volume",
                                  "open", "high", "low", "prev_close", "time"})
        self.assertLessEqual(q["bid"], q["last"])
        self.assertLessEqual(q["last"], q["ask"])

    def test_expirations_are_future_iso_dates(self):
        expirations = self.provider.get_expirations("AAPL")
        self.assertEqual(expirations[0], "2030-01-01")  # today is a valid expiration, like "Today" in the UI
        self.assertEqual(expirations, sorted(expirations))

    def test_chain_has_calls_and_puts(self):
        contracts = self.provider.get_option_chain("AAPL", "2030-01-18")
        self.assertEqual({c["type"] for c in contracts}, {"call", "put"})
        self.assertEqual(len(contracts), 22)

    def test_seeded_runs_are_reproducible(self):
        a = MockProvider(rng=random.Random(7), clock=self.clock).get_quotes(["SPY"])
        b = MockProvider(rng=random.Random(7), clock=self.clock).get_quotes(["SPY"])
        self.assertEqual(a, b)


class FactoryTests(unittest.TestCase):
    def test_defaults_to_mock_without_token(self):
        self.assertEqual(create_provider({}).name, "mock")

    def test_uses_tradier_when_token_present(self):
        self.assertEqual(create_provider({"TRADIER_TOKEN": "t"}).name, "tradier")

    def test_unknown_provider_fails_loudly(self):
        with self.assertRaises(ValueError):
            create_provider({"DATA_PROVIDER": "bogus"})


if __name__ == "__main__":
    unittest.main()


class QuoteFieldTests(unittest.TestCase):
    def test_tradier_maps_open_high_low_prev_close(self):
        q = map_quote({"symbol": "SPY", "last": "1.5", "open": "1.0", "high": "2.0", "low": "0.9", "prevclose": "1.2"})
        self.assertEqual((q["open"], q["high"], q["low"], q["prev_close"]), (1.0, 2.0, 0.9, 1.2))

    def test_mock_high_and_low_bracket_last(self):
        provider = MockProvider(rng=random.Random(9), clock=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc))
        for _ in range(50):
            q = provider.get_quotes(["AAPL"])[0]
            self.assertLessEqual(q["low"], q["last"])
            self.assertGreaterEqual(q["high"], q["last"])

    def test_contract_quote_matches_chain_row(self):
        provider = MockProvider(rng=random.Random(4), clock=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc))
        contract = provider.get_option_chain("AAPL", "2030-01-18")[0]
        quote = provider.get_quotes([contract["symbol"]])[0]
        self.assertEqual(quote["symbol"], contract["symbol"])
        # Each call moves the simulated price a little, so allow a small tolerance.
        self.assertLessEqual(abs(quote["last"] - contract["last"]), contract["last"] * 0.03)
