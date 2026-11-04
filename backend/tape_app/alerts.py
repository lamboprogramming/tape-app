"""Price alert rules evaluated against quotes.

A rule:
    {"id": "a1", "symbol": "AAPL", "field": "last", "op": ">=", "value": 240}

Evaluation is stateless. `evaluate_alerts` reports which rules fire on the
current quotes. To avoid repeat notifications, the caller passes the set of
rule ids already fired and gets back only the new ones.
"""

import operator

FIELDS = ("last", "change", "change_pct", "volume", "bid", "ask")
OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}


def validate_rule(rule: dict) -> list[str]:
    errors = []
    if not rule.get("id"):
        errors.append("rule needs an id")
    if not isinstance(rule.get("symbol"), str) or not rule.get("symbol"):
        errors.append("rule needs a symbol")
    if rule.get("field") not in FIELDS:
        errors.append(f"unknown field {rule.get('field')!r}")
    if rule.get("op") not in OPS:
        errors.append(f"unknown op {rule.get('op')!r}")
    value = rule.get("value")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        errors.append("value must be a number")
    return errors


def evaluate_alerts(rules: list[dict], quotes: list[dict], already_fired: set[str] | None = None) -> list[dict]:
    """Return rules that fire on these quotes and were not already fired."""
    already_fired = already_fired or set()
    by_symbol = {q["symbol"]: q for q in quotes}
    fired = []
    for rule in rules:
        if rule.get("id") in already_fired:
            continue
        quote = by_symbol.get(rule.get("symbol"))
        if quote is None:
            continue
        actual = quote.get(rule["field"])
        if actual is not None and OPS[rule["op"]](actual, rule["value"]):
            fired.append({**rule, "actual": actual})
    return fired
