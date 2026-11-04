"""Customizable screener engine.

A screen is a list of filters over quotes:
    {"field": "change_pct", "op": ">=", "value": 2}
    {"field": "volume", "op": "between", "value": [1_000_000, 50_000_000]}

Filters are AND-ed. A quote missing a field never matches a filter on that
field. Results can be sorted and limited.
"""

import operator
from typing import Any, Callable

FIELDS: dict[str, Callable[[dict], Any]] = {
    "last": lambda q: q.get("last"),
    "change": lambda q: q.get("change"),
    "change_pct": lambda q: q.get("change_pct"),
    "volume": lambda q: q.get("volume"),
    "bid": lambda q: q.get("bid"),
    "ask": lambda q: q.get("ask"),
    "spread": lambda q: (q["ask"] - q["bid"]) if q.get("ask") is not None and q.get("bid") is not None else None,
}

OPS: dict[str, Callable[[Any, Any], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    "between": lambda a, bounds: bounds[0] <= a <= bounds[1],
}


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_screen(screen: dict) -> list[str]:
    errors = []
    filters = screen.get("filters", [])
    if not isinstance(filters, list):
        return ["filters must be a list"]
    for i, f in enumerate(filters):
        if not isinstance(f, dict):
            errors.append(f"filters[{i}] must be an object")
            continue
        if f.get("field") not in FIELDS:
            errors.append(f"filters[{i}]: unknown field {f.get('field')!r}")
        if f.get("op") not in OPS:
            errors.append(f"filters[{i}]: unknown op {f.get('op')!r}")
        value = f.get("value")
        if f.get("op") == "between":
            if not (isinstance(value, list) and len(value) == 2 and all(_is_number(v) for v in value)):
                errors.append(f"filters[{i}]: between needs a [low, high] numeric pair")
        elif not _is_number(value):
            errors.append(f"filters[{i}]: value must be a number")
    sort = screen.get("sort")
    if sort is not None and (not isinstance(sort, dict) or sort.get("field") not in FIELDS):
        errors.append(f"unknown sort field {sort.get('field') if isinstance(sort, dict) else sort!r}")
    limit = screen.get("limit")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500):
        errors.append("limit must be an integer between 1 and 500")
    return errors


def run_screen(quotes: list[dict], screen: dict) -> list[dict]:
    filters = screen.get("filters", [])
    matches = []
    for q in quotes:
        ok = True
        for f in filters:
            actual = FIELDS[f["field"]](q)
            if actual is None or not OPS[f["op"]](actual, f["value"]):
                ok = False
                break
        if ok:
            matches.append(q)

    sort = screen.get("sort")
    if sort:
        reverse = sort.get("direction", "desc") != "asc"
        getter = FIELDS[sort["field"]]
        # Missing values sort last regardless of direction.
        present = [q for q in matches if getter(q) is not None]
        missing = [q for q in matches if getter(q) is None]
        present.sort(key=getter, reverse=reverse)
        matches = present + missing

    limit = screen.get("limit")
    return matches[:limit] if limit else matches
