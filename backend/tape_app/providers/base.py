"""Market data provider contract.

Every provider returns the app's canonical shapes so nothing downstream depends
on a vendor. Vendor-specific fields are mapped inside each adapter.

Quote (dict):
    symbol, last, change, change_pct (vs previous close), bid, ask, volume,
    open, high, low, prev_close, time (ms epoch or date string)

OptionContract (dict):
    symbol, underlying, type ("call" | "put"), strike, expiration (YYYY-MM-DD),
    bid, ask, last, volume, open_interest
"""

from abc import ABC, abstractmethod


class MarketDataProvider(ABC):
    name: str = "base"

    @abstractmethod
    def get_quotes(self, symbols: list[str]) -> list[dict]:
        """Return one quote per symbol the provider knows about."""

    @abstractmethod
    def get_expirations(self, symbol: str) -> list[str]:
        """Return option expiration dates as YYYY-MM-DD, ascending."""

    @abstractmethod
    def get_option_chain(self, symbol: str, expiration: str) -> list[dict]:
        """Return every call and put for one expiration."""
