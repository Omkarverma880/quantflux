# AutoTrader — Setup & Run Guide

## Prerequisites

- Python 3.10+
- Node.js 18+ & npm
- Zerodha Kite Connect API credentials (API Key + Secret)

---

## Step 1 — Install Python Dependencies

```bash
cd broker_integration
pip install -r requirements.txt
```

---

## Step 2 — Install Frontend Dependencies

```bash
cd frontend
npm install
```

---

## Step 3 — Configure Environment

Open `.env` in the project root and set your Zerodha credentials:

```
KITE_API_KEY=your_api_key
KITE_API_SECRET=your_api_secret
```

Other settings (already have sensible defaults):

| Variable              | Default  | Description                    |
|-----------------------|----------|--------------------------------|
| PAPER_TRADE           | true     | Set `false` for live trading   |
| TRADING_ENABLED       | false    | Set `true` to allow orders     |
| MAX_LOSS_PER_DAY      | 5000     | Daily loss limit (₹)          |
| MAX_TRADES_PER_DAY    | 20       | Max trades per day             |
| ACTIVE_STRATEGIES     | (empty)  | Comma-separated strategy names |

---

## Step 4 — Start the Backend Server

Open **Terminal 1**:

```bash
cd broker_integration
python main.py server
```

This starts the FastAPI server at `http://localhost:8000`.

---

## Step 5 — Start the Frontend Dev Server

Open **Terminal 2**:

```bash
cd broker_integration/frontend
npm run dev
```

This starts Vite dev server at `http://localhost:5173`.

---

## Step 6 — Open the App

Go to **http://localhost:5173** in your browser.

You should see the AutoTrader dashboard with sidebar navigation:
- **Dashboard** — Account overview, engine controls
- **Cum. Volume** — Strategy 1 (shows demo data immediately, no login needed)
- **Strategies** — Registered strategy list
- **Orders** — Positions, holdings, order history
- **Settings** — Configuration info

---

## Step 7 — Login to Zerodha (for live data)

1. Click **"Login to Zerodha"** in the sidebar
2. Authenticate in the popup window
3. Once logged in, the status shows your name + user ID

After login, Strategy 1 (Cumulative Volume) will switch from demo data to live NIFTY Futures data.

---

## Step 8 — Configure Strategy 1 (Cumulative Volume)

Before live data works, set the correct futures instrument token:

1. Go to **Cum. Volume** page
2. Click the **⚙ Settings** icon (top right)
3. Set:
   - **Futures Instrument**: e.g. `NFO:NIFTY26APRFUT`
   - **Futures Token**: the integer instrument token from Zerodha (e.g. `10694658`)
   - **Spot Instrument**: `NSE:NIFTY 50`
   - **Threshold**: `50000` (default)
4. Click **Save & Reload**

> **How to find the token**: After login, call `GET http://localhost:8000/api/dashboard/ltp?instruments=NFO:NIFTY26APRFUT` — or use Kite's instrument dump.

---

## Quick Reference — CLI Commands

```bash
python main.py server      # Start web server
python main.py login       # CLI Zerodha login
python main.py run         # Start trading engine (CLI)
python main.py status      # Show account/margin status
python main.py strategies  # List registered strategies
```

---

## Troubleshooting

| Problem                          | Fix                                                     |
|----------------------------------|----------------------------------------------------------|
| Frontend blank page              | Run `npm install` in `frontend/`, then `npm run dev`     |
| "Broker not authenticated"       | Login via sidebar or `python main.py login`              |
| Strategy 1 shows "Demo Data"     | Set valid `futures_token` in the config panel             |
| No data on weekend               | Expected — app shows last Friday's data automatically    |
| Port 8000 already in use         | Kill the other process or change port in `main.py`       |
