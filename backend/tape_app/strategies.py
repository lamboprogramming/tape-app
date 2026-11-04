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


def build_iron_condor(put_long: float, put_short: float, call_short: float, call_long: float,
                      put_long_premium: float, put_short_premium: float,
                      call_short_premium: float, call_long_premium: float,
                      multiplier: int = 100) -> dict:
    """Iron condor: a bull put spread below spot plus a bear call spread above it.

    Strikes must read put_long < put_short < call_short < call_long.
    Net premium is received (credit). Max loss is the wider side minus the credit.
    """
    if not put_long < put_short < call_short < call_long:
        raise ValueError("iron condor strikes must satisfy put_long < put_short < call_short < call_long")
    put_side = build_vertical("bull_put", put_long, put_short, put_long_premium, put_short_premium, multiplier)
    call_side = build_vertical("bear_call", call_long, call_short, call_long_premium, call_short_premium, multiplier)
    credit = put_side["max_profit"] + call_side["max_profit"]
    wider = max(put_side["width"], call_side["width"]) * multiplier
    max_loss = round(wider - credit, 2)
    return {
        "strategy": "iron_condor",
        "net_type": "credit",
        "max_profit": round(credit, 2),
        "max_loss": max_loss,
        "breakevens": [round(put_short - (credit / multiplier), 4), round(call_short + (credit / multiplier), 4)],
        "put_side": put_side,
        "call_side": call_side,
        "multiplier": multiplier,
    }


def iron_condor_payoff(position: dict, underlying: float) -> float:
    """Dollar P&L at expiration for a built iron condor."""
    return round(payoff_at_expiry(position["put_side"], underlying) + payoff_at_expiry(position["call_side"], underlying), 2)


def build_naked(kind: str, side: str, strike: float, premium: float, multiplier: int = 100) -> dict:
    """Single-leg short or long option with its risk, for the strategy builder.

    Short calls have unlimited risk. Short puts risk strike minus premium per share.
    Long options cannot lose more than the premium paid.
    """
    if kind not in ("call", "put"):
        raise ValueError("kind must be 'call' or 'put'")
    if side not in ("long", "short"):
        raise ValueError("side must be 'long' or 'short'")
    if strike <= 0 or premium <= 0:
        raise ValueError("strike and premium must be positive")
    warnings = []
    if side == "long":
        max_loss, max_profit, net_type = round(premium * multiplier, 2), None, "debit"
        breakeven = strike + premium if kind == "call" else strike - premium
    else:
        net_type = "credit"
        max_profit = round(premium * multiplier, 2)
        if kind == "call":
            max_loss = None  # unlimited
            breakeven = strike + premium
            warnings.append("Naked short call: risk is unlimited if the underlying keeps rising.")
        else:
            max_loss = round((strike - premium) * multiplier, 2)
            breakeven = strike - premium
            warnings.append("Naked short put: risk is the strike minus premium if the underlying goes to zero.")
    return {
        "strategy": f"{side}_{kind}",
        "net_type": net_type,
        "max_profit": max_profit,
        "max_loss": max_loss,
        "breakeven": round(breakeven, 4),
        "warnings": warnings,
        "multiplier": multiplier,
    }
