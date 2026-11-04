# Tape App backend (Python)

Standard library only: no packages to install yet. Swap in FastAPI once packages are available.

## Run

```bash
cd backend
python3 -m tape_app.server          # mock data by default
DATA_PROVIDER=tradier TRADIER_TOKEN=... python3 -m tape_app.server
TRADIER_ENV=sandbox                 # optional: delayed sandbox quotes
```

Port defaults to 4000 (`PORT` overrides it).

## Test

```bash
python3 -m unittest discover -s tests -t . -v
```

## API

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/health` | provider name |
| GET | `/api/quotes?symbols=AAPL,MSFT` | canonical quote shape |
| GET | `/api/options/{symbol}/expirations` | YYYY-MM-DD dates |
| GET | `/api/options/{symbol}/chain?expiration=YYYY-MM-DD` | calls and puts |
| POST | `/api/screener` | `{universe, filters, sort?, limit?}` |
| POST | `/api/size` | shares or contracts for one trade |
| POST | `/api/size/scenarios` | contracts at $500, $800, $1,000, $1,500, $2,000 portfolios |
| POST | `/api/strategy/vertical` | debit and credit spreads, optional payoff curve |
| POST | `/api/strategy/iron-condor` | four-leg credit spread with payoff |
| POST | `/api/strategy/naked` | single-leg risk, with warnings on naked shorts |
| POST | `/api/alerts/evaluate` | single or combined (all-of) conditions |
| GET | `/api/tape?symbols=AAPL&interval=2` | server-sent events, `event: quotes` |

Screener fields: `last`, `change`, `change_pct`, `volume`, `bid`, `ask`, `spread`, `range_pct`, `range_dollars`.
`range_pct` is the quote's position in its own day range (0 = day low, 100 = day high).
Quotes also carry `flags`: `near_high`, `near_low`, `pullback_from_high`.

Quotes are cached per symbol for `QUOTE_CACHE_SECONDS` (default 30), so every member shares one upstream call per window.
Market-hours gating is not built yet.
Operators: `>`, `>=`, `<`, `<=`, `==`, `!=`, `between` (inclusive pair).

## Access model

- One market data account is owned by the admin. Its token lives only in the server's environment (`TRADIER_TOKEN`). It is never returned by any route and never sent to a browser.
- Members do not connect brokers. They sign in to the app and receive quotes, chains, and screens from the server.
- Members can build their own strategies, screens, and alerts. Those stay in their account.

**Not built yet, and required before any member sees the app:**
1. Member sign-in and sessions. Every `/api` route currently allows anyone who can reach the port.
2. Per-member storage for watchlists, screens, alerts, and profile defaults. Today they live in the browser.
3. Rate limits per member, so one user can't use up the shared account's request quota.
4. Confirm with Tradier that the API terms allow serving its data to paying members of your app. Market data redistribution is often restricted by vendor and exchange agreements, and sharing strategy output may raise investment-advice rules. Check both before launch.

Until item 1 is done, run the server on `127.0.0.1` only.

## Layout

- `tape_app/providers/`: data provider contract, Tradier adapter, mock feed
- `tape_app/screener.py`: filter, sort, and validation engine
- `tape_app/server.py`: HTTP routes and the live tape stream
- `tests/`: unit and API tests
