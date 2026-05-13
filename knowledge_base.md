# QuantFlux — Knowledge Base

> Internal reference doc for the QuantFlux algorithmic trading platform.
> Use this to orient quickly before making changes or diagnosing bugs.
> Keep updated as the codebase evolves.

---

## 1. What is QuantFlux?

A multi-user, multi-strategy options/equity trading platform for Indian
markets, wrapping **Zerodha KiteConnect** behind a FastAPI backend with
a React + Tailwind dashboard. Supports both fully automated strategy
execution and a discretionary **Manual Trading** workspace, with
shared risk controls, paper-trade mode, and persistent state across
restarts.

**Key capabilities**
- Per-user Zerodha OAuth (tokens stored encrypted in PostgreSQL).
- 9 strategies + a CV (cumulative volume) analytics module.
- Background asyncio loop runs strategy `.check()` every 1s during
  market hours for every authenticated user.
- Local SL/TGT monitor for manual trades (no resting OCO at the broker
  — exits triggered locally on LTP).
- WebSocket broadcasts strategy state to the frontend.
- Paper-trade mode at the broker layer (transparent to strategies).
- Alembic migrations for DB schema.

---

## 2. Tech Stack

| Layer | Tech |
|---|---|
| Runtime | Python 3.11+, Node 18+ |
| Backend | FastAPI, Uvicorn, SQLAlchemy 2.x, Alembic, Pydantic |
| Auth | bcrypt (app passwords), python-jose (JWT), Fernet (token-at-rest) |
| Broker | `kiteconnect>=5.0` (REST + KiteTicker WebSocket) |
| DB | PostgreSQL (psycopg2-binary). Railway-friendly URL fixup. |
| Frontend | React 18, Vite 5, Tailwind 3, Recharts, lucide-react, react-router-dom 6 |
| Deploy | Dockerfile + `railway.toml`. `PORT`, `APP_URL` env-driven. |

Python deps: `requirements.txt`. JS deps: `frontend/package.json`.

---

## 3. Repository Layout

