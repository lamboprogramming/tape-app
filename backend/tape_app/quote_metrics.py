"""Derived metrics for a quote: position in the day's range and flags.

Position in range is per contract (or stock): 0% at the day's low, 100% at the
day's high. Flags are simple thresholds. Volume-spike and multi-window flow
signals need stored snapshots and are not here yet.
"""

NEAR_HIGH_PCT = 90.0  # at or above this share of the day range -> "near_high"
NEAR_LOW_PCT = 10.0  # at or below -> "near_low"
PULLBACK_FROM_HIGH_PCT = 5.0  # last is this far below the day high -> "pullback_from_high"


def enrich(quote: dict) -> dict:
    """Return a copy of the quote with range_dollars, range_pct and flags added."""
    low, high, last = quote.get("low"), quote.get("high"), quote.get("last")
    out = dict(quote)
    if low is None or high is None or last is None or high <= low:
        out.update(range_dollars=None, range_pct=None, flags=[])
        return out

    range_dollars = round(high - low, 4)
    range_pct = round((last - low) / (high - low) * 100, 2)
    flags = []
    if range_pct >= NEAR_HIGH_PCT:
        flags.append("near_high")
    if range_pct <= NEAR_LOW_PCT:
        flags.append("near_low")
    if high > 0 and (high - last) / high * 100 >= PULLBACK_FROM_HIGH_PCT:
        flags.append("pullback_from_high")
    out.update(range_dollars=range_dollars, range_pct=range_pct, flags=flags)
    return out


def enrich_all(quotes: list[dict]) -> list[dict]:
    return [enrich(q) for q in quotes]
