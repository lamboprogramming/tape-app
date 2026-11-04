"""Shared snapshot cache in front of a market data provider.

Every member reads the same snapshot, so the admin's data account is queried
once per symbol per window rather than once per member. Quote and chain
snapshots expire after `quote_ttl` seconds (30 by default; the notes suggest
30 to 60 during market hours). Expirations change rarely and are cached longer.

Market-hours gating is not implemented yet; the TTL applies at all times.
"""

import time

from .providers.base import MarketDataProvider


class CachedProvider(MarketDataProvider):
    def __init__(self, inner: MarketDataProvider, quote_ttl: float = 30.0,
                 expiration_ttl: float = 3600.0, clock=time.monotonic):
        self.inner = inner
        self.name = inner.name
        self.quote_ttl = quote_ttl
        self.expiration_ttl = expiration_ttl
        self.clock = clock
        self._quotes: dict[str, tuple[float, dict]] = {}
        self._expirations: dict[str, tuple[float, list[str]]] = {}
        self._chains: dict[tuple[str, str], tuple[float, list[dict]]] = {}
        self.upstream_calls = 0  # visible in tests and for cost monitoring

    def get_quotes(self, symbols: list[str]) -> list[dict]:
        now = self.clock()
        missing = [s for s in symbols if s not in self._quotes or now - self._quotes[s][0] >= self.quote_ttl]
        if missing:
            self.upstream_calls += 1
            for q in self.inner.get_quotes(missing):
                self._quotes[q["symbol"]] = (now, q)
        return [self._quotes[s][1] for s in symbols if s in self._quotes]

    def get_expirations(self, symbol: str) -> list[str]:
        now = self.clock()
        hit = self._expirations.get(symbol)
        if hit and now - hit[0] < self.expiration_ttl:
            return hit[1]
        self.upstream_calls += 1
        value = self.inner.get_expirations(symbol)
        self._expirations[symbol] = (now, value)
        return value

    def get_option_chain(self, symbol: str, expiration: str) -> list[dict]:
        now = self.clock()
        key = (symbol, expiration)
        hit = self._chains.get(key)
        if hit and now - hit[0] < self.quote_ttl:
            return hit[1]
        self.upstream_calls += 1
        value = self.inner.get_option_chain(symbol, expiration)
        self._chains[key] = (now, value)
        return value
