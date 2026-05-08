"""
RiskController — shared per-strategy risk management engine.

Used by Strategy 6 / 7 / 8 / 9 to enforce:

  - Configurable re-entry rules after TARGET vs SL exits
  - Cooldown window between exit and the next allowed entry
  - "Fresh crossover" requirement (price must move sufficiently away
     from the trigger line and re-cross before another entry can fire)
  - Daily caps: max SL hits, max consecutive losses, max re-entries
  - Auto-pause + manual confirmation flow after SL
  - Persistent state across restarts (serialize / restore)

Strategies hold one ``RiskController`` instance and call:

  * ``allow_entry(side, current_price, line_price)`` before every entry
  * ``record_entry(side)`` once the entry order is sent
  * ``record_exit(exit_type, side, line_price, pnl)`` on every exit
  * ``update_price_for_arming(side, current_price)`` every tick

The controller never places orders itself — it only gates the strategy's
own decision-making.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timedelta
from enum import Enum
from typing import Optional, Tuple

from core.logger import get_logger

logger = get_logger("risk_controller")


class RiskMode(str, Enum):
    ACTIVE = "ACTIVE"                              # entries allowed
    COOLDOWN = "COOLDOWN"                          # entries blocked until cooldown_until
    PAUSED_AFTER_SL = "PAUSED_AFTER_SL"            # auto-paused, awaiting resume
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"  # SL hit + manual confirm needed
    HALTED = "HALTED"                              # daily cap exceeded


@dataclass
class RiskConfig:
    # Re-entry policy
    allow_reentry_after_target: bool = True
    allow_reentry_after_sl: bool = False
    require_manual_confirmation_after_sl: bool = True
    auto_pause_after_sl: bool = True

    # Daily caps
    max_reentries_per_day: int = 5
    max_sl_hits_per_day: int = 2
    max_consecutive_losses: int = 3

    # Cooldown after any exit (seconds; 0 disables)
    entry_cooldown_seconds: int = 30

    # Fresh-crossover requirement after SL
    require_fresh_crossover: bool = True
    fresh_crossover_distance: float = 2.0  # in price units of the line


@dataclass
class RiskStatePayload:
    mode: str = RiskMode.ACTIVE.value
    cooldown_until: str = ""
    last_exit_type: str = ""
    last_exit_at: str = ""
    last_exit_side: str = ""
    last_exit_line: float = 0.0
    sl_hits_today: int = 0
    consecutive_losses: int = 0
    reentries_today: int = 0
    fresh_crossover_armed: bool = True
    awaiting_confirmation: bool = False
    halted: bool = False
    halt_reason: str = ""
    trading_date: str = ""
    last_block_reason: str = ""


class RiskController:
    def __init__(self, config: Optional[RiskConfig] = None):
        self.config = config or RiskConfig()
        self.state = RiskStatePayload(trading_date=date.today().isoformat())

    # ── Lifecycle ──────────────────────────────────

    def reset_for_new_day(self) -> None:
        today = date.today().isoformat()
        if self.state.trading_date != today:
            cfg_dump = asdict(self.config)
            self.state = RiskStatePayload(trading_date=today)
            # Carry config across midnight; reset only counters.
            logger.info("Risk counters reset for new day. cfg=%s", cfg_dump)

    def update_config(self, **kwargs) -> None:
        for k, v in kwargs.items():
            if v is None:
                continue
            if not hasattr(self.config, k):
                continue
            cur = getattr(self.config, k)
            try:
                if isinstance(cur, bool):
                    setattr(self.config, k, bool(v))
                elif isinstance(cur, int):
                    setattr(self.config, k, int(v))
                elif isinstance(cur, float):
                    setattr(self.config, k, float(v))
                else:
                    setattr(self.config, k, v)
            except (TypeError, ValueError):
                pass

    # ── Decision API ───────────────────────────────

    def allow_entry(
        self,
        *,
        side: str,
        current_price: float = 0.0,
        line_price: float = 0.0,
    ) -> Tuple[bool, str]:
        """Returns (allowed, reason). Strategies must call this before
        firing any entry order."""
        self.reset_for_new_day()

        # Hard halts
        if self.state.halted:
            return self._block(f"HALTED — {self.state.halt_reason}")
        if self.state.awaiting_confirmation:
            return self._block("Awaiting manual confirmation after SL")
        if self.state.mode == RiskMode.PAUSED_AFTER_SL.value:
            return self._block("Paused after SL — resume manually")

        # Cooldown
        if self.state.cooldown_until:
            try:
                until = datetime.fromisoformat(self.state.cooldown_until)
            except ValueError:
                self.state.cooldown_until = ""
            else:
                if datetime.now() < until:
                    remaining = (until - datetime.now()).total_seconds()
                    return self._block(f"Cooldown — {remaining:.0f}s left")
                self.state.cooldown_until = ""
                if self.state.mode == RiskMode.COOLDOWN.value:
                    self.state.mode = RiskMode.ACTIVE.value

        # Daily caps
        if self.state.sl_hits_today >= self.config.max_sl_hits_per_day:
            self.halt(f"Max {self.config.max_sl_hits_per_day} SL hits reached")
            return self._block(self.state.halt_reason)
        if self.state.consecutive_losses >= self.config.max_consecutive_losses:
            self.halt(f"Max {self.config.max_consecutive_losses} consecutive losses")
            return self._block(self.state.halt_reason)
        if self.state.reentries_today >= self.config.max_reentries_per_day:
            self.halt(f"Max {self.config.max_reentries_per_day} re-entries reached")
            return self._block(self.state.halt_reason)

        # After-SL re-entry policy
        if self.state.last_exit_type == "SL_HIT" and not self.config.allow_reentry_after_sl:
            return self._block("Re-entry after SL disabled")
        if self.state.last_exit_type == "TARGET_HIT" and not self.config.allow_reentry_after_target:
            return self._block("Re-entry after TARGET disabled")

        # Fresh crossover (only enforced for the SAME side that just exited)
        if (
            self.config.require_fresh_crossover
            and not self.state.fresh_crossover_armed
            and self.state.last_exit_side
            and side == self.state.last_exit_side
            and self.state.last_exit_line > 0
            and current_price > 0
        ):
            d = abs(current_price - self.state.last_exit_line)
            if d < self.config.fresh_crossover_distance:
                return self._block(
                    f"Fresh crossover required — {d:.2f} away "
                    f"(need {self.config.fresh_crossover_distance:.2f})"
                )
            # Price has moved away; arm for the next genuine re-cross.
            self.state.fresh_crossover_armed = True

        self.state.last_block_reason = ""
        return True, "ok"

    def update_price_for_arming(self, *, side: str, current_price: float) -> None:
        """Re-arms fresh_crossover once price has moved sufficiently away
        from the last exit's line on the same side."""
        if self.state.fresh_crossover_armed:
            return
        if not self.state.last_exit_side or self.state.last_exit_side != side:
            return
        if self.state.last_exit_line <= 0 or current_price <= 0:
            return
        if abs(current_price - self.state.last_exit_line) >= self.config.fresh_crossover_distance:
            self.state.fresh_crossover_armed = True

    def record_entry(self, *, side: str) -> None:
        # Any entry that follows a previous exit on this trading day
        # counts as a re-entry (the very first entry of the day does not).
        if self.state.last_exit_at:
            self.state.reentries_today += 1

    def record_exit(
        self,
        *,
        exit_type: str,
        side: str,
        line_price: float,
        pnl: float,
    ) -> None:
        now = datetime.now()
        self.state.last_exit_type = exit_type
        self.state.last_exit_at = now.isoformat()
        self.state.last_exit_side = side or ""
        self.state.last_exit_line = float(line_price or 0)

        # Cooldown applies after every exit
        if self.config.entry_cooldown_seconds > 0:
            self.state.cooldown_until = (
                now + timedelta(seconds=self.config.entry_cooldown_seconds)
            ).isoformat()
            if self.state.mode == RiskMode.ACTIVE.value:
                self.state.mode = RiskMode.COOLDOWN.value

        if exit_type == "SL_HIT":
            self.state.sl_hits_today += 1
            self.state.consecutive_losses += 1
            self.state.fresh_crossover_armed = False
            if self.config.auto_pause_after_sl:
                self.state.mode = RiskMode.PAUSED_AFTER_SL.value
            if self.config.require_manual_confirmation_after_sl:
                self.state.awaiting_confirmation = True
                self.state.mode = RiskMode.AWAITING_CONFIRMATION.value
        elif exit_type == "TARGET_HIT":
            # winners reset the loss streak
            self.state.consecutive_losses = 0
        else:
            # AUTO_SQUAREOFF / BROKER_SQUAREOFF / MANUAL etc — count loss only if pnl < 0
            if pnl is not None and pnl < 0:
                self.state.consecutive_losses += 1
            else:
                self.state.consecutive_losses = 0

    # ── Manual control ─────────────────────────────

    def confirm_resume(self) -> None:
        """User-initiated: clear pending confirmation and re-activate."""
        self.state.awaiting_confirmation = False
        self.state.cooldown_until = ""
        if self.state.mode in (
            RiskMode.AWAITING_CONFIRMATION.value,
            RiskMode.PAUSED_AFTER_SL.value,
            RiskMode.COOLDOWN.value,
        ):
            self.state.mode = RiskMode.ACTIVE.value
        self.state.fresh_crossover_armed = True

    def pause(self) -> None:
        self.state.mode = RiskMode.PAUSED_AFTER_SL.value

    def resume(self) -> None:
        self.state.awaiting_confirmation = False
        self.state.halted = False
        self.state.halt_reason = ""
        self.state.cooldown_until = ""
        self.state.fresh_crossover_armed = True
        self.state.mode = RiskMode.ACTIVE.value

    def halt(self, reason: str) -> None:
        self.state.halted = True
        self.state.halt_reason = reason
        self.state.mode = RiskMode.HALTED.value

    def reset_counters(self) -> None:
        self.state.sl_hits_today = 0
        self.state.consecutive_losses = 0
        self.state.reentries_today = 0
        self.state.halted = False
        self.state.halt_reason = ""
        if self.state.mode == RiskMode.HALTED.value:
            self.state.mode = RiskMode.ACTIVE.value

    # ── Persistence / Status ───────────────────────

    def serialize(self) -> dict:
        return {"config": asdict(self.config), "state": asdict(self.state)}

    def restore(self, data: dict) -> None:
        if not isinstance(data, dict):
            return
        try:
            cfg = data.get("config") or {}
            self.update_config(**cfg)
            st = data.get("state") or {}
            for k, v in st.items():
                if hasattr(self.state, k):
                    try:
                        setattr(self.state, k, v)
                    except (TypeError, ValueError):
                        pass
            # Day boundary: drop counters but keep config
            self.reset_for_new_day()
        except Exception as exc:
            logger.warning("RiskController.restore failed: %s", exc)

    def status_payload(self) -> dict:
        cooldown_remaining = 0
        if self.state.cooldown_until:
            try:
                until = datetime.fromisoformat(self.state.cooldown_until)
                cooldown_remaining = max(0, int((until - datetime.now()).total_seconds()))
            except ValueError:
                pass
        return {
            "mode": self.state.mode,
            "cooldown_remaining_s": cooldown_remaining,
            "awaiting_confirmation": self.state.awaiting_confirmation,
            "halted": self.state.halted,
            "halt_reason": self.state.halt_reason,
            "sl_hits_today": self.state.sl_hits_today,
            "consecutive_losses": self.state.consecutive_losses,
            "reentries_today": self.state.reentries_today,
            "fresh_crossover_armed": self.state.fresh_crossover_armed,
            "last_exit_type": self.state.last_exit_type,
            "last_exit_at": self.state.last_exit_at,
            "last_exit_side": self.state.last_exit_side,
            "last_exit_line": self.state.last_exit_line,
            "last_block_reason": self.state.last_block_reason,
            "config": asdict(self.config),
        }

    # ── Internal ───────────────────────────────────

    def _block(self, reason: str) -> Tuple[bool, str]:
        self.state.last_block_reason = reason
        return False, reason