```
quantflux/
├─ main.py                      # CLI entry: server | run | status | strategies
├─ alembic.ini                  # Alembic config
├─ alembic/                     # Migrations (single revision: 373326eef00d initial_schema)
├─ Dockerfile / railway.toml    # Deploy
├─ gann_levels.csv              # Static Gann level grid used by S1/S2/S4/S5
├─ requirements.txt
├─ application_documentation.html / strategy_documentation.html  # Static docs
├─ steps.md / README.md         # Operator notes
├─ config/
│  └─ settings.py               # Env-loaded config (single source of truth)
├─ core/
│  ├─ auth.py                   # JWT + per-user Zerodha auth
│  ├─ broker.py                 # KiteConnect wrapper + paper-trade
│  ├─ database.py               # SQLAlchemy engine + Session factory
│  ├─ encryption.py             # Fernet wrappers
│  ├─ logger.py                 # Centralised logger
│  ├─ models.py                 # ORM (User, UserSettings, ZerodhaSession, ...)
│  ├─ risk_controller.py        # Per-strategy risk gating (S6/7/8/9)
│  └─ risk_manager.py           # Singleton daily PnL / trade caps
├─ engine/
│  └─ trading_engine.py         # CLI-driven KiteTicker engine (legacy path)
├─ strategies/
│  ├─ base_strategy.py          # ABC + StrategyConfig
│  ├─ registry.py               # STRATEGY_MAP {name → class}
│  ├─ cumulative_volume.py      # CV analytics (non-trading)
│  ├─ strategy1_gann_cv.py      # S1: Gann floor + CV → BUY ATM CE/PE
│  ├─ strategy2_option_sell.py  # S2: Gann ceiling + CV → SELL ATM PE/CE
│  ├─ strategy3_cv_vwap_ema_adx.py  # S3: 3-phase trend strategy
│  ├─ strategy4_high_low_retest.py  # S4: prev-day 9:15-10:15 H/L retest
│  ├─ strategy5_gann_range.py   # S5: dynamic Gann range retest
│  ├─ strategy6_call_put_lines.py  # S6: spot line touch (CE/PE)
│  ├─ strategy7_strike_lines.py # S7: strike-LTP line touch
│  ├─ strategy8_reverse.py      # S8: S7 with reversed direction
│  ├─ strategy9_loc.py          # S9: 6-line "Line of Control"
│  ├─ backtest_engine.py        # Vectorised replayer (used by /api/strategies/backtest)
│  └─ example_ma_crossover.py   # Demo template
├─ app/
│  ├─ server.py                 # FastAPI app + lifespan + bg loop + static
│  ├─ websocket_manager.py      # Singleton ConnectionManager
│  └─ routes/
│     ├─ auth_routes.py         # /api/auth — login/register/Kite OAuth
│     ├─ dashboard_routes.py    # /api/dashboard — summary + LTP
│     ├─ trading_routes.py      # /api/trading — legacy auto-engine controls
│     ├─ strategy_routes.py     # /api/strategies — registry + backtest
│     ├─ portfolio_routes.py    # /api/portfolio — holdings, watchlists, exit-levels
│     ├─ settings_routes.py     # /api/settings — per-user config + Kite creds
│     ├─ cumulative_volume_routes.py # /api/strategy1 — CV data + config
│     ├─ strategy1_routes.py … strategy9_routes.py  # Per-strategy CRUD/control
│     └─ manual_trading_routes.py    # /api/manual — discretionary trading
└─ frontend/
   ├─ vite.config.js / tailwind.config.js / postcss.config.js
   ├─ index.html
   ├─ public/
   └─ src/
      ├─ main.jsx, App.jsx       # Router + auth gate
      ├─ api.js                  # axios instance with JWT header
      ├─ AuthContext.jsx         # JWT + login state
      ├─ ThemeContext.jsx, ToastContext.jsx
      ├─ index.css               # Tailwind layers + design tokens
      ├─ components/
      │  ├─ Layout.jsx           # Sidebar + topbar shell
      │  ├─ ErrorBoundary.jsx
      │  ├─ QuantFluxLogo.jsx
      │  ├─ BacktestPanel.jsx    # Generic backtest UI
      │  └─ RiskPanel.jsx        # Reusable risk-controller settings UI
      └─ pages/
         ├─ Dashboard.jsx, Login.jsx, Onboarding.jsx, Settings.jsx
         ├─ Strategies.jsx, Strategy1.jsx … Strategy9.jsx
         ├─ CumulativeVolume.jsx, Orders.jsx, TradeHistory.jsx
         ├─ ManualTrading.jsx
         ├─ PortfolioAnalytics.jsx, AnalyticsWorld.jsx
```

---

## 4. Configuration & Environment Variables

All read from `.env` via `python-dotenv` in `config/settings.py`:

| Var | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:1605@localhost:5432/quantflux_db` | SQLAlchemy URL. Auto-rewrites Railway's `postgres://` → `postgresql://`. |
| `LOG_LEVEL` | `INFO` | Logging level. |
| `TRADING_ENABLED` | `false` | Master switch; respected by trading engine. |
| `PAPER_TRADE` | `true` | If true, broker layer fakes orders. |
| `MAX_LOSS_PER_DAY` | `5000` | Hard daily loss cap (RiskManager). |
| `MAX_TRADES_PER_DAY` | `20` | Daily trade cap. |
| `MAX_POSITION_SIZE` | `100000` | Per-position notional cap. |
| `MAX_SINGLE_ORDER_VALUE` | `50000` | Per-order notional cap. |
| `AUTO_SQUARE_OFF_TIME` | `15:15` | Pre-close exit time (used by S3, S4, S5, etc.). |
| `SECRET_KEY` | `change-me-in-production` | JWT signing key. Also used for Fernet derivation in `core/encryption.py`. |
| `PORT` | `8000` | Uvicorn port. |
| `APP_URL` | `http://localhost:8000` | Public URL (Railway). |
| `CORS_ORIGIN_REGEX` | localhost + `*.up.railway.app` | CORS regex. |
| `ACTIVE_STRATEGIES` | `""` | CSV used by CLI `python main.py run`. |
| `DEV_MODE` | `false` | Enables Uvicorn `--reload`. |

Persistence root: `data/` (auto-created).

---

