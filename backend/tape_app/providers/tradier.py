"""Tradier market data adapter.

Needs TRADIER_TOKEN. TRADIER_ENV=sandbox uses the sandbox host (delayed quotes).
Live quotes and real-time options data come with a funded Tradier brokerage
account, which is the trade-off recorded in the data-source decision.
"""

import json
import urllib.parse
import urllib.request

from .base import MarketDataProvider

HOSTS = {
    "live": "https://api.tradier.com/v1",
    "sandbox": "https://sandbox.tradier.com/v1",
}


def _as_list(value) -> list:
    """Tradier returns a bare object for one result and a list for many, or null."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def map_quote(raw: dict) -> dict:
    return {
        "symbol": raw["symbol"],
        "last": _num(raw.get("last")),
        "change": _num(raw.get("change")),
        "change_pct": _num(raw.get("change_percentage")),
        "bid": _num(raw.get("bid")),
        "ask": _num(raw.get("ask")),
        "volume": _num(raw.get("volume")),
        "open": _num(raw.get("open")),
        "high": _num(raw.get("high")),
        "low": _num(raw.get("low")),
        "prev_close": _num(raw.get("prevclose")),
        "time": raw.get("trade_date"),
    }


def map_contract(raw: dict, underlying: str) -> dict:
    return {
        "symbol": raw["symbol"],
        "underlying": underlying,
        "type": raw.get("option_type"),
        "strike": _num(raw.get("strike")),
        "expiration": raw.get("expiration_date"),
        "bid": _num(raw.get("bid")),
        "ask": _num(raw.get("ask")),
        "last": _num(raw.get("last")),
        "volume": _num(raw.get("volume")),
        "open_interest": _num(raw.get("open_interest")),
    }


class TradierProvider(MarketDataProvider):
    name = "tradier"

    def __init__(self, token: str, env: str = "live", opener=urllib.request.urlopen, timeout: float = 10):
        if not token:
            raise ValueError("TradierProvider requires a token (TRADIER_TOKEN)")
        self.base_url = HOSTS.get(env, HOSTS["live"])
        self.token = token
        self._open = opener
        self.timeout = timeout

    def _get(self, path: str, params: dict) -> dict:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(
            f"{self.base_url}{path}?{query}",
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
        )
        with self._open(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get_quotes(self, symbols: list[str]) -> list[dict]:
        if not symbols:
            return []
        data = self._get("/markets/quotes", {"symbols": ",".join(symbols)})
        quotes = (data.get("quotes") or {}).get("quote")
        return [map_quote(q) for q in _as_list(quotes) if q and q.get("symbol")]

    def get_expirations(self, symbol: str) -> list[str]:
        data = self._get("/markets/options/expirations", {"symbol": symbol, "includeAllRoots": "true"})
        return _as_list((data.get("expirations") or {}).get("date"))

    def get_option_chain(self, symbol: str, expiration: str) -> list[dict]:
        data = self._get("/markets/options/chains", {"symbol": symbol, "expiration": expiration, "greeks": "false"})
        options = (data.get("options") or {}).get("option")
        return [map_contract(o, symbol) for o in _as_list(options)]
