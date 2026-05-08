"""
Backtest engine for Strategy 7 (line-touch entry) and Strategy 8 (reverse).

Replays historical minute candles for the chosen CE / PE strikes, applies
the same trigger / SL / TGT rules used in live trading and returns:

  {
    "trades":       [...],        # candle-by-candle simulated trades
    "equity_curve": [{t,y}, ...], # cumulative PnL over time
    "stats":        {...},
    "ce_series":    [{t,o,h,l,c}, ...],  # for replay/visualisation
    "pe_series":    [...],
    "lines":        {call_line, put_line},
    "params":       {...},
  }
"""
from __future__ import annotations

from datetime import datetime, date as _date, time as dtime
from typing import Optional


def _to_minute_candles(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows or []:
        ts = r.get("date")
        try:
            t_str = ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else (str(ts)[-8:] if ts else "")
        except Exception:
            t_str = ""
        out.append({
            "t": t_str,
            "o": float(r.get("open", 0) or 0),
            "h": float(r.get("high", 0) or 0),
            "l": float(r.get("low", 0) or 0),
            "c": float(r.get("close", 0) or 0),
        })
    return out


def _zip_by_time(ce: list[dict], pe: list[dict]) -> list[dict]:
    pe_by_t = {c["t"]: c for c in pe}
    rows = []
    for c in ce:
        p = pe_by_t.get(c["t"])
        if not p:
            continue
        rows.append({"t": c["t"], "ce": c, "pe": p})
    return rows


def _resolve_reverse_strike(strategy: str, trigger_side: str, ce_strike: int, pe_strike: int,
                            offset: int, manual_pe: int, manual_ce: int) -> int:
    """Return the strike that gets BOUGHT for an S8 trigger, or 0 for S7."""
    if strategy != "S8":
        return ce_strike if trigger_side == "CALL" else pe_strike
    if trigger_side == "CALL":
        return manual_pe if manual_pe > 0 else (ce_strike + offset)
    return manual_ce if manual_ce > 0 else (pe_strike - offset)


def run_backtest(
    broker,
    *,
    strategy: str,                 # "S7" | "S8" | "S9"
    trade_date: _date,
    ce_token: int,
    pe_token: int,
    ce_strike: int,
    pe_strike: int,
    call_line: float,
    put_line: float,
    sl_points: float,
    target_points: float,
    lot_size: int,
    lots: int,
    max_trades: int = 3,
    reverse_offset: int = 200,
    manual_pe_strike: int = 0,
    manual_ce_strike: int = 0,
    reverse_ce_token: int = 0,     # token of strike that BUYS happen on (S8 reverse)
    reverse_pe_token: int = 0,
    # ── S9 Line Of Control (3-line per side) ──
    ce_buy_line: float = 0,
    ce_target_line: float = 0,
    ce_sl_line: float = 0,
    pe_buy_line: float = 0,
    pe_target_line: float = 0,
    pe_sl_line: float = 0,
) -> dict:
    if not ce_token or not pe_token:
        return {
            "status": "error",
            "message": "ce_token and pe_token required",
            "trades": [], "equity_curve": [],
            "ce_series": [], "pe_series": [],
            "stats": {}, "lines": {"call_line": call_line, "put_line": put_line},
        }

    from_dt = datetime.combine(trade_date, dtime(9, 15))
    to_dt   = datetime.combine(trade_date, dtime(15, 30))

    try:
        ce_rows = broker.get_historical_data(
            instrument_token=ce_token, from_date=from_dt, to_date=to_dt, interval="minute",
        ) or []
        pe_rows = broker.get_historical_data(
            instrument_token=pe_token, from_date=from_dt, to_date=to_dt, interval="minute",
        ) or []
    except Exception as exc:
        return {
            "status": "error",
            "message": f"historical fetch failed: {exc}",
            "trades": [], "equity_curve": [],
            "ce_series": [], "pe_series": [],
            "stats": {}, "lines": {"call_line": call_line, "put_line": put_line},
        }

    # Optional reverse-leg series for S8 (strike that actually gets BOUGHT)
    reverse_ce_rows: list[dict] = []
    reverse_pe_rows: list[dict] = []
    if strategy == "S8":
        try:
            if reverse_ce_token:
                reverse_ce_rows = broker.get_historical_data(
                    instrument_token=reverse_ce_token, from_date=from_dt, to_date=to_dt, interval="minute",
                ) or []
            if reverse_pe_token:
                reverse_pe_rows = broker.get_historical_data(
                    instrument_token=reverse_pe_token, from_date=from_dt, to_date=to_dt, interval="minute",
                ) or []
        except Exception:
            pass

    ce = _to_minute_candles(ce_rows)
    pe = _to_minute_candles(pe_rows)
    rce_by_t = {c["t"]: c for c in _to_minute_candles(reverse_ce_rows)}
    rpe_by_t = {c["t"]: c for c in _to_minute_candles(reverse_pe_rows)}

    if not ce or not pe:
        return {
            "status": "error",
            "message": "no historical data for given date / strikes",
            "trades": [], "equity_curve": [],
            "ce_series": ce, "pe_series": pe,
            "stats": {}, "lines": {"call_line": call_line, "put_line": put_line},
        }

    aligned = _zip_by_time(ce, pe)

    quantity = max(0, int(lots) * int(lot_size))
    trades: list[dict] = []
    equity_curve: list[dict] = []
    cum_pnl = 0.0

    in_position = False
    pos_side: Optional[str] = None        # option BOUGHT (CE/PE)
    pos_trigger: Optional[str] = None     # CALL/PUT line that fired
    entry_price = 0.0
    sl_price = 0.0
    tgt_price = 0.0
    entry_time = ""
    entered_strike = 0

    triggers_used = 0
    prev_ce_close = 0.0
    prev_pe_close = 0.0

    for row in aligned:
        t   = row["t"]
        cec = row["ce"]
        pec = row["pe"]

        # Step 1 — exit if in position
        if in_position:
            # Reverse-leg candle (the position's actual option)
            if strategy == "S8":
                cur = (rpe_by_t.get(t) if pos_side == "PE" else rce_by_t.get(t)) or {}
                hi = cur.get("h", 0); lo = cur.get("l", 0); cl = cur.get("c", 0)
                if not hi:
                    # fallback to monitored side
                    src = pec if pos_side == "PE" else cec
                    hi, lo, cl = src["h"], src["l"], src["c"]
            else:
                src = cec if pos_side == "CE" else pec
                hi, lo, cl = src["h"], src["l"], src["c"]

            exit_price = 0.0
            exit_type = ""
            if strategy == "S9":
                # Line-driven exits — but ALWAYS validate direction relative
                # to entry_price so a "target line" hit below entry is
                # correctly labelled as a stop-out.
                t_line = ce_target_line if pos_side == "CE" else pe_target_line
                s_line = ce_sl_line     if pos_side == "CE" else pe_sl_line
                if s_line > 0 and lo <= s_line and s_line < entry_price:
                    exit_price, exit_type = s_line, "SL_HIT"
                elif t_line > 0 and hi >= t_line and t_line > entry_price:
                    exit_price, exit_type = t_line, "TARGET_HIT"
                elif t >= "15:15:00":
                    exit_price, exit_type = cl, "AUTO_SQUAREOFF"
            else:
                if lo <= sl_price:
                    exit_price, exit_type = sl_price, "SL_HIT"
                elif hi >= tgt_price:
                    exit_price, exit_type = tgt_price, "TARGET_HIT"
                elif t >= "15:15:00":
                    exit_price, exit_type = cl, "AUTO_SQUAREOFF"

            if exit_price > 0:
                pnl = round((exit_price - entry_price) * quantity, 2)
                cum_pnl += pnl
                trades.append({
                    "entry_time": entry_time,
                    "exit_time": t,
                    "side": pos_side,
                    "trigger_side": pos_trigger,
                    "strike": entered_strike,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "exit_type": exit_type,
                    "sl": sl_price, "tgt": tgt_price,
                    "qty": quantity, "pnl": pnl,
                })
                in_position = False
                pos_side = None
                pos_trigger = None

        equity_curve.append({"t": t, "y": round(cum_pnl, 2)})

        if in_position or triggers_used >= max_trades or t >= "15:15:00":
            prev_ce_close = cec["c"]; prev_pe_close = pec["c"]
            continue

        # Step 2 — touch detection on CE / PE (one-sided ≥ for S8, crossing for S7)
        ce_h = cec["h"]; pe_h = pec["h"]
        ce_o = cec["o"]; pe_o = pec["o"]

        fire_call = False; fire_put = False; trigger_price = 0.0
        if strategy == "S9":
            # Strict crossover up through the BUY line — required on BOTH sides
            # independently. Without this, the engine fires every candle whose
            # high is above the line (false re-entries on a sustained move that
            # never re-tests the line).
            ce_cross = (
                ce_buy_line > 0 and prev_ce_close > 0
                and prev_ce_close < ce_buy_line <= ce_h
            )
            pe_cross = (
                pe_buy_line > 0 and prev_pe_close > 0
                and prev_pe_close < pe_buy_line <= pe_h
            )
            if ce_cross and pe_cross:
                # Both crossed in the same minute — pick the side that opened
                # closer to its line (lowest expected slippage).
                if abs(ce_o - ce_buy_line) <= abs(pe_o - pe_buy_line):
                    fire_call = True; trigger_price = ce_buy_line
                else:
                    fire_put = True; trigger_price = pe_buy_line
            elif ce_cross:
                fire_call = True; trigger_price = ce_buy_line
            elif pe_cross:
                fire_put = True; trigger_price = pe_buy_line
        elif strategy == "S8":
            if call_line > 0 and ce_h >= call_line:
                fire_call = True; trigger_price = call_line
            elif put_line > 0 and pe_h >= put_line:
                fire_put = True; trigger_price = put_line
        else:  # S7 — bi-directional cross
            if call_line > 0 and prev_ce_close > 0:
                if (prev_ce_close < call_line <= ce_h) or (prev_ce_close > call_line >= cec["l"]):
                    fire_call = True; trigger_price = call_line
            if not fire_call and put_line > 0 and prev_pe_close > 0:
                if (prev_pe_close < put_line <= pe_h) or (prev_pe_close > put_line >= pec["l"]):
                    fire_put = True; trigger_price = put_line

        if fire_call or fire_put:
            trigger_side = "CALL" if fire_call else "PUT"
            if strategy == "S8":
                pos_side = "PE" if fire_call else "CE"
                target_strike = _resolve_reverse_strike(
                    "S8", trigger_side, ce_strike, pe_strike,
                    reverse_offset, manual_pe_strike, manual_ce_strike,
                )
                fill_candle = (rpe_by_t.get(t) if pos_side == "PE" else rce_by_t.get(t)) or {}
                fill = float(fill_candle.get("o") or 0) or float(fill_candle.get("c") or 0)
                if fill <= 0:
                    fill = float((pec if pos_side == "PE" else cec)["o"])
                entered_strike = target_strike
            else:
                # S7 / S9 — direct: BUY same side that fired.
                #
                # Realistic fill model for a line-touch strategy:
                # the moment LTP reaches the line, a MARKET order is sent
                # and fills near the line price. So:
                #   - if the candle OPENED above the line (gap), the
                #     earliest possible entry is at the open
                #   - otherwise the LTP rose intra-candle to touch the
                #     line and we fill ≈ at the line itself.
                # Using max(open, trigger_line) mirrors this without
                # peeking at intra-candle ticks.
                pos_side = "CE" if fire_call else "PE"
                cand_open = ce_o if fire_call else pe_o
                fill = max(float(cand_open or 0), float(trigger_price or 0))
                if fill <= 0:
                    fill = float(cand_open or trigger_price or 0)
                entered_strike = ce_strike if fire_call else pe_strike

            entry_price = float(fill)
            if strategy == "S9":
                # Target / SL come from per-side lines; fallbacks to point offsets.
                t_line = ce_target_line if pos_side == "CE" else pe_target_line
                s_line = ce_sl_line     if pos_side == "CE" else pe_sl_line
                tgt_price = t_line if t_line > 0 else (entry_price + target_points)
                sl_price  = max(0.05, s_line) if s_line > 0 else max(0.05, entry_price - sl_points)

                # ── Entry validity guards (S9) ──
                # 1) Gap-up fill: if open is far above the BUY line, the
                #    "touch" never occurred at a fillable level — reject.
                # 2) Config sanity: fill ≥ target_line means trade has no
                #    upside; fill ≤ sl_line means trade is born stopped-out.
                MAX_ENTRY_GAP = max(2.0, sl_points * 0.2)
                gap = entry_price - trigger_price
                cfg_invalid = (
                    (t_line > 0 and entry_price >= t_line)
                    or (s_line > 0 and entry_price <= s_line)
                )
                if gap > MAX_ENTRY_GAP or cfg_invalid:
                    # Skip this entry — record a rejection note for the UI.
                    reason = (
                        f"gap {gap:.2f} > {MAX_ENTRY_GAP:.2f}"
                        if gap > MAX_ENTRY_GAP
                        else "fill outside SL/target band"
                    )
                    pos_side = None
                    pos_trigger = None
                    entry_price = 0.0
                    sl_price = 0.0
                    tgt_price = 0.0
                    fire_call = False
                    fire_put = False
                    prev_ce_close = cec["c"]; prev_pe_close = pec["c"]
                    continue
            else:
                sl_price    = max(0.05, entry_price - sl_points)
                tgt_price   = entry_price + target_points
            entry_time  = t
            pos_trigger = trigger_side
            in_position = True
            triggers_used += 1
            trades.append({
                "entry_time": t,
                "exit_time": "",
                "side": pos_side,
                "trigger_side": trigger_side,
                "strike": entered_strike,
                "trigger_price": trigger_price,
                "entry_price": entry_price,
                "sl": sl_price, "tgt": tgt_price,
                "exit_type": "OPEN",
                "qty": quantity, "pnl": 0,
            })
            # remove the duplicate placeholder once exit happens
            # (handled by replacing the last open record at exit time below)

        prev_ce_close = cec["c"]; prev_pe_close = pec["c"]

    # Merge any open placeholder rows with their close rows
    open_rows = [t for t in trades if t.get("exit_type") == "OPEN"]
    closed = []
    for t in trades:
        if t.get("exit_type") == "OPEN":
            continue
        # find matching open by entry_time
        match = next((o for o in open_rows if o["entry_time"] == t["entry_time"]), None)
        if match:
            t["trigger_price"] = match.get("trigger_price", 0)
        closed.append(t)
    # If there's a still-open position at EOD, close it on last candle
    if in_position and aligned:
        last = aligned[-1]
        if strategy == "S8":
            cur = (rpe_by_t.get(last["t"]) if pos_side == "PE" else rce_by_t.get(last["t"])) or {}
            cl = float(cur.get("c") or 0) or float((last["pe"] if pos_side == "PE" else last["ce"])["c"])
        else:
            cl = (last["ce"] if pos_side == "CE" else last["pe"])["c"]
        pnl = round((cl - entry_price) * quantity, 2)
        cum_pnl += pnl
        closed.append({
            "entry_time": entry_time, "exit_time": last["t"],
            "side": pos_side, "trigger_side": pos_trigger,
            "strike": entered_strike,
            "entry_price": entry_price, "exit_price": cl,
            "exit_type": "AUTO_SQUAREOFF",
            "sl": sl_price, "tgt": tgt_price,
            "qty": quantity, "pnl": pnl,
        })
        if equity_curve:
            equity_curve[-1] = {"t": last["t"], "y": round(cum_pnl, 2)}

    wins   = [t for t in closed if t.get("pnl", 0) > 0]
    losses = [t for t in closed if t.get("pnl", 0) < 0]
    total  = len(closed)

    stats = {
        "total_trades": total,
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(100 * len(wins) / total, 2) if total else 0.0,
        "total_pnl": round(cum_pnl, 2),
        "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0.0,
        "best": round(max((t["pnl"] for t in closed), default=0), 2),
        "worst": round(min((t["pnl"] for t in closed), default=0), 2),
        "max_drawdown": _max_drawdown(equity_curve),
    }

    lines_payload = {"call_line": call_line, "put_line": put_line}
    if strategy == "S9":
        lines_payload = {
            "ce": {"buy": ce_buy_line, "target": ce_target_line, "sl": ce_sl_line},
            "pe": {"buy": pe_buy_line, "target": pe_target_line, "sl": pe_sl_line},
            # also expose flat aliases used by the chart engine
            "call_line": ce_buy_line, "put_line": pe_buy_line,
        }

    return {
        "status": "ok",
        "strategy": strategy,
        "trade_date": trade_date.isoformat(),
        "trades": closed,
        "equity_curve": equity_curve,
        "ce_series": ce,
        "pe_series": pe,
        "lines": lines_payload,
        "stats": stats,
        "params": {
            "ce_strike": ce_strike, "pe_strike": pe_strike,
            "sl_points": sl_points, "target_points": target_points,
            "lot_size": lot_size, "lots": lots, "max_trades": max_trades,
            "reverse_mode": "MANUAL" if (manual_pe_strike or manual_ce_strike) else "AUTO",
            "reverse_offset": reverse_offset,
            "manual_pe_strike": manual_pe_strike,
            "manual_ce_strike": manual_ce_strike,
        },
    }


def _max_drawdown(curve: list[dict]) -> float:
    peak = 0.0; max_dd = 0.0
    for p in curve:
        y = float(p.get("y", 0))
        if y > peak:
            peak = y
        dd = peak - y
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)