## 5. Data Persistence

### PostgreSQL (via Alembic — `alembic/versions/373326eef00d_initial_schema.py`)
- `users` — app users (bcrypt password).
- `user_settings` — Kite creds (encrypted) + risk caps + active strategies CSV.
- `zerodha_sessions` — daily encrypted access_token, unique per `(user_id, login_date)`.
- `strategy_configs` — JSONB per `(user_id, strategy_name)`.
- `strategy_states` — JSONB per `(user_id, strategy_name)`, includes `trading_date`.
- `trade_logs` — historical per-strategy trades (entry/exit/PnL).
- `order_history` — every order placed.

### Filesystem (`data/`)
- `data/tokens/access_token.json` — legacy single-user token cache.
- `data/strategy_configs/strategyN_state.json` — per-strategy state snapshot
  (in addition to DB) for fast restart-resume.
- `data/trade_history/strategyN_trades.json` — per-strategy trade log.
- `data/trade_history/order_history.json` — shared order log.
- `data/trade_history/manual/<YYYY-MM-DD>.json` — daily manual-trade log.
- `data/manual_monitor_state.json` — persisted SL/TGT monitor for
  manual trades (so it survives restarts mid-session).
- `logs/` — rotating log files.

---

## 6. Auth Flow

Two layers, both in `core/auth.py`:

1. **App auth (JWT, multi-user)**
   - `POST /api/auth/register` and `POST /api/auth/app-login` issue
     a 24h `HS256` JWT signed with `SECRET_KEY`.
   - Frontend stores it in `localStorage['app_token']` (see
     `AuthContext.jsx`) and sends as `Authorization: Bearer <token>`.
   - Dependency chain: `oauth2_scheme → get_current_user_id → login_required(user_id)`.
   - `POST /api/auth/forgot-password` + `POST /api/auth/reset-password`
     uses a 15m JWT with `purpose=password_reset`.

2. **Zerodha auth (per-user OAuth)** — `UserZerodhaAuth`
   - Settings page stores `kite_api_key` + Fernet-encrypted `kite_api_secret`.
   - `GET /api/auth/login` builds Kite login URL with `redirect_params=user_id=<id>`.
   - `GET /api/auth/callback` calls `kite.generate_session(...)`, encrypts
     `access_token`, upserts `zerodha_sessions` for today.
   - `get_kite_for_user(db, user_id)` returns a cached `KiteConnect`
     instance with the day's access token applied. Cache:
     `_user_kite_instances: dict[int, KiteConnect]`.
   - `is_authenticated(db, user_id)` = a row exists for today.

Per-user broker cache: `core/broker.py` keeps `_user_brokers: dict[int, Broker]`.
`get_user_broker(db, user_id)` returns/creates a `Broker(per_user=True)`
and re-attaches today's kite handle on every call. **Manual trading's
`/positions` endpoint clears this cache on transient errors to recover
from a stale token (see fix in §11.)**

---

## 7. Core Modules

### `core/database.py`
SQLAlchemy engine (`pool_size=10`, `max_overflow=20`, `pool_pre_ping=True`).
- `get_db()` — FastAPI dependency (yield + close).
- `get_db_session()` — caller-managed (used by background tasks).

### `core/models.py`
ORM models listed in §5. `JSONB` used for `strategy_configs.config`,
`strategy_states.state`, `trade_logs.extra`.

### `core/auth.py`
JWT helpers + `UserZerodhaAuth` (per-user Kite). See §6.

### `core/encryption.py`
Fernet wrappers (`encrypt_value`, `decrypt_value`) — derives a Fernet
key from `SECRET_KEY` (rotate carefully, would invalidate stored tokens).

### `core/broker.py`
- Enums: `OrderSide`, `OrderType`, `ProductType`, `Exchange`.
- Dataclasses: `OrderRequest`, `OrderResponse`, `Position`, `Holding`.
- `Broker` class — wraps KiteConnect; supports `PAPER_TRADE` mode
  (paper orders held in `_paper_orders`).
- Singletons: `get_broker()` (legacy single-user) and
  `get_user_broker(db, user_id)` (multi-user — preferred).

