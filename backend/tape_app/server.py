"""HTTP API and live tape for the Tape app.

Standard library only (http.server + server-sent events), so it runs without
installing packages. Swap for FastAPI once packages are available; the routes
and payloads stay the same.

Routes:
    GET  /api/health
    GET  /api/quotes?symbols=AAPL,MSFT
    GET  /api/options/{symbol}/expirations
    GET  /api/options/{symbol}/chain?expiration=YYYY-MM-DD
    GET  /api/options/{symbol}/summary?expiration=YYYY-MM-DD   (strikes, distance %, range position)
    POST /api/screener    body: {"universe": [...], "filters": [...], "sort": {...}, "limit": n}
    POST /api/size        body: {"mode": "shares"|"contracts", "account", "risk_pct", ...}
    POST /api/strategy/vertical   body: {"strategy", "long_strike", "short_strike", "long_premium", "short_premium", "curve"?}
    POST /api/alerts/evaluate     body: {"rules": [...], "already_fired": [...]}
    POST /api/size/scenarios      body: {"premium", "risk_pct", "portfolios"?: [500, ...]}
    POST /api/strategy/iron-condor  body: {put_long, put_short, call_short, call_long, *_premium}
    POST /api/strategy/naked      body: {"kind": "call"|"put", "side": "long"|"short", "strike", "premium"}
    GET  /api/tape?symbols=AAPL,MSFT&interval=2   (text/event-stream, event: quotes)
"""

import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .alerts import evaluate_alerts, required_symbols, validate_rule
from .options import summarize_chain
from .quote_metrics import enrich_all
from .providers import create_provider
from .providers.base import MarketDataProvider
from .screener import run_screen, validate_screen
from .sizing import size_option_contracts, size_scenarios, size_shares
from .strategies import (build_iron_condor, build_naked, build_vertical, iron_condor_payoff,
                         payoff_curve, payoff_at_expiry)

MAX_SYMBOLS = 100


