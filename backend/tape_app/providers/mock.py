"""Simulated feed for development without API keys.

Prices random-walk on every call. Option contracts keep their own price state,
so a contract's quote and its chain row agree. Pass a seeded random.Random for
reproducible output in tests.
"""

import random
from datetime import datetime, timedelta, timezone

from .base import MarketDataProvider

BASE_PRICES = {
    "AAPL": 230, "MSFT": 415, "NVDA": 130, "TSLA": 250, "AMZN": 210,
    "SPY": 580, "QQQ": 500, "AMD": 160, "META": 600, "GOOGL": 175,
}


def _seed_price(symbol: str) -> float:
    if symbol in BASE_PRICES:
        return float(BASE_PRICES[symbol])
    return 20.0 + sum(ord(c) for c in symbol) % 900


def _round(n: float) -> float:
    return round(n, 2)


class _Series:
    """One instrument's session state: previous close, open, running high and low, last."""

    def __init__(self, base: float):
        self.prev_close = base
        self.open = base
        self.last = base
        self.high = base
        self.low = base

    def move(self, rng: random.Random, pct: float) -> None:
        self.last = max(0.01, self.last * (1 + (rng.random() - 0.5) * pct))
        self.high = max(self.high, self.last)
        self.low = min(self.low, self.last)


class MockProvider(MarketDataProvider):
    name = "mock"

    def __init__(self, rng: random.Random | None = None, clock=None):
        self.rng = rng or random.Random()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._underlyings: dict[str, _Series] = {}
        self._contracts: dict[str, dict] = {}  # OCC-style symbol -> {"series": _Series, "meta": {...}}

    def _underlying(self, symbol: str) -> _Series:
        if symbol not in self._underlyings:
            self._underlyings[symbol] = _Series(_seed_price(symbol))
        series = self._underlyings[symbol]
        series.move(self.rng, 0.004)
        return series

    def get_quotes(self, symbols: list[str]) -> list[dict]:
        now_ms = int(self.clock().timestamp() * 1000)
        out = []
        for symbol in symbols:
            if symbol in self._contracts:
                series = self._contracts[symbol]["series"]
                series.move(self.rng, 0.02)
                volume = self._contracts[symbol]["meta"]["volume"]
            else:
                series = self._underlying(symbol)
                volume = self.rng.randint(100_000, 5_100_000)
            out.append(_quote(symbol, series, volume, now_ms))
        return out

    def get_expirations(self, symbol: str) -> list[str]:
        start = self.clock()
        return [(start + timedelta(days=d)).date().isoformat() for d in range(0, 57, 1) if (start + timedelta(days=d)).weekday() < 5][:12]

    def get_option_chain(self, symbol: str, expiration: str) -> list[dict]:
        spot = self._underlying(symbol).last
        step = 5.0 if spot >= 200 else 2.5 if spot >= 50 else 1.0
        center = round(spot / step) * step
        contracts = []
        for i in range(-5, 6):
            strike = center + i * step
            for kind in ("call", "put"):
                occ = f"{symbol}{expiration.replace('-', '')}{'C' if kind == 'call' else 'P'}{strike:g}"
                if occ not in self._contracts:
                    intrinsic = max(0.0, spot - strike) if kind == "call" else max(0.0, strike - spot)
                    start = intrinsic + 0.5 + abs(i) * 0.3
                    self._contracts[occ] = {
                        "series": _Series(start),
                        "meta": {"underlying": symbol, "type": kind, "strike": strike, "expiration": expiration,
                                 "volume": self.rng.randint(0, 20_000), "open_interest": self.rng.randint(0, 10_000)},
                    }
                record = self._contracts[occ]
                series, meta = record["series"], record["meta"]
                series.move(self.rng, 0.02)
                mid = series.last
                spread = max(0.01, round(mid * 0.04, 2))
                contracts.append({
                    "symbol": occ,
                    "underlying": symbol,
                    "type": kind,
                    "strike": strike,
                    "expiration": expiration,
                    "bid": _round(max(0.01, mid - spread / 2)),
                    "ask": _round(mid + spread / 2),
                    "last": _round(mid),
                    "volume": meta["volume"],
                    "open_interest": meta["open_interest"],
                })
        return contracts


def _quote(symbol: str, series: _Series, volume: int, now_ms: int) -> dict:
    last = series.last
    change = last - series.prev_close
    spread = max(0.01, last * 0.0005)
    return {
        "symbol": symbol,
        "last": _round(last),
        "change": _round(change),
        "change_pct": _round(change / series.prev_close * 100),
        "bid": _round(last - spread / 2),
        "ask": _round(last + spread / 2),
        "volume": volume,
        "open": _round(series.open),
        "high": _round(series.high),
        "low": _round(series.low),
        "prev_close": _round(series.prev_close),
        "time": now_ms,
    }
