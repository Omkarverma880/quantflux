"""
Main entry point for the automated trading system.
Usage:
    python main.py server       # Start web dashboard + API server
    python main.py run          # Start trading engine (CLI only)
    python main.py status       # Show account status
    python main.py strategies   # List registered strategies
"""
import os
import sys
import json

# Ensure project root is in path
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import settings
from core.logger import get_logger

logger = get_logger("main")


def cmd_server():
    """Start the FastAPI web server (dashboard + API)."""
    import uvicorn

    print("\n" + "=" * 60)
    print("  QUANTFLUX — WEB DASHBOARD")
    print(f"  Mode    : {'PAPER TRADE' if settings.PAPER_TRADE else 'LIVE TRADE'}")
    print(f"  Reload  : {'ON' if os.getenv('DEV_MODE', 'false').lower() == 'true' else 'OFF'}")
    print(f"  API     : http://localhost:{settings.PORT}/api")
    print(f"  Frontend: http://localhost:5173  (run 'npm run dev' in frontend/)")
    print("=" * 60 + "\n")

    uvicorn.run(
        "app.server:app",
        host="0.0.0.0",
        port=settings.PORT,
        reload=os.getenv("DEV_MODE", "false").lower() == "true",
        log_level="info",
    )


def cmd_status():
    """Show account status."""
    from core.broker import get_broker
    broker = get_broker()
    broker.connect()

    margins = broker.get_margins()
    equity = margins.get("equity", {})
    print("\n── Account Status ──")
    print(f"  Available margin : ₹{equity.get('available', {}).get('live_balance', 0):,.2f}")
    print(f"  Used margin      : ₹{equity.get('utilised', {}).get('debits', 0):,.2f}")

    positions = broker.get_positions()
    if positions:
        print(f"\n── Open Positions ({len(positions)}) ──")
        for p in positions:
            if p.quantity != 0:
                print(f"  {p.tradingsymbol:15s} qty={p.quantity:>5d}  avg={p.average_price:.2f}  pnl={p.pnl:.2f}")
    else:
        print("  No open positions.")


def cmd_run():
    """Start the trading engine with configured strategies."""
    from engine.trading_engine import TradingEngine
    from strategies.registry import list_strategies

    print("\n" + "=" * 60)
    print("  AUTOMATED TRADING SYSTEM")
    print(f"  Mode: {'PAPER TRADE' if settings.PAPER_TRADE else 'LIVE TRADE'}")
    print("=" * 60 + "\n")

    engine = TradingEngine()

    strategy_configs = []
    for name in settings.ACTIVE_STRATEGIES:
        config_file = settings.DATA_DIR / "strategy_configs" / f"{name}.json"
        if config_file.exists():
            with open(config_file) as f:
                cfg = json.load(f)
            cfg["name"] = name
            strategy_configs.append(cfg)
        else:
            strategy_configs.append({"name": name, "instruments": [], "params": {}})

    if not strategy_configs:
        print("No strategies configured. Add strategy names to ACTIVE_STRATEGIES in .env")
        print(f"Available strategies: {list_strategies()}")
        sys.exit(0)

    engine.load_strategies(strategy_configs)
    engine.start()


def cmd_strategies():
    """List all registered strategies."""
    from strategies.registry import list_strategies
    strategies = list_strategies()
    if strategies:
        print("\n── Registered Strategies ──")
        for s in strategies:
            print(f"  • {s}")
    else:
        print("\nNo strategies registered yet.")
        print("Create a strategy in strategies/ and add it to STRATEGY_MAP in registry.py")


def cmd_help():
    print(__doc__)


COMMANDS = {
    "server": cmd_server,
    "run": cmd_run,
    "status": cmd_status,
    "strategies": cmd_strategies,
    "help": cmd_help,
}

if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "help"
    if command in COMMANDS:
        COMMANDS[command]()
    else:
        print(f"Unknown command: {command}")
        cmd_help()