class _HttpError(Exception):
    def __init__(self, status: int, message: str, details=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details
MAX_BODY_BYTES = 64 * 1024
DEFAULT_TAPE_INTERVAL = 2.0
MIN_TAPE_INTERVAL = 0.5
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_symbols(raw) -> list[str]:
    """Normalize 'aapl, MSFT,aapl' or ['aapl', 'msft'] to ['AAPL', 'MSFT']."""
    if isinstance(raw, list):
        raw = ",".join(str(s) for s in raw)
    seen = []
    for part in str(raw or "").split(","):
        symbol = part.strip().upper()
        if symbol and symbol not in seen:
            seen.append(symbol)
    return seen[:MAX_SYMBOLS]


def stream_tape(provider: MarketDataProvider, symbols: list[str], interval: float,
                write, sleep=time.sleep, max_polls: int | None = None) -> int:
    """Poll the provider and write an SSE event whenever a symbol's last price changes.

    `write` receives the raw SSE text. Returns the number of polls performed.
    Stops when the client disconnects (write raises) or after max_polls.
    """
    last_seen: dict[str, float] = {}
    polls = 0
    while max_polls is None or polls < max_polls:
        polls += 1
        changed = []
        for quote in provider.get_quotes(symbols):
            if last_seen.get(quote["symbol"]) != quote["last"]:
                last_seen[quote["symbol"]] = quote["last"]
                changed.append(quote)
        if changed:
            payload = json.dumps({"quotes": changed})
            try:
                write(f"event: quotes\ndata: {payload}\n\n")
            except (BrokenPipeError, ConnectionResetError):
                break
        if max_polls is None or polls < max_polls:
            sleep(interval)
    return polls


def make_handler(provider: MarketDataProvider):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TapeApp/0.1"

        # ---- helpers -----------------------------------------------------
        def _send_json(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)

        def _error(self, status: int, message: str, details=None) -> None:
            body = {"error": message}
            if details:
                body["details"] = details
            self._send_json(status, body)

        def log_message(self, fmt, *args):  # keep test and dev output quiet
            pass

        def _cors(self) -> None:
            # Local development only: lets the preview page (opened from disk or another port) call the API.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        # ---- routes ------------------------------------------------------
        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            try:
                if url.path == "/api/health":
                    return self._send_json(200, {"ok": True, "provider": provider.name})
                if url.path == "/api/quotes":
                    symbols = parse_symbols(query.get("symbols", [""])[0])
                    if not symbols:
                        return self._error(400, "symbols query param required")
                    return self._send_json(200, {"quotes": enrich_all(provider.get_quotes(symbols))})
                if url.path == "/api/tape":
                    return self._stream_tape(query)
                match = re.fullmatch(r"/api/options/([^/]+)/(expirations|chain)", url.path)
                if match:
                    symbol = match.group(1).upper()
                    if match.group(2) == "expirations":
                        return self._send_json(200, {"symbol": symbol, "expirations": provider.get_expirations(symbol)})
                    expiration = query.get("expiration", [""])[0]
                    if not DATE_RE.match(expiration):
                        return self._error(400, "expiration must be YYYY-MM-DD")
                    contracts = provider.get_option_chain(symbol, expiration)
                    return self._send_json(200, {"symbol": symbol, "expiration": expiration, "contracts": contracts})
                summary = re.fullmatch(r"/api/options/([^/]+)/summary", url.path)
                if summary:
                    symbol = summary.group(1).upper()
                    expiration = query.get("expiration", [""])[0]
                    if not DATE_RE.match(expiration):
                        return self._error(400, "expiration must be YYYY-MM-DD")
                    spot_quotes = provider.get_quotes([symbol])
                    spot = spot_quotes[0]["last"] if spot_quotes else None
                    contracts = provider.get_option_chain(symbol, expiration)
                    body = summarize_chain(spot, contracts)
                    return self._send_json(200, {"symbol": symbol, "expiration": expiration, **body})
                return self._error(404, "not found")
            except Exception as exc:  # upstream failures should not crash the server
                return self._error(502, "upstream data error", str(exc))

        def _read_body(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length > MAX_BODY_BYTES:
                raise _HttpError(413, "request body too large")
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                raise _HttpError(400, "body must be JSON")
            if not isinstance(body, dict):
                raise _HttpError(400, "body must be a JSON object")
            return body

        def do_POST(self):
            path = urlparse(self.path).path
            handlers = {
                "/api/screener": self._post_screener,
                "/api/size": self._post_size,
                "/api/strategy/vertical": self._post_vertical,
                "/api/alerts/evaluate": self._post_alerts,
                "/api/size/scenarios": self._post_scenarios,
                "/api/strategy/iron-condor": self._post_iron_condor,
                "/api/strategy/naked": self._post_naked,
            }
            handler = handlers.get(path)
            if handler is None:
                return self._error(404, "not found")
            try:
                return handler(self._read_body())
            except _HttpError as err:
                return self._error(err.status, err.message, err.details)
            except KeyError as exc:
                return self._error(400, f"missing field {exc.args[0]!r}")
            except (ValueError, TypeError) as exc:
                return self._error(400, str(exc))
            except Exception as exc:
                return self._error(502, "upstream data error", str(exc))

        def _post_screener(self, body):
            symbols = parse_symbols(body.get("universe", []))
            if not symbols:
                raise _HttpError(400, "universe must be a non-empty list of symbols")
            errors = validate_screen(body)
            if errors:
                raise _HttpError(400, "invalid screen", errors)
            results = run_screen(enrich_all(provider.get_quotes(symbols)), body)
            return self._send_json(200, {"count": len(results), "results": results})

        def _post_size(self, body):
            mode = body.get("mode")
            if mode == "shares":
                result = size_shares(float(body["account"]), float(body["risk_pct"]),
                                     float(body["entry"]), float(body["stop"]))
            elif mode == "contracts":
                result = size_option_contracts(float(body["account"]), float(body["risk_pct"]),
                                               float(body["premium"]), int(body.get("multiplier", 100)))
            else:
                raise _HttpError(400, 'mode must be "shares" or "contracts"')
            return self._send_json(200, result)

        def _post_scenarios(self, body):
            portfolios = body.get("portfolios")
            kwargs = {"portfolios": [float(p) for p in portfolios]} if portfolios else {}
            rows = size_scenarios(float(body["premium"]), float(body["risk_pct"]),
                                  multiplier=int(body.get("multiplier", 100)), **kwargs)
            return self._send_json(200, {"scenarios": rows})

        def _post_iron_condor(self, body):
            position = build_iron_condor(
                float(body["put_long"]), float(body["put_short"]), float(body["call_short"]), float(body["call_long"]),
                float(body["put_long_premium"]), float(body["put_short_premium"]),
                float(body["call_short_premium"]), float(body["call_long_premium"]),
                int(body.get("multiplier", 100)),
            )
            curve_spec = body.get("curve")
            if curve_spec:
                low, high, steps = float(curve_spec["low"]), float(curve_spec["high"]), int(curve_spec.get("steps", 50))
                if high <= low or steps < 2:
                    raise _HttpError(400, "curve needs high > low and steps >= 2")
                step = (high - low) / (steps - 1)
                position["curve"] = [{"underlying": round(low + i * step, 4),
                                      "pnl": iron_condor_payoff(position, low + i * step)} for i in range(steps)]
            return self._send_json(200, position)

        def _post_naked(self, body):
            return self._send_json(200, build_naked(body.get("kind"), body.get("side"),
                                                    float(body["strike"]), float(body["premium"]),
                                                    int(body.get("multiplier", 100))))

        def _post_vertical(self, body):
            position = build_vertical(
                body.get("strategy"),
                float(body["long_strike"]), float(body["short_strike"]),
                float(body["long_premium"]), float(body["short_premium"]),
                int(body.get("multiplier", 100)),
            )
            curve_spec = body.get("curve")
            if curve_spec:
                curve = payoff_curve(position, float(curve_spec["low"]), float(curve_spec["high"]),
                                     int(curve_spec.get("steps", 50)))
                position["curve"] = curve
            return self._send_json(200, position)

        def _post_alerts(self, body):
            rules = body.get("rules", [])
            if not isinstance(rules, list):
                raise _HttpError(400, "rules must be a list")
            errors = [f"rules[{i}]: {e}" for i, r in enumerate(rules) for e in validate_rule(r)]
            if errors:
                raise _HttpError(400, "invalid rules", errors)
            symbols = required_symbols(rules)
            fired = evaluate_alerts(rules, enrich_all(provider.get_quotes(symbols)) if symbols else [],
                                    set(body.get("already_fired", [])))
            return self._send_json(200, {"fired": fired})

        def _stream_tape(self, query):
            symbols = parse_symbols(query.get("symbols", [""])[0])
            if not symbols:
                return self._error(400, "symbols query param required")
            try:
                interval = float(query.get("interval", [DEFAULT_TAPE_INTERVAL])[0])
            except ValueError:
                return self._error(400, "interval must be a number of seconds")
            interval = max(MIN_TAPE_INTERVAL, interval)

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self._cors()
            self.end_headers()

            def write(text: str) -> None:
                self.wfile.write(text.encode("utf-8"))
                self.wfile.flush()

            try:
                stream_tape(provider, symbols, interval, write)
            except Exception as exc:
                write(f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n")

    return Handler


def create_server(provider: MarketDataProvider, host: str = "127.0.0.1", port: int = 4000) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(provider))
    server.daemon_threads = True
    return server


def main() -> None:
    import os

    provider = create_provider()
    port = int(os.environ.get("PORT", "4000"))
    server = create_server(provider, port=port)
    print(f"tape-app backend on http://127.0.0.1:{port} (provider: {provider.name})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