### `core/risk_manager.py`
Process-singleton tracking of daily PnL + trade count. Resets at
midnight. Used by automated engine.

### `core/risk_controller.py`
Per-strategy gating used by **S6/S7/S8/S9**. States:
`ACTIVE / COOLDOWN / PAUSED_AFTER_SL / AWAITING_CONFIRMATION / HALTED`.
Caps re-entries, requires fresh crossover after SL, etc. Strategies
own one instance and call `allow_entry / record_entry / record_exit /
update_price_for_arming`.

### `core/logger.py`
`get_logger(name)` — file (rotated under `logs/`) + stdout. Logger
names: `auth`, `broker`, `database`, `engine`, `server`, `websocket`,
`api.<area>`, `strategy.<name>`, `risk_controller`, `strategy<n>.*`.

---

## 8. Strategies

All live in `strategies/`, registered in `strategies/registry.py`
(`STRATEGY_MAP`). Each has matching `app/routes/strategy<n>_routes.py`
mounted at `/api/strategy<n>-trade` and a React page
`frontend/src/pages/Strategy<n>.jsx`.

| # | Class | File | Concept |
|---|---|---|---|
| CV | `CumulativeVolumeStrategy` | `cumulative_volume.py` | Non-trading. Signed-volume + cumulative volume from 9:15. Powers `/api/strategy1/data`. |
| 1 | `Strategy1GannCV` | `strategy1_gann_cv.py` | CV ≥ +/− threshold → BUY ATM CE/PE at the **floor Gann level**. SL/TGT in points. 1 trade/day. |
| 2 | `Strategy2OptionSell` | `strategy2_option_sell.py` | Inverse — SELL ATM CE/PE at **ceiling Gann level**. SL above entry, TGT below. |
| 3 | `Strategy3CvVwapEmaAdx` | `strategy3_cv_vwap_ema_adx.py` | 3-phase: trend (EMA200+VWAP+ADX+|CV|), pullback to EMA20, breakout candle. Bidirectional. |
| 4 | `Strategy4HighLowRetest` | `strategy4_high_low_retest.py` | Yesterday's 9:15-10:15 H/L → retest hold/reject + fakeout. ATM at MARKET. |
| 5 | `Strategy5GannRange` | `strategy5_gann_range.py` | Dynamic Gann range (floor/ceil around spot). Retest/fakeout at the active range, ITM CE/PE. |
| 6 | `Strategy6CallPutLines` | `strategy6_call_put_lines.py` | Two user-drawn lines on NIFTY spot. Direct touch entry. ITM CE/PE. Uses `RiskController`. |
| 7 | `Strategy7StrikeLines` | `strategy7_strike_lines.py` | Lines on **option strike LTPs** (5 above/below ATM). Direct touch, BUY same strike. |
| 8 | `Strategy8Reverse` | `strategy8_reverse.py` | Mirror of S7 with reversed direction (CE line hit → buy PUT). AUTO/MANUAL reverse-strike modes (200pt ITM default). |
| 9 | `Strategy9LOC` | `strategy9_loc.py` | "Line of Control": 6 user lines (BUY/TGT/SL × CE/PE). One-cycle rule, lines editable mid-trade. |

Common patterns
- States: `IDLE → ORDER_PLACED → POSITION_OPEN → COMPLETED` (extra
  states in S4/S5 for breakout watch).
- Persistence: each strategy reads/writes `strategy<n>_state.json` AND
  `strategy_states` table.
- Auto square-off: `PRE_CLOSE_EXIT = 15:15` (configurable in S3 via
  `AUTO_SQUARE_OFF_TIME`).
- Shadow SL/TGT: levels stored locally; promoted to real broker
  orders when LTP gets close (avoids broker rejecting unrealistic SL).
- All use `gann_levels.csv` where Gann logic is involved (S1, S2, S4, S5).

`backtest_engine.py` — vectorised replayer used by `POST
/api/strategies/backtest`; consumed by `BacktestPanel.jsx`.

`base_strategy.py` (`BaseStrategy` ABC) is used by the **legacy CLI
engine** (`engine/trading_engine.py`). Strategies S1–S9 above are
plain classes with their own lifecycle; the FastAPI server wires them
to the background loop directly via the `_get_strategy(broker, uid)`
helper inside each `app/routes/strategyN_routes.py`.

