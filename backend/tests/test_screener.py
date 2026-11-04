import unittest

from tape_app.screener import run_screen, validate_screen

QUOTES = [
    {"symbol": "AAA", "last": 10, "change": 1, "change_pct": 5, "bid": 9.99, "ask": 10.01, "volume": 2_000_000},
    {"symbol": "BBB", "last": 50, "change": -2, "change_pct": -3.8, "bid": 49.5, "ask": 50.5, "volume": 500_000},
    {"symbol": "CCC", "last": 120, "change": 4, "change_pct": 3.4, "bid": None, "ask": None, "volume": 9_000_000},
]


class RunScreenTests(unittest.TestCase):
    def symbols(self, screen):
        return [q["symbol"] for q in run_screen(QUOTES, screen)]

    def test_filters_are_and_ed(self):
        screen = {"filters": [
            {"field": "change_pct", "op": ">", "value": 0},
            {"field": "volume", "op": ">=", "value": 1_000_000},
        ]}
        self.assertEqual(self.symbols(screen), ["AAA", "CCC"])

    def test_between_is_inclusive(self):
        screen = {"filters": [{"field": "last", "op": "between", "value": [10, 50]}]}
        self.assertEqual(self.symbols(screen), ["AAA", "BBB"])

    def test_missing_value_never_matches(self):
        screen = {"filters": [{"field": "spread", "op": "<", "value": 1}]}
        self.assertEqual(self.symbols(screen), ["AAA"])

    def test_sort_desc_and_limit(self):
        screen = {"sort": {"field": "volume", "direction": "desc"}, "limit": 2}
        self.assertEqual(self.symbols(screen), ["CCC", "AAA"])

    def test_sort_puts_missing_values_last(self):
        screen = {"sort": {"field": "spread", "direction": "asc"}}
        self.assertEqual(self.symbols(screen), ["AAA", "BBB", "CCC"])


class ValidateScreenTests(unittest.TestCase):
    def test_reports_each_problem(self):
        errors = validate_screen({
            "filters": [
                {"field": "nope", "op": ">", "value": 1},
                {"field": "last", "op": "between", "value": [1]},
                {"field": "last", "op": ">", "value": "high"},
                {"field": "last", "op": "!", "value": 1},
            ],
            "limit": 0,
        })
        self.assertEqual(len(errors), 5)

    def test_accepts_a_good_screen(self):
        screen = {
            "filters": [{"field": "change_pct", "op": ">=", "value": 2}],
            "sort": {"field": "volume", "direction": "desc"},
            "limit": 25,
        }
        self.assertEqual(validate_screen(screen), [])

    def test_bool_is_not_a_number(self):
        self.assertTrue(validate_screen({"filters": [{"field": "last", "op": ">", "value": True}]}))


if __name__ == "__main__":
    unittest.main()
