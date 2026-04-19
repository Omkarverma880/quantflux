"""
Strategy management API routes.
List, enable/disable, configure strategies.
"""
import json
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from config import settings
from strategies.registry import list_strategies, STRATEGY_MAP
from core.logger import get_logger

router = APIRouter()
logger = get_logger("api.strategies")


class StrategyConfigUpdate(BaseModel):
    instruments: list[str] = []
    capital: float = 100000
    max_positions: int = 5
    params: dict = {}


@router.get("/")
async def get_all_strategies():
    """List all registered strategies with their configs."""
    registered = list_strategies()
    active = settings.ACTIVE_STRATEGIES

    result = []
    for name in registered:
        config_file = settings.DATA_DIR / "strategy_configs" / f"{name}.json"
        config = {}
        if config_file.exists():
            try:
                config = json.loads(config_file.read_text())
            except json.JSONDecodeError:
                pass

        result.append({
            "name": name,
            "active": name in active,
            "config": config,
        })

    return {"strategies": result, "active": active}


@router.get("/{name}")
async def get_strategy(name: str):
    """Get a single strategy's config."""
    if name not in STRATEGY_MAP:
        return {"error": f"Strategy '{name}' not found"}

    config_file = settings.DATA_DIR / "strategy_configs" / f"{name}.json"
    config = {}
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text())
        except json.JSONDecodeError:
            pass

    return {
        "name": name,
        "active": name in settings.ACTIVE_STRATEGIES,
        "config": config,
    }


@router.put("/{name}/config")
async def update_strategy_config(name: str, config: StrategyConfigUpdate):
    """Update a strategy's configuration."""
    if name not in STRATEGY_MAP:
        return {"error": f"Strategy '{name}' not found"}

    config_dir = settings.DATA_DIR / "strategy_configs"
    config_dir.mkdir(parents=True, exist_ok=True)

    config_file = config_dir / f"{name}.json"
    config_data = config.model_dump()
    config_file.write_text(json.dumps(config_data, indent=2))

    logger.info(f"Updated config for strategy: {name}")
    return {"status": "updated", "config": config_data}


@router.post("/{name}/activate")
async def activate_strategy(name: str):
    """Add strategy to active list."""
    if name not in STRATEGY_MAP:
        return {"error": f"Strategy '{name}' not found"}

    if name not in settings.ACTIVE_STRATEGIES:
        settings.ACTIVE_STRATEGIES.append(name)
        _update_env_strategies()

    return {"status": "activated", "active_strategies": settings.ACTIVE_STRATEGIES}


@router.post("/{name}/deactivate")
async def deactivate_strategy(name: str):
    """Remove strategy from active list."""
    if name in settings.ACTIVE_STRATEGIES:
        settings.ACTIVE_STRATEGIES.remove(name)
        _update_env_strategies()

    return {"status": "deactivated", "active_strategies": settings.ACTIVE_STRATEGIES}


def _update_env_strategies():
    """Persist ACTIVE_STRATEGIES back to .env file."""
    env_file = settings.BASE_DIR / ".env"
    if not env_file.exists():
        return

    lines = env_file.read_text().splitlines()
    new_value = ",".join(settings.ACTIVE_STRATEGIES)
    updated = False

    for i, line in enumerate(lines):
        if line.startswith("ACTIVE_STRATEGIES="):
            lines[i] = f"ACTIVE_STRATEGIES={new_value}"
            updated = True
            break

    if not updated:
        lines.append(f"ACTIVE_STRATEGIES={new_value}")

    env_file.write_text("\n".join(lines) + "\n")
