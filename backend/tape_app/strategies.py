"""Two-leg vertical spreads: payoff at expiration.

Supported structures:
    bull_call  debit   buy lower call, sell higher call
    bear_put   debit   buy higher put, sell lower put
    bull_put   credit  sell higher put, buy lower put
    bear_call  credit  sell lower call, buy higher call

Per-share numbers. Multiply by the contract multiplier (usually 100) for dollars.
Payoff is evaluated at expiration (intrinsic value only).
"""

SPREADS = {
    "bull_call": {"kind": "call", "debit": True},
    "bear_put": {"kind": "put", "debit": True},
    "bull_put": {"kind": "put", "debit": False},
    "bear_call": {"kind": "call", "debit": False},
}


def _intrinsic(kind: str, underlying: float, strike: float) -> float:
    if kind == "call":
        return max(0.0, underlying - strike)
    return max(0.0, strike - underlying)


def build_vertical(strategy: str, long_strike: float, short_strike: float,
                   long_premium: float, short_premium: float, multiplier: int = 100) -> dict:
    """Describe a two-leg vertical spread.

    `long_premium` is the price paid for the bought option; `short_premium` is
    the price received for the sold option. Net debit or credit is derived.
    """
    if strategy not in SPREADS:
        raise ValueError(f"unknown strategy {strategy!r}; expected one of {sorted(SPREADS)}")
    if long_strike == short_strike:
        raise ValueError("strikes must differ")
    spec = SPREADS[strategy]
    kind = spec["kind"]
    # Strike ordering that makes each structure a real spread.
    if strategy == "bull_call" and not long_strike < short_strike:
        raise ValueError("bull call: long (lower) strike must be below short strike")
    if strategy == "bear_put" and not long_strike > short_strike:
        raise ValueError("bear put: long (higher) strike must be above short strike")
    if strategy == "bull_put" and not short_strike > long_strike:
        raise ValueError("bull put: short (higher) strike must be above long strike")
    if strategy == "bear_call" and not short_strike < long_strike:
        raise ValueError("bear call: short (lower) strike must be below long strike")
    width = abs(short_strike - long_strike)
    net = round(long_premium - short_premium, 4)  # positive = debit paid

    if spec["debit"]:
        if net <= 0:
            raise ValueError("a debit spread must cost money to enter")
        max_loss = net
        max_profit = width - net
    else:
        if net >= 0:
            raise ValueError("a credit spread must receive money to enter")
        max_loss = width + net
        max_profit = -net

    if max_profit <= 0:
        raise ValueError("spread has no profit potential at these prices")

    if kind == "call":
        breakeven = long_strike + net if spec["debit"] else short_strike - net
    else:
        breakeven = long_strike - net if spec["debit"] else short_strike + net

    return {
        "strategy": strategy,
        "kind": kind,
        "long_strike": long_strike,
        "short_strike": short_strike,
        "net_premium": net,
        "net_type": "debit" if spec["debit"] else "credit",
        "width": width,
        "max_profit": round(max_profit * multiplier, 2),
        "max_loss": round(max_loss * multiplier, 2),
        "breakeven": round(breakeven, 4),
        "multiplier": multiplier,
    }


def payoff_at_expiry(position: dict, underlying: float) -> float:
    """Dollar P&L at expiration for a built spread at one underlying price."""
    kind = position["kind"]
    long_leg = _intrinsic(kind, underlying, position["long_strike"])
    short_leg = _intrinsic(kind, underlying, position["short_strike"])
    # Long leg is worth its intrinsic value, short leg is owed its intrinsic
    # value; net premium is what was paid (debit) or received (credit).
    per_share = (long_leg - short_leg) - position["net_premium"]
    return round(per_share * position["multiplier"], 2)


def payoff_curve(position: dict, low: float, high: float, steps: int = 50) -> list[dict]:
    """P&L across an evenly spaced grid of underlying prices, for charting."""
    if steps < 2 or high <= low:
        raise ValueError("need steps >= 2 and high > low")
    step = (high - low) / (steps - 1)
    return [
        {"underlying": round(low + i * step, 4), "pnl": payoff_at_expiry(position, low + i * step)}
        for i in range(steps)
    ]
