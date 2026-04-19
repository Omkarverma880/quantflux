# Zerodha Auto-Trading System

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure
Edit `.env` and set your API secret:
```
KITE_API_SECRET=your_api_secret_here
```

### 3. Login
```bash
python main.py login
```
This opens your browser for Zerodha login. You only do this once per day.

### 4. Run
```bash
python main.py run
```

## Commands
| Command | Description |
|---------|-------------|
| `python main.py login` | Login to Zerodha |
| `python main.py run` | Start trading engine |
| `python main.py status` | Show account & positions |
| `python main.py strategies` | List registered strategies |

## Adding a New Strategy

1. Create `strategies/my_strategy.py`:
```python
from strategies.base_strategy import BaseStrategy, StrategyConfig
from core.broker import Broker

class MyStrategy(BaseStrategy):
    def __init__(self, config: StrategyConfig, broker: Broker):
        super().__init__(config, broker)
        # read params from config.params dict

    def on_tick(self, tick_data: dict):
        # Your logic here
        # tick_data = {"NSE:SYMBOL": {last_price, volume, ...}}
        # Use self.buy("SYMBOL", qty=1) / self.sell("SYMBOL", qty=1)
        pass
```

2. Register it in `strategies/registry.py`:
```python
from strategies.my_strategy import MyStrategy
STRATEGY_MAP["my_strategy"] = MyStrategy
```

3. Add to `.env`:
```
ACTIVE_STRATEGIES=my_strategy
```

4. Optionally create `data/strategy_configs/my_strategy.json`:
```json
{
    "instruments": ["NSE:RELIANCE", "NSE:INFY"],
    "capital": 100000,
    "params": {"param1": "value1"}
}
```

## Project Structure
```
broker_integration/
├── main.py                     # CLI entry point
├── .env                        # Config (API keys, settings)
├── requirements.txt
├── config/
│   ├── __init__.py
│   └── settings.py             # All configuration
├── core/
│   ├── __init__.py
│   ├── auth.py                 # Zerodha login & token management
│   ├── broker.py               # Order placement, market data
│   ├── risk_manager.py         # Risk limits enforcement
│   └── logger.py               # Logging setup
├── engine/
│   ├── __init__.py
│   └── trading_engine.py       # Strategy orchestrator + tick router
├── strategies/
│   ├── __init__.py
│   ├── base_strategy.py        # Abstract base class for strategies
│   ├── registry.py             # Strategy name → class mapping
│   └── example_ma_crossover.py # Example strategy (demo)
├── data/
│   ├── tokens/                 # Access token storage (auto-created)
│   └── strategy_configs/       # Strategy JSON configs
└── logs/                       # Daily log files
```

## Safety Features
- **Paper trade mode** (default) — no real orders until you enable live trading
- **Daily loss limit** — stops trading if daily loss exceeds threshold
- **Max trades/day** — prevents runaway strategies
- **Position size limits** — caps exposure per order and total
- **Auto square-off** — closes all positions 15 min before market close
- **Token persistence** — login once per day, token is reused
