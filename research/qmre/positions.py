"""
QMRE paper-position manager + Telegram alerts (dedup) — SIMULATION ONLY.

Opens / monitors / closes QMREPaperPosition rows for Live paper trading. Exits on
target/SL (live LTP) and intraday EOD square-off; swing positions can carry.
Never places a real order. Telegram reuses the app's dual-bot notify with
per-signal dedup + cooldown so it doesn't spam.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timezone
from typing import Optional

from core.database import get_db_session
from core.logger import get_logger
from core.models import QMREPaperPosition
from research.qmre import paper

logger = get_logger("research.qmre.positions")


def _f(v):
    return float(v) if v is not None else None


def pos_dict(r: QMREPaperPosition) -> dict:
    return {"id": r.id, "date": r.trade_date.isoformat() if r.trade_date else None,
            "symbol": r.symbol, "direction": r.direction, "mode": r.mode, "qty": r.qty,
            "entry_price": _f(r.entry_price), "entry_time": r.entry_time, "sl": _f(r.sl),
            "target": _f(r.target), "ltp": _f(r.ltp), "mtm": _f(r.mtm), "mfe": _f(r.mfe),
            "mae": _f(r.mae), "status": r.status, "exit_price": _f(r.exit_price),
            "exit_time": r.exit_time, "exit_reason": r.exit_reason, "score": _f(r.score),
            "signal_class": r.signal_class, "strategy_version": r.strategy_version,
            "source": r.source, "note": r.note}


class QMREPaperManager:
    def __init__(self, broker, user_id: int):
        self.broker = broker
        self.user_id = user_id

    # ── open (manual or auto). SIMULATION ONLY. ──
    def open_paper(self, *, symbol, qty, entry, sl, target, mode="intraday",
                   direction="LONG", score=None, signal_class=None, version=None,
                   source="manual", note=None) -> dict:
        paper.assert_paper_only()                      # hard guard
        db = get_db_session()
        try:
            row = QMREPaperPosition(
                user_id=self.user_id, trade_date=date.today(), symbol=symbol.upper(),
                direction=direction, mode=mode, qty=int(qty),
                entry_price=round(float(entry), 2), entry_time=datetime.now().strftime("%H:%M:%S"),
                sl=round(float(sl), 2) if sl else None, target=round(float(target), 2) if target else None,
                ltp=round(float(entry), 2), status="OPEN", score=score, signal_class=signal_class,
                strategy_version=version, source=source, note=note)
            db.add(row); db.commit(); db.refresh(row)
            return pos_dict(row)
        finally:
            db.close()

    def _ltp(self, symbol, exch="NSE") -> Optional[float]:
        try:
            key = f"{exch}:{symbol}"
            d = self.broker.get_ltp([key]) or {}
            v = d.get(key)
            return float(v) if v else None
        except Exception:
            return None

    def monitor(self, cfg) -> int:
        """Update LTP/MTM and close on target/SL/EOD. Returns positions closed."""
        db = get_db_session()
        closed = 0
        try:
            opens = (db.query(QMREPaperPosition)
                     .filter(QMREPaperPosition.user_id == self.user_id,
                             QMREPaperPosition.status == "OPEN").all())
            eod_h, eod_m = (cfg.get("eod_exit_time", "15:15")).split(":")
            now = datetime.now()
            for r in opens:
                ltp = self._ltp(r.symbol)
                if ltp is None:
                    continue
                qty = int(r.qty or 0)
                mtm = round((ltp - float(r.entry_price)) * qty, 2)
                r.ltp = round(ltp, 2); r.mtm = mtm
                r.mfe = round(max(float(r.mfe or 0), mtm), 2)
                r.mae = round(min(float(r.mae or 0), mtm), 2)
                reason = None
                if r.target and ltp >= float(r.target):
                    reason = "TARGET"
                elif r.sl and ltp <= float(r.sl):
                    reason = "STOP"
                elif r.mode == "intraday" and now.time() >= dtime(int(eod_h), int(eod_m)):
                    reason = "SQUAREOFF"
                if reason:
                    r.status = reason; r.exit_price = round(ltp, 2)
                    r.exit_reason = reason; r.exit_time = now.strftime("%H:%M:%S")
                    closed += 1
            db.commit()
            return closed
        finally:
            db.close()

    def close_paper(self, pos_id: int) -> dict:
        db = get_db_session()
        try:
            r = db.query(QMREPaperPosition).filter(
                QMREPaperPosition.id == pos_id, QMREPaperPosition.user_id == self.user_id).first()
            if not r:
                return {"status": "error", "message": "Position not found"}
            if r.status == "OPEN":
                ltp = self._ltp(r.symbol) or float(r.entry_price)
                r.status = "MANUAL"; r.exit_price = round(ltp, 2); r.exit_reason = "MANUAL"
                r.exit_time = datetime.now().strftime("%H:%M:%S")
                r.mtm = round((ltp - float(r.entry_price)) * int(r.qty or 0), 2)
                db.commit()
            return {"status": "ok", "position": pos_dict(r)}
        finally:
            db.close()

    def positions(self, trade_date: Optional[str] = None) -> list[dict]:
        db = get_db_session()
        try:
            q = db.query(QMREPaperPosition).filter(QMREPaperPosition.user_id == self.user_id)
            if trade_date:
                try:
                    q = q.filter(QMREPaperPosition.trade_date == date.fromisoformat(trade_date))
                except ValueError:
                    pass
            return [pos_dict(r) for r in q.order_by(QMREPaperPosition.id.desc()).limit(500).all()]
        finally:
            db.close()

    def portfolio(self, cfg) -> dict:
        rows = self.positions()
        openp = [r for r in rows if r["status"] == "OPEN"]
        closed = [r for r in rows if r["status"] != "OPEN"]
        realized = round(sum(r["mtm"] or 0 for r in closed), 2)
        unrealized = round(sum(r["mtm"] or 0 for r in openp), 2)
        wins = [r for r in closed if (r["mtm"] or 0) > 0]
        losses = [r for r in closed if (r["mtm"] or 0) < 0]
        gw = sum(r["mtm"] for r in wins); gl = -sum(r["mtm"] for r in losses)
        cap = float(cfg.get("starting_capital", 1000000)) or 1
        deployed = round(sum((r["entry_price"] or 0) * (r["qty"] or 0) for r in openp), 2)
        return {
            "starting_capital": cap, "realized": realized, "unrealized": unrealized,
            "total_pnl": round(realized + unrealized, 2), "return_pct": round((realized + unrealized) / cap * 100, 2),
            "deployed": deployed, "available": round(cap - deployed, 2),
            "open": len(openp), "closed": len(closed), "trades": len(closed),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0,
            "profit_factor": round(gw / gl, 2) if gl else (gw and 999.0 or 0.0),
            "avg_win": round(gw / len(wins), 2) if wins else 0,
            "avg_loss": round(-gl / len(losses), 2) if losses else 0,
            "best": round(max((r["mtm"] or 0 for r in closed), default=0), 2),
            "worst": round(min((r["mtm"] or 0 for r in closed), default=0), 2),
            "positions_open": openp, "positions_closed": closed[:50],
        }


# ── Telegram (dual-bot) with per-signal dedup + cooldown ──
_ALERT_CACHE: dict = {}          # (user, key) → last-sent datetime


def send_alert(cfg, key: str, text: str) -> bool:
    """Dedup + cooldown. key is a stable signal id (symbol+class+date)."""
    if not cfg.get("telegram_alerts"):
        return False
    try:
        from core import notify
        cool = int(cfg.get("alert_cooldown_min", 15)) * 60
        now = datetime.now(timezone.utc)
        last = _ALERT_CACHE.get(key)
        if last and (now - last).total_seconds() < cool:
            return False
        bots = ["a", "b"] if cfg.get("telegram_bot") == "both" else [cfg.get("telegram_bot", "a")]
        sent = False
        for b in bots:
            if notify.enabled(b):
                notify.send(text, bot=b); sent = True
        if sent:
            _ALERT_CACHE[key] = now
        return sent
    except Exception as exc:
        logger.debug("qmre alert failed: %s", exc)
        return False


def format_signal(c: dict, mkt: dict, cfg: dict) -> str:
    f = c["features"]; r = c["risk"]; sz = c["sizing"]
    return (f"<b>QUANTFLUX — {c['class']} PAPER SIGNAL</b>\n\n"
            f"Symbol: <b>{c['symbol']}</b>  ·  ₹{f['ltp']}\n"
            f"Score: {c['score']}/100  ·  {c['class']}\n"
            f"Market: {mkt.get('regime_label')}  ·  RVOL {f.get('rvol')}x  ·  "
            f"{'Above' if f.get('above_vwap') else 'Below'} VWAP\n"
            f"Paper Entry ₹{r['entry']}  ·  SL ₹{r['sl']}  ·  T1 ₹{r['target1']} / T2 ₹{r['target2']}\n"
            f"Qty {sz['qty']}  ·  Risk ₹{sz['risk_amount']}  ·  RR {r['rr']}\n"
            f"Mode: {cfg.get('mode', 'intraday').upper()}\n"
            f"<i>Research signal · paper only · NO REAL ORDER PLACED.</i>")
