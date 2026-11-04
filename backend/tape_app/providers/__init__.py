import os

from .base import MarketDataProvider
from .mock import MockProvider
from .tradier import TradierProvider


def create_provider(env: dict | None = None) -> MarketDataProvider:
    """Choose a provider from the environment.

    DATA_PROVIDER=tradier  requires TRADIER_TOKEN (TRADIER_ENV=live|sandbox)
    DATA_PROVIDER=mock     default when no token is set
    """
    env = os.environ if env is None else env
    choice = (env.get("DATA_PROVIDER") or ("tradier" if env.get("TRADIER_TOKEN") else "mock")).lower()
    if choice == "tradier":
        return TradierProvider(token=env.get("TRADIER_TOKEN", ""), env=env.get("TRADIER_ENV", "live"))
    if choice == "mock":
        return MockProvider()
    raise ValueError(f'Unknown DATA_PROVIDER "{choice}"')
