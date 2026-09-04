"""
Strategy evaluators for the Equity Strategy Workspace.

Each function answers one question about one stock, right now, on the selected
timeframe: *does this stock satisfy this strategy's entry condition?* There is
no sizing, no target, no stop and no order — the workspace is a screener.

Every evaluator returns::

    {"ok": True | False | None, "label": str, "detail": str}

``ok is None`` means *not applicable* (the strategy needs intraday candles and a
monthly timeframe was picked, or there is not enough history yet). N/A never
counts against a stock: the score is ``matched / applicable``.

Where a strategy already owns a pure, tested calculation module in ``research/``
that module is reused verbatim, so a tick here means exactly what a signal means
in the live strategy. Nothing in this file touches a live engine.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime
from typing import Optional

from research.fourth_candle.calculations import analyze_day, day_candles, find_breakout
from research.hammer_breakout import calculations as hammer_calc
from research.prev_period_vwap import compute_prev_period_vwaps
from research.equity_workspace import indicators as ind
from research.wyckoff import core as wyckoff_core
from research.wyckoff.config import sanitize as wyckoff_sanitize

# ── strategy catalogue ────────────────────────────────────────────────
# key, name, one-line rule (the legend the UI shows), data it needs, and
# whether it is intraday-only. Order here is the column order in the table.
INTRADAY_TFS = {"minute", "3minute", "5minute", "10minute", "15minute", "30minute", "60minute"}

STRATEGIES: list[dict] = [
    {"key": "fourth_candle", "name": "4th Candle", "short": "4C",
     "rule": "First 3 candles of the day all one colour, then price breaks the 4th candle's high (3 red) or low (3 green).",
     "needs": ["tf"], "intraday_only": True, "source": "Equity Strategy 2 & 3"},
    {"key": "hammer_low", "name": "Hammer at Low", "short": "HAM",
     "rule": "Yesterday's daily candle is a hammer at a 3/6-month low after N red candles, and today trades above its high.",
     "needs": ["daily"], "intraday_only": False, "source": "Equity Strategy 4"},
    {"key": "pmvwap_hold", "name": "Prev-Month VWAP", "short": "PMV",
     "rule": "Price is at or above the previous month's VWAP, with the previous week's VWAP above it — the holding regime.",
     "needs": ["daily"], "intraday_only": False, "source": "Equity Strategy 1 / Research 8"},
    {"key": "vwap_pvwap", "name": "VWAP × Prev VWAP", "short": "VPV",
     "rule": "Today's running session VWAP has crossed above yesterday's session VWAP.",
     "needs": ["tf"], "intraday_only": True, "source": "Strategy 11"},
    {"key": "cum_volume", "name": "Cumulative Volume", "short": "CV",
     "rule": "Signed cumulative volume (green adds, red subtracts) is positive and rising over the lookback.",
     "needs": ["tf"], "intraday_only": False, "source": "Strategy 1 / CV"},
    {"key": "ema_pullback", "name": "EMA Pull-back", "short": "EMA",
     "rule": "Fast EMA above slow EMA (uptrend) and price is back touching the slow EMA — the pull-back entry zone.",
     "needs": ["tf"], "intraday_only": False, "source": "Strategy 12"},
    {"key": "cv_vwap_ema_adx", "name": "CV+VWAP+EMA+ADX", "short": "CVX",
     "rule": "Price above both the slow EMA and VWAP, ADX at or above the threshold, and cumulative volume confirming.",
     "needs": ["tf"], "intraday_only": False, "source": "Strategy 3"},
    {"key": "wyckoff", "name": "Wyckoff Phase", "short": "WYK",
     "rule": "Wyckoff structure is complete — a Phase C spring confirmed by its test, or a Phase D sign of strength with the last point of support (mirrored for distribution).",
     "needs": ["tf"], "intraday_only": False, "source": "Wyckoff Method"},
    {"key": "first_hour_breakout", "name": "First-Hour Breakout", "short": "FHB",
     "rule": "Price is above the highest first-hour (9:15–10:15) high of the last N days, with today's volume above that window's average.",
     "needs": ["hour"], "intraday_only": False, "source": "Strategy 10"},
]

STRATEGY_KEYS = [s["key"] for s in STRATEGIES]
_BY_KEY = {s["key"]: s for s in STRATEGIES}


def needed_data(keys: list[str]) -> set[str]:
    """Which candle series the chosen strategies actually require, so the scan
    only pays for the fetches it needs."""
    out: set[str] = set()
    for k in keys:
        out.update(_BY_KEY.get(k, {}).get("needs", []))
    return out


def _na(reason: str) -> dict:
    return {"ok": None, "label": "N/A", "detail": reason}


def _no(detail: str) -> dict:
    return {"ok": False, "label": "—", "detail": detail}


def _yes(label: str, detail: str) -> dict:
    return {"ok": True, "label": label, "detail": detail}


# ── 1. 4th Candle ────────────────────────────────────────────────────
def eval_fourth_candle(ctx: dict) -> dict:
    if ctx["tf"] not in INTRADAY_TFS:
        return _na("intraday only")
    candles = ctx.get("tf_candles") or []
    today = ctx["today"]
    dc = day_candles(candles, today)
    if len(dc) < 5:
        return _na(f"only {len(dc)} candles today — needs 5")
    an = analyze_day(dc, reverse=bool(ctx["cfg"]["fourth_candle_reverse"]))
    if not an or not an["bias"]:
        return _no("first 3 candles mixed")
    side = "LONG" if an["bias"] == "call" else "SHORT"
    level = an["fourth_high"] if an["bias"] == "call" else an["fourth_low"]
    bo = find_breakout(dc, an, entry_cutoff="15:30")
    if not bo:
        return _no(f"{side} bias, {level} not broken")
    return _yes(side, f"{side} — broke {level} at {bo['dt'].strftime('%H:%M')}")


# ── 2. Hammer at a 3/6-month low ─────────────────────────────────────
def eval_hammer_low(ctx: dict) -> dict:
    daily = ctx.get("daily") or []
    today = ctx["today"]
    completed = [c for c in daily if c["_d"] < today]
    cfg = ctx["cfg"]
    hcfg = {"low_lookback": cfg["hammer_lookback"], "red_before": cfg["hammer_red_before"],
            "lower_wick_min": cfg["hammer_lower_wick_min"], "body_max": cfg["hammer_body_max"],
            "upper_wick_max": cfg["hammer_upper_wick_max"]}
    if len(completed) < hcfg["low_lookback"] + hcfg["red_before"] + 1:
        return _na("not enough daily history")
    setup = hammer_calc.hammer_at(completed, len(completed) - 1, hcfg)
    if not setup:
        return _na("not enough daily history")
    if not setup["ok"]:
        return _no("no hammer on yesterday's candle")
    trigger = round(setup["high"], 2)
    day_bar = next((c for c in daily if c["_d"] == today), None)
    high = float(day_bar["high"]) if day_bar else (ctx.get("ltp") or 0.0)
    ltp = ctx.get("ltp") or 0.0
    if max(high, ltp) > trigger:
        return _yes("BUY", f"broke hammer high {trigger} (low {round(setup['low'], 2)})")
    return _no(f"armed — needs > {trigger}")


# ── 3. Previous-month VWAP holding ───────────────────────────────────
def eval_pmvwap_hold(ctx: dict) -> dict:
    daily = ctx.get("daily") or []
    if len(daily) < 30:
        return _na("not enough daily history")
    vw = compute_prev_period_vwaps(daily)
    row = vw[-1] if vw else {}
    pm, pw = row.get("prev_month_vwap"), row.get("prev_week_vwap")
    if not pm or not pw:
        return _na("prev-period VWAP unavailable")
    ltp = ctx.get("ltp") or float(daily[-1]["close"])
    buf = float(ctx["cfg"]["pmvwap_buffer_pct"]) / 100.0
    if ltp >= pm * (1.0 - buf) and pw >= pm:
        return _yes("HOLD", f"LTP {round(ltp, 2)} ≥ PM-VWAP {pm} · PW-VWAP {pw} above")
    why = "below PM-VWAP" if ltp < pm * (1.0 - buf) else "PW-VWAP below PM-VWAP"
    return _no(f"{why} (PM {pm} · PW {pw})")


# ── 4. Session VWAP × previous-day VWAP ──────────────────────────────
def eval_vwap_pvwap(ctx: dict) -> dict:
    if ctx["tf"] not in INTRADAY_TFS:
        return _na("intraday only")
    candles = ctx.get("tf_candles") or []
    today = ctx["today"]
    tdy = day_candles(candles, today)
    if len(tdy) < 2:
        return _na("session just started")
    prev_days = sorted({c["_dt"].date() for c in candles if c["_dt"].date() < today})
    if not prev_days:
        return _na("no previous session")
    prev = day_candles(candles, prev_days[-1])
    if not prev:
        return _na("no previous session")
    prev_vwap = round(ind.last(ind.running_vwap(prev)), 2)
    series = ind.running_vwap(tdy)
    now_vwap = round(ind.last(series), 2)
    if now_vwap <= prev_vwap:
        return _no(f"VWAP {now_vwap} below prev {prev_vwap}")
    crossed = any(v <= prev_vwap for v in series)     # started below and rose through
    when = ""
    if crossed:
        for i, v in enumerate(series):
            if v > prev_vwap:
                when = f" at {tdy[i]['_dt'].strftime('%H:%M')}"
                break
    return _yes("BULL", f"VWAP {now_vwap} {'crossed above' if crossed else 'held above'} "
                        f"prev-day {prev_vwap}{when}")


# ── 5. Cumulative volume ─────────────────────────────────────────────
def eval_cum_volume(ctx: dict) -> dict:
    candles = ctx.get("tf_candles") or []
    n = int(ctx["cfg"]["cv_lookback"])
    if len(candles) < max(4, n // 2):
        return _na("not enough candles")
    window = candles[-n:]
    cv = ind.cumulative_volume(window)
    cur = ind.last(cv)
    mid = cv[len(cv) // 2] if len(cv) > 2 else 0.0
    if cur > 0 and cur > mid:
        return _yes("BUY", f"CV {cur:,.0f} positive and rising over {len(window)} candles")
    state = "negative" if cur <= 0 else "fading"
    return _no(f"CV {cur:,.0f} {state}")


# ── 6. EMA pull-back ─────────────────────────────────────────────────
def eval_ema_pullback(ctx: dict) -> dict:
    cfg = ctx["cfg"]
    candles = ctx.get("tf_candles") or []
    slow_p = int(cfg["ema_slow"])
    if len(candles) < slow_p:
        return _na(f"needs {slow_p} candles, has {len(candles)}")
    closes = [float(c["close"]) for c in candles]
    fast = ind.last(ind.ema_series(closes, int(cfg["ema_fast"])))
    slow = ind.last(ind.ema_series(closes, slow_p))
    ltp = ctx.get("ltp") or closes[-1]
    if slow <= 0:
        return _na("EMA unavailable")
    gap = abs(ltp - slow) / slow * 100.0
    if fast <= slow:
        return _no(f"downtrend — EMA{cfg['ema_fast']} {fast:.2f} ≤ EMA{slow_p} {slow:.2f}")
    if gap > float(cfg["ema_touch_pct"]):
        return _no(f"uptrend, {gap:.2f}% away from EMA{slow_p} {slow:.2f}")
    return _yes("BUY", f"uptrend and price {gap:.2f}% off EMA{slow_p} {slow:.2f}")


# ── 7. CV + VWAP + EMA + ADX ─────────────────────────────────────────
def eval_cv_vwap_ema_adx(ctx: dict) -> dict:
    cfg = ctx["cfg"]
    candles = ctx.get("tf_candles") or []
    slow_p = int(cfg["ema_slow"])
    need = max(slow_p, int(cfg["adx_period"]) * 2 + 2)
    if len(candles) < need:
        return _na(f"needs {need} candles, has {len(candles)}")
    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    ema200 = ind.last(ind.ema_series(closes, slow_p))
    adx = ind.last(ind.adx_series(highs, lows, closes, int(cfg["adx_period"])))
    session = day_candles(candles, ctx["today"]) if ctx["tf"] in INTRADAY_TFS else candles
    vwap = ind.last(ind.running_vwap(session or candles))
    cv = ind.last(ind.cumulative_volume(candles[-int(cfg["cv_lookback"]):]))
    ltp = ctx.get("ltp") or closes[-1]
    fails = []
    if not (ema200 > 0 and ltp > ema200):
        fails.append(f"price ≤ EMA{slow_p} ({ema200:.2f})")
    if not (vwap > 0 and ltp > vwap):
        fails.append(f"price ≤ VWAP ({vwap:.2f})")
    if adx < float(cfg["adx_threshold"]):
        fails.append(f"ADX {adx:.1f} < {cfg['adx_threshold']}")
    if abs(cv) < float(cfg["cv_threshold"]):
        fails.append(f"CV {cv:,.0f} weak")
    if fails:
        return _no("; ".join(fails))
    return _yes("BULL", f"price > EMA{slow_p} {ema200:.2f} & VWAP {vwap:.2f} · ADX {adx:.1f}")


# ── 8. First-hour breakout ───────────────────────────────────────────
def eval_first_hour_breakout(ctx: dict) -> dict:
    hours = ctx.get("hour") or []
    days = int(ctx["cfg"]["first_hour_days"])
    firsts = [c for c in hours if c["_dt"].time() == dtime(9, 15) and c["_dt"].date() < ctx["today"]]
    if len(firsts) < 2:
        firsts = [c for c in hours if c["_dt"].date() < ctx["today"]]
    if not firsts:
        return _na("no 60-min history")
    firsts = firsts[-days:]
    level = round(max(float(c["high"]) for c in firsts), 2)
    avg_vol = sum(float(c.get("volume", 0) or 0) for c in firsts) / len(firsts)
    ltp = ctx.get("ltp") or 0.0
    vol = float(ctx.get("volume") or 0)
    if ltp <= level:
        return _no(f"below {level} ({len(firsts)}-day first-hour high)")
    if avg_vol > 0 and vol < avg_vol:
        return _no(f"above {level} but volume {vol:,.0f} < avg {avg_vol:,.0f}")
    return _yes("BUY", f"above {level} ({len(firsts)}-day first-hour high) on {vol:,.0f} volume")


# ── 9. Wyckoff structure ─────────────────────────────────────────────
def eval_wyckoff(ctx: dict) -> dict:
    """Delegates to the Wyckoff engine and reports only its verdict — the full
    read (range, events, nine tests, why-to-avoid) lives on the Wyckoff page."""
    candles = ctx.get("tf_candles") or []
    cfg = ctx["cfg"]
    look = int(cfg.get("wyckoff_lookback", 120))
    if len(candles) < 60:
        return _na(f"needs 60 candles, has {len(candles)}")
    wcfg = wyckoff_sanitize({"timeframe": ctx["tf"], "lookback": look,
                             "min_tests_enter": int(cfg.get("wyckoff_min_tests", 5))})
    read = wyckoff_core.analyze(candles, wcfg, instrument=ctx.get("symbol", ""))
    g = read.get("guidance") or {}
    action = g.get("action")
    phase = read.get("phase")
    tests = f"{g.get('tests_passed', 0)}/{g.get('tests_total', 9)}"
    if action == "ENTER_LONG":
        return _yes("LONG", f"Phase {phase} {read.get('bias')} · {tests} tests · {g.get('headline')}")
    if action == "ENTER_SHORT":
        return _yes("SHORT", f"Phase {phase} {read.get('bias')} · {tests} tests · {g.get('headline')}")
    if read.get("phase") == "?":
        return _na(read.get("phase_note") or "no structure")
    return _no(f"Phase {phase} {read.get('bias')} · {tests} tests · "
               f"{(g.get('avoid') or [g.get('headline', '')])[0]}")


EVALUATORS = {
    "fourth_candle": eval_fourth_candle,
    "hammer_low": eval_hammer_low,
    "pmvwap_hold": eval_pmvwap_hold,
    "vwap_pvwap": eval_vwap_pvwap,
    "cum_volume": eval_cum_volume,
    "ema_pullback": eval_ema_pullback,
    "cv_vwap_ema_adx": eval_cv_vwap_ema_adx,
    "wyckoff": eval_wyckoff,
    "first_hour_breakout": eval_first_hour_breakout,
}


def evaluate(ctx: dict, keys: list[str]) -> dict:
    """Run the selected evaluators over one stock's context."""
    out: dict = {}
    for k in keys:
        fn = EVALUATORS.get(k)
        if not fn:
            continue
        try:
            out[k] = fn(ctx)
        except Exception as exc:                       # one bad stock never kills a scan
            out[k] = {"ok": None, "label": "ERR", "detail": str(exc)[:120]}
    return out
