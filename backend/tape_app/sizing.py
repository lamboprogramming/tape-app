"""Risk-based position sizing.

Given an account size, the percent of the account you are willing to lose on
one trade, and an entry and stop, return how many shares or contracts to take.
Results round down so the risk never exceeds the budget.
"""

import math


def size_shares(account: float, risk_pct: float, entry: float, stop: float) -> dict:
    _check_positive(account=account, entry=entry)
    if not 0 < risk_pct <= 100:
        raise ValueError("risk_pct must be in (0, 100]")
    per_share_risk = abs(entry - stop)
    if per_share_risk == 0:
        raise ValueError("entry and stop must differ")
    budget = account * risk_pct / 100
    shares = math.floor(budget / per_share_risk)
    return {
        "shares": shares,
        "risk_budget": round(budget, 2),
        "risk_at_size": round(shares * per_share_risk, 2),
        "position_value": round(shares * entry, 2),
    }


def size_option_contracts(account: float, risk_pct: float, premium: float,
                          multiplier: int = 100) -> dict:
    """Size a long-premium trade where max loss is the premium paid."""
    _check_positive(account=account, premium=premium)
    if not 0 < risk_pct <= 100:
        raise ValueError("risk_pct must be in (0, 100]")
    budget = account * risk_pct / 100
    per_contract = premium * multiplier
    contracts = math.floor(budget / per_contract)
    return {
        "contracts": contracts,
        "risk_budget": round(budget, 2),
        "max_loss": round(contracts * per_contract, 2),
    }


def _check_positive(**values: float) -> None:
    for name, value in values.items():
        if value is None or value <= 0:
            raise ValueError(f"{name} must be positive")
