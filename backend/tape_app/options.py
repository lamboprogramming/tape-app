"""Options chain analytics: strike distance and range position.

range_position answers "where is the underlying inside the chain's strike
ladder?": 0.0 = at the lowest listed strike, 1.0 = at the highest. This is an
interpretation of "range position" in the feature notes; adjust if you meant
the day's high-low range or a 52-week range.
"""


def range_position(spot: float, strikes: list[float]) -> float | None:
    """Fraction of the strike ladder below spot, clamped to [0, 1]."""
    if spot is None or not strikes:
        return None
    low, high = min(strikes), max(strikes)
    if high == low:
        return 0.5
    return min(1.0, max(0.0, (spot - low) / (high - low)))


def strike_distance_pct(spot: float, strike: float) -> float | None:
    if not spot:
        return None
    return round((strike - spot) / spot * 100, 4)


def summarize_chain(spot: float, contracts: list[dict]) -> dict:
    """Group a chain by strike and add distance-from-spot and mid prices.

    Returns one row per strike with call and put bid/ask/mid and open interest.
    """
    strikes = sorted({c["strike"] for c in contracts if c.get("strike") is not None})
    rows = {s: {"strike": s, "distance_pct": strike_distance_pct(spot, s), "call": None, "put": None} for s in strikes}
    for c in contracts:
        if c.get("strike") is None or c.get("type") not in ("call", "put"):
            continue
        bid, ask = c.get("bid"), c.get("ask")
        mid = round((bid + ask) / 2, 4) if bid is not None and ask is not None else None
        rows[c["strike"]][c["type"]] = {
            "symbol": c.get("symbol"),
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "volume": c.get("volume"),
            "open_interest": c.get("open_interest"),
        }
    return {
        "spot": spot,
        "range_position": range_position(spot, strikes),
        "strikes": [rows[s] for s in strikes],
    }