---

## 9. Engine & Background Loop

Two execution paths:

1. **CLI `python main.py run`** → `engine.trading_engine.TradingEngine`.
   - Connects to KiteTicker, subscribes to instruments, distributes
     ticks to `BaseStrategy.on_tick`. Handles 15:15 squareoff.
   - Used for the legacy `BaseStrategy` style (`example_ma_crossover.py`).

2. **`python main.py server`** → FastAPI app.
   - `app/server.py` registers a lifespan task `_strategy_background_loop`.
   - Every `STRATEGY_CHECK_INTERVAL = 1s` during market hours (Mon–Fri,
     9:15–15:30), it fetches all users with a `ZerodhaSession` for
     today and calls `_run_strategies_for_user(uid)` in a thread executor.
   - `_run_strategies_for_user` builds the broker, resolves CV data
     (shared cache), then drives each strategy's `.check(...)` method.
   - After every cycle, it broadcasts a `strategy_update` WebSocket
     event with the FULL `get_status()` payload (datetime/Enum-safe via
     `json.dumps(default=str)`).

Strategies use threading.Lock internally for state mutation (since
checks run in executor threads).

---

## 10. API Surface (router prefixes)

Mounted in `app/server.py`:

| Prefix | File | Highlights |
|---|---|---|
| `/api/auth` | `auth_routes.py` | `register`, `app-login`, `me`, `onboard`, Kite `login`, `callback`, `status`, `logout`, `forgot-password`, `reset-password` |
| `/api/dashboard` | `dashboard_routes.py` | `summary` (margins+positions+orders, swallows errors), `ltp` |
| `/api/trading` | `trading_routes.py` | Legacy auto-engine controls |
| `/api/strategies` | `strategy_routes.py` | Registry list, generic backtest |
| `/api/portfolio` | `portfolio_routes.py` | Holdings, exit-levels, watchlists |
| `/api/settings` | `settings_routes.py` | User settings + Kite creds CRUD |
| `/api/strategy1` | `cumulative_volume_routes.py` | CV data + config |
| `/api/strategy1-trade … strategy9-trade` | `strategyN_routes.py` | Per-strategy `status`, `start`, `stop`, `config`, `state` |
| `/api/manual` | `manual_trading_routes.py` | See §11 |
| `/ws` | `websocket_manager.py` | Single endpoint; `event` types include `strategy_update` |

Static frontend served from `frontend/dist` (built with `npm run build`).

---

## 11. Manual Trading Subsystem (`/api/manual` + `ManualTrading.jsx`)

**Backend file:** `app/routes/manual_trading_routes.py`
**Frontend page:** `frontend/src/pages/ManualTrading.jsx`

### Endpoints
| Method | Path | Purpose |
|---|---|---|
| GET | `/auth_status` | Zerodha auth + profile |
| GET | `/option_setup` | Single-side option chain (CE or PE) |
| GET | `/option_setup_all` | CE + PE option chain in one call |
| POST | `/preload_instruments` | Warm instrument cache for NFO + BFO |
| POST | `/invalidate_cache` | Force re-download of instruments |
| POST | `/order` | Place manual order (with optional SL/TGT/trailing) |
| GET | `/positions` | Active positions + live LTP/PnL (resilient — see below) |
| POST | `/squareoff` | MARKET exit a position |
| GET | `/open_orders` | Pending + trigger-pending orders |
| GET | `/orders` | Full order book |
| POST | `/order/modify` | Modify an order's price |
| POST | `/order/cancel` | Cancel an order |
| GET | `/trade_logs` | Manual log file contents (today or `?log_date=all`) |
| GET | `/pnl` | Aggregated manual PnL |
| GET | `/margins` | Available margin |
| GET | `/monitor/status` | SL/TGT monitor snapshot |
| POST | `/monitor/unregister` | Remove a symbol from monitor |

### Instrument cache
`_get_cached_instruments(exchange, broker)` — daily TTL (date-based)
keyed by exchange. Avoids re-downloading the ~100K NFO instrument list
each request. Cleared via `_invalidate_instrument_cache()`.

