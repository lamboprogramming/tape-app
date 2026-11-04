"""Price alert rules evaluated against quotes.

Single condition (still supported):
    {"id": "a1", "symbol": "AAPL", "field": "last", "op": ">=", "value": 240}

Combined conditions (all must hold):
    {"id": "a2", "symbol": "NVDA", "conditions": [
        {"field": "change_pct", "op": "<=", "value": -1},
        {"symbol": "NVDA260220C00220000", "field": "change_pct", "op": "<=", "value": -40},
    ]}

A condition without its own symbol uses the rule's symbol. Fields come from
enriched quotes (see quote_metrics), so range_pct and range_dollars are valid.

Evaluation is stateless. The caller passes ids already fired so repeat
notifications are suppressed.
"""

import operator

FIELDS = ("last", "change", "change_pct", "volume", "bid", "ask", "range_pct", "range_dollars")
OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}
MAX_CONDITIONS = 5


def _conditions(rule: dict) -> list[dict]:
    if "conditions" in rule:
        return [{"symbol": rule.get("symbol"), **c} if isinstance(c, dict) else c for c in rule["conditions"]]
    return [{"symbol": rule.get("symbol"), "field": rule.get("field"), "op": rule.get("op"), "value": rule.get("value")}]


def _validate_condition(c, label: str) -> list[str]:
    errors = []
    if not isinstance(c, dict):
        return [f"{label} must be an object"]
    if not isinstance(c.get("symbol"), str) or not c.get("symbol"):
        errors.append(f"{label} needs a symbol")
    if c.get("field") not in FIELDS:
        errors.append(f"{label}: unknown field {c.get('field')!r}")
    if c.get("op") not in OPS:
        errors.append(f"{label}: unknown op {c.get('op')!r}")
    value = c.get("value")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        errors.append(f"{label}: value must be a number")
    return errors


def validate_rule(rule: dict) -> list[str]:
    if not isinstance(rule, dict):
        return ["rule must be an object"]
    errors = []
    if not rule.get("id"):
        errors.append("rule needs an id")
    if "conditions" in rule:
        conditions = rule["conditions"]
        if not isinstance(conditions, list) or not conditions:
            errors.append("conditions must be a non-empty list")
        elif len(conditions) > MAX_CONDITIONS:
            errors.append(f"at most {MAX_CONDITIONS} conditions per rule")
        else:
            for i, c in enumerate(conditions):
                errors.extend(_validate_condition(
                    {"symbol": rule.get("symbol"), **c} if isinstance(c, dict) else c, f"conditions[{i}]"))
    else:
        errors.extend(_validate_condition(
            {"symbol": rule.get("symbol"), "field": rule.get("field"), "op": rule.get("op"), "value": rule.get("value")},
            "rule"))
    return errors


def required_symbols(rules: list[dict]) -> list[str]:
    seen = []
    for rule in rules:
        for c in _conditions(rule):
            sym = c.get("symbol")
            if isinstance(sym, str) and sym and sym not in seen:
                seen.append(sym)
    return seen


def evaluate_alerts(rules: list[dict], quotes: list[dict], already_fired: set[str] | None = None) -> list[dict]:
    """Return rules whose conditions all hold on these quotes and that have not already fired."""
    already_fired = already_fired or set()
    by_symbol = {q["symbol"]: q for q in quotes}
    fired = []
    for rule in rules:
        if rule.get("id") in already_fired:
            continue
        actuals = []
        for c in _conditions(rule):
            quote = by_symbol.get(c.get("symbol"))
            actual = quote.get(c.get("field")) if quote else None
            holds = actual is not None and OPS[c["op"]](actual, c["value"])
            actuals.append({"symbol": c.get("symbol"), "field": c.get("field"), "actual": actual, "holds": holds})
            if not holds:
                break
        else:
            fired.append({**rule, "actuals": actuals})
    return fired
