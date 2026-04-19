"""
Risk management module.
Enforces daily loss limits, position size limits, and trade count limits.
Per-user: each user has their own RiskManager with limits from their settings.
"""
from datetime import date
from core.logger import get_logger
from config import settings

logger = get_logger("risk")


class RiskManager:
    """
    Checks every order against risk rules before execution.
    Strategies call broker → broker calls risk_manager.check() → approve/reject.
    """

    def __init__(
        self,
        max_loss_per_day: float = None,
        max_trades_per_day: int = None,
        max_position_size: float = None,
        max_single_order_value: float = None,
    ):
        self._today = date.today()
        self._daily_pnl: float = 0.0
        self._daily_trade_count: int = 0
        self._open_position_value: float = 0.0
        # Per-user limits (fall back to global defaults)
        self.max_loss_per_day = max_loss_per_day if max_loss_per_day is not None else settings.MAX_LOSS_PER_DAY
        self.max_trades_per_day = max_trades_per_day if max_trades_per_day is not None else settings.MAX_TRADES_PER_DAY
        self.max_position_size = max_position_size if max_position_size is not None else settings.MAX_POSITION_SIZE
        self.max_single_order_value = max_single_order_value if max_single_order_value is not None else settings.MAX_SINGLE_ORDER_VALUE

    def reset_if_new_day(self):
        if date.today() != self._today:
            self._today = date.today()
            self._daily_pnl = 0.0
            self._daily_trade_count = 0
            self._open_position_value = 0.0
            logger.info("Risk counters reset for new day.")

    def pre_order_check(self, symbol: str, qty: int, price: float, side: str) -> tuple[bool, str]:
        """
        Returns (approved: bool, reason: str).
        Called before every order placement.
        """
        self.reset_if_new_day()

        # Check daily loss limit
        if self._daily_pnl <= -self.max_loss_per_day:
            msg = f"BLOCKED: Daily loss limit hit ({self._daily_pnl:.2f})"
            logger.warning(msg)
            return False, msg

        # Check trade count
        if self._daily_trade_count >= self.max_trades_per_day:
            msg = f"BLOCKED: Max trades/day reached ({self._daily_trade_count})"
            logger.warning(msg)
            return False, msg

        # Check single order value
        order_value = qty * price if price > 0 else 0
        if order_value > self.max_single_order_value and price > 0:
            msg = f"BLOCKED: Order value {order_value:.0f} > limit {self.max_single_order_value:.0f}"
            logger.warning(msg)
            return False, msg

        # Check total position exposure
        if self._open_position_value + order_value > self.max_position_size and price > 0:
            msg = f"BLOCKED: Total exposure would exceed {self.max_position_size:.0f}"
            logger.warning(msg)
            return False, msg

        return True, "OK"

    def record_trade(self, qty: int, price: float, side: str):
        """Call after a trade is executed."""
        self._daily_trade_count += 1
        value = qty * price if price > 0 else 0
        if side == "BUY":
            self._open_position_value += value
        else:
            self._open_position_value -= value

    def update_pnl(self, pnl: float):
        """Update running daily P&L."""
        self._daily_pnl = pnl

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def daily_trade_count(self) -> int:
        return self._daily_trade_count

    @property
    def is_trading_allowed(self) -> bool:
        self.reset_if_new_day()
        if self._daily_pnl <= -self.max_loss_per_day:
            return False
        if self._daily_trade_count >= self.max_trades_per_day:
            return False
        return True


# ── Per-user risk managers ──────────────────────────
_user_risk_managers: dict[int, RiskManager] = {}

# Legacy singleton (for CLI/engine usage)
_risk_manager = None


def get_risk_manager() -> RiskManager:
    """Legacy global risk manager for backward compat."""
    global _risk_manager
    if _risk_manager is None:
        _risk_manager = RiskManager()
    return _risk_manager


def get_user_risk_manager(user_id: int) -> RiskManager:
    """Return a per-user RiskManager, creating one if needed with user's settings."""
    if user_id not in _user_risk_managers:
        # Try loading user-specific limits from DB
        try:
            from core.database import get_db_session
            from core.models import UserSettings
            db = get_db_session()
            try:
                us = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
                if us:
                    _user_risk_managers[user_id] = RiskManager(
                        max_loss_per_day=float(us.max_loss_per_day or settings.MAX_LOSS_PER_DAY),
                        max_trades_per_day=us.max_trades_per_day or settings.MAX_TRADES_PER_DAY,
                        max_position_size=float(us.max_position_size or settings.MAX_POSITION_SIZE),
                        max_single_order_value=float(us.max_single_order_value or settings.MAX_SINGLE_ORDER_VALUE),
                    )
                else:
                    _user_risk_managers[user_id] = RiskManager()
            finally:
                db.close()
        except Exception:
            _user_risk_managers[user_id] = RiskManager()
    return _user_risk_managers[user_id]