### Index/exchange map
- `NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY` → `NFO`
- `SENSEX/BANKEX` → `BFO`
- Spot map: `NIFTY` = `NSE:NIFTY 50`, `SENSEX` = `BSE:SENSEX`.

### Order placement flow
1. `place_manual_order` resolves the trading symbol (manual override or
   index+strike+option_type → nearest expiry from instruments cache).
2. Optionally splits qty into iceberg legs and places one order per leg.
3. Appends an entry to today's `data/trade_history/manual/<DATE>.json`.
4. If `stop_loss > 0` or `target > 0`, schedules the async
   `_verify_fill_and_register(...)` task.

### `_verify_fill_and_register` (FIXED — see §13)
- Polls `broker.get_orders()` every **2 s** for up to ~6 hours (or
  until each leg reaches a terminal status: `COMPLETE`, `REJECTED`,
  `CANCELLED`).
- Aggregates filled value/qty across legs (handles partial fills).
- Only registers the SL/TGT monitor when at least some quantity is
  confirmed `COMPLETE`. Never uses fallback prices.

### `_ManualTradeMonitor` (singleton)
- `register(...)` computes absolute SL and TGT prices (PERCENT or POINTS)
  rounded to nearest 0.05 tick, stores per-symbol in `self._trades`,
  persists to `data/manual_monitor_state.json`, and ensures the async
  loop is running.
- `_monitor_loop` runs while there are trades, sleeping 1 s between
  checks. Skips outside 9:15–15:30.
- `_check_once` groups trades by `user_id`, fetches LTP for that user's
  instruments in one call, then `_process_ltp` evaluates each trade.
- `_process_ltp` updates `best_price`, optionally trails SL, optionally
  moves SL to cost at 50 % of TGT, then checks SL / TGT hit.
- On hit: **NEW** position-existence guard verifies the symbol is
  actually held in the broker account before exiting. If qty = 0 (entry
  never filled) the trade is unregistered and marked `SKIPPED_*_NO_POSITION`.
  If the live qty is less than monitored qty, the exit qty is capped.
- Up to `MAX_EXIT_RETRIES = 3` MARKET exit attempts; on failure status
  becomes `FAILED_EXIT_<reason>` and a CRITICAL log is emitted.

### Frontend (`ManualTrading.jsx`)
- `ManualOrderForm` — fields: index/option_type/strike/entry_price,
  side, qty (auto-computed from `trade_amount/entry_price` rounded to
  lot multiples), product, order type, iceberg legs, SL/TGT (POINTS or
  PERCENT), trailing SL toggle, "Move SL to Cost" toggle.
- `ManualActivePositions` — auto-refreshes every 5 s during market
  hours, shows LTP/PnL/SL/TGT/monitor badge, "Square Off" button.
- `ManualOpenOrders` — same cadence; cancel button.
- `ManualTradeLogs` — reads today's JSON log.
- Custom events used as a refresh bus:
  `refreshManualPositions`, `refreshManualOpenOrders`, `refreshManualTradeLogs`.

---

## 12. WebSocket

`ConnectionManager` singleton (`ws_manager`) in `app/websocket_manager.py`.
- Single endpoint `/ws` accepts connections (no auth handshake yet).
- Broadcast envelope: `{ event, data, timestamp }`.
- Events emitted today:
  - `strategy_update` — full per-strategy `get_status()` for s1..s9
    (1s cadence during market hours).
- Frontend pages subscribe and merge into local state to keep the UI
  responsive without polling REST endpoints in tight loops.

---

## 13. Bug History & Fixes

### 2026-05-13 — Manual Trading positions endpoint flickering (Bug 1)
**Symptom:** "Error loading positions: Invalid token" appearing every
few seconds, table flashing in/out.
**Root cause:** `/api/manual/positions` raised `HTTPException 500` on
any transient broker error (Kite occasionally returns "Invalid token"
under load / token-cache contention). The frontend cleared `positions`
on every error and re-rendered the empty state.
**Fix (`app/routes/manual_trading_routes.py`):**
- Wrap `broker.get_positions()` in retry-once-with-cache-evict logic.
- Never raise 500 — return `{positions: [], warning: "..."}` so the UI
  can keep its previous snapshot.
- LTP enrichment failures already non-fatal; left intact.
**Fix (`frontend/src/pages/ManualTrading.jsx`):**
- `inFlight` ref prevents overlapping requests when the 5 s interval
  fires before the previous request returned.
- On failure, do NOT clear `positions`; only show error banner after
  3 consecutive failures or if no successful load has ever happened.
- Render condition allows table to remain visible alongside an error
  banner.
- Same hardening applied to `ManualOpenOrders`.

### 2026-05-13 — SL/TGT exit fired before entry filled (Bug 2)
**Symptom:** A LIMIT BUY at 361 stayed OPEN; when LTP touched the
target the monitor pushed a SELL that was REJECTED (no underlying
position).
**Root cause:** `_verify_fill_and_register` polled for only 15 s and
then **fell back to `fallback_entry_price` (the user-supplied limit
price) and registered the monitor with the requested quantity** even
though no fill had occurred. The monitor then fired an exit on TGT.
**Fix (`app/routes/manual_trading_routes.py`):**
- `_verify_fill_and_register` rewritten:
  - Polls every 2 s for up to ~6 h (or until market close).
  - Tracks each leg until terminal status (`COMPLETE / REJECTED / CANCELLED`).
  - Aggregates filled qty + avg price across legs (partial-fill safe).
  - **Only registers the monitor when at least some quantity is COMPLETE.**
    Never uses fallback prices.
- `_ManualTradeMonitor._process_ltp` got a position-existence guard:
  before sending an exit it calls `broker.get_positions()` and skips
  + unregisters when no position exists; caps exit qty to the live
  position size.

**About the related question** ("if LTP nears TGT but reverses to SL,
will TGT be cancelled?"): SL and TGT are not separate broker orders —
they are local price thresholds checked every second on LTP. There is
nothing to cancel. Whichever level the LTP touches first triggers the
single MARKET exit. Already correct.

---

## 14. Common Operations

### Run dev backend
```
python main.py server                 # http://localhost:8000
DEV_MODE=true python main.py server   # with auto-reload
```

### Run frontend dev
```
cd frontend
npm install
npm run dev                           # vite dev server, proxied to backend
```

### DB migrations
```
alembic upgrade head
alembic revision --autogenerate -m "msg"
```

### List / inspect strategies
```
python main.py strategies
python main.py status
```

### Reset manual SL/TGT monitor state
Delete `data/manual_monitor_state.json` while the server is stopped.

---

## 15. Gotchas & Conventions

- **Tick size:** options round to 0.05 (`TICK = 0.05` in
  `manual_trading_routes.py`). Always use `_round_tick` before sending
  prices to broker.
- **Lot sizes:** option chains expose `lot_size`; manual form computes
  qty as `floor(trade_amount / entry_price)` rounded down to a lot
  multiple.
- **Time semantics:** strategy loop and monitor loop both gate on
  9:15–15:30 IST. `AUTO_SQUARE_OFF_TIME` (default 15:15) is honoured by
  S3+; others hard-code 15:15.
- **`get_user_broker` caching:** the cached `Broker` reuses a shared
  `KiteConnect` instance per user. Mutations like `set_access_token`
  happen on every retrieval — generally idempotent, but on transient
  "Invalid token" errors `_user_brokers.pop(user_id)` recovers cleanly
  (used by the new positions endpoint).
- **JSON serialisation for WS:** dataclasses / dates / Enums are
  flattened with `json.dumps(..., default=str)` before broadcast so
  the dashboard never receives non-JSON values.
- **Paper trade:** `Broker` short-circuits order placement when
  `settings.PAPER_TRADE` is true; orders live in `_paper_orders` only.
  All strategies remain unchanged.
- **CSV files:** `gann_levels.csv` is a flat list of integer levels,
  one per line. Sorted ascending after load.
- **Logger names:** `strategy<n>.<descriptor>` for trade strategies,
  `api.<area>` for routes, plain names for core modules. Useful for
  filtering log files.

---

_Last updated: 2026-05-13 — bugs in Manual Trading positions endpoint
and SL/TGT registration fixed; doc seeded._
