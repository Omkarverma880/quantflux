"""
Madhav — QuantFlux's built-in help chatbot.

No external LLM. Two layers:

  1. **Curated answers** for common topics (strategies, kill switch, fences,
     manual trading, etc.) — written in plain English, focused on exactly
     what was asked.
  2. **Doc fallback** that searches the knowledge base / README / HTML docs
     and returns the best section, with markdown cleaned up so it reads
     like prose, not source code.
"""
from __future__ import annotations

import re
import html
from pathlib import Path
from functools import lru_cache

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.auth import login_required
from core.logger import get_logger

router = APIRouter()
logger = get_logger("api.madhav")

ROOT = Path(__file__).resolve().parent.parent.parent  # project root


# ────────────────────────────────────────────────────────────────────────
# 1.  Curated answer book — short, natural-English explanations.
# ────────────────────────────────────────────────────────────────────────

TOPICS: dict[str, dict] = {
    "strategies_list": {
        "title": "Strategies in QuantFlux",
        "answer": (
            "QuantFlux ships with **9 trading strategies** plus 1 non-trading "
            "helper (Cumulative Volume).  Here's the lineup:\n\n"
            "1. **Strategy 1 — Gann CV** — Gann levels + cumulative-volume threshold.\n"
            "2. **Strategy 2 — Option Selling** — OTM call/put sells with SL in premium.\n"
            "3. **Strategy 3 — CV + VWAP + EMA + ADX** — four-filter trend trade.\n"
            "4. **Strategy 4 — High/Low Retest** — opening-range retest breakout.\n"
            "5. **Strategy 5 — Gann Range** — Gann-level crossover with CV confirmation.\n"
            "6. **Strategy 6 — Call/Put Lines** — OI / premium-derived line reactions.\n"
            "7. **Strategy 7 — Strike Lines** — pivot/strike-line reactions.\n"
            "8. **Strategy 8 — Reverse** — mean-reversion fades of extremes.\n"
            "9. **Strategy 9 — LOC** — last-hour compression breakout.\n\n"
            "Each strategy has its own page under **Strategies** in the sidebar, "
            "its own config, and exits through the 15:15 hard fence.  Ask me "
            "*'explain strategy 3'* (or any number) for details on one."
        ),
    },
    "strategy1": {
        "title": "Strategy 1 — Gann + Cumulative Volume",
        "answer": (
            "Strategy 1 combines two things: the **Gann levels** loaded from "
            "`gann_levels.csv` and the **cumulative-volume (CV)** indicator "
            "computed from 9:15 onwards.\n\n"
            "It waits for the CV to cross a configured positive or negative "
            "threshold. When CV is positive enough it leans bullish, when "
            "negative enough it leans bearish. The Gann level closest to spot "
            "is then used to pick the strike. Once a position is open, the "
            "strategy manages it with the standard SL / target / trailing "
            "rules from its config, and is force-flat at the 15:15 auto-"
            "squareoff fence.\n\n"
            "API: `/api/strategy1-trade`.  UI page: Strategies → Strategy 1."
        ),
    },
    "strategy2": {
        "title": "Strategy 2 — Option Selling",
        "answer": (
            "Strategy 2 is an **option-selling** strategy. It picks an OTM "
            "call or put (configurable distance from spot), sells it as MIS, "
            "and protects the trade with a stop-loss in premium points.\n\n"
            "Use it when you expect range-bound or trending-away movement "
            "from the strike. All exits route through the 15:15 hard fence."
        ),
    },
    "strategy3": {
        "title": "Strategy 3 — CV + VWAP + EMA + ADX",
        "answer": (
            "Strategy 3 stacks four filters before taking a trade:\n"
            "• Cumulative-volume direction\n"
            "• Price vs VWAP\n"
            "• Price vs EMA\n"
            "• ADX strength threshold\n\n"
            "Only when all four agree does it enter an option leg in the "
            "trend direction. This makes it slower but higher-quality than "
            "Strategy 1."
        ),
    },
    "strategy4": {
        "title": "Strategy 4 — High/Low Retest",
        "answer": (
            "Strategy 4 watches the opening-range high and low. When price "
            "breaks one side and then comes back to **retest** it without "
            "breaking through, the strategy enters in the breakout direction. "
            "It works well on trending days with a clean opening range."
        ),
    },
    "strategy5": {
        "title": "Strategy 5 — Gann Range",
        "answer": (
            "Strategy 5 uses Gann levels to define an intraday range. A trade "
            "is taken when price decisively crosses a Gann level with "
            "supporting CV, targeting the next Gann level above/below."
        ),
    },
    "strategy6": {
        "title": "Strategy 6 — Call/Put Lines",
        "answer": (
            "Strategy 6 plots call and put 'lines' derived from option open "
            "interest and premium structure. Entries happen when price "
            "interacts with these lines in a specific way (rejection, "
            "break, retest) — it is meant for option-flow-aware traders."
        ),
    },
    "strategy7": {
        "title": "Strategy 7 — Strike Lines",
        "answer": (
            "Strategy 7 is a strike-line / pivot strategy: it draws lines at "
            "key strike levels and trades the reaction of price at those "
            "lines, with risk capped by the next strike."
        ),
    },
    "strategy8": {
        "title": "Strategy 8 — Reverse",
        "answer": (
            "Strategy 8 is a **mean-reversion** style: it fades extreme CV "
            "and price extensions, expecting them to come back to fair value. "
            "Position size is kept smaller because reversion entries can "
            "stop out on continuation."
        ),
    },
    "strategy9": {
        "title": "Strategy 9 — LOC (Last-Hour Compression)",
        "answer": (
            "Strategy 9 looks for **last-hour compression**: tight ranges in "
            "the final 60–75 minutes of the session. It enters in the "
            "direction of the resolving breakout, with the 15:15 fence "
            "guaranteeing the trade closes before the close."
        ),
    },
    "kill_switch": {
        "title": "Kill Switch / Instant Exit",
        "answer": (
            "The kill switch is the big red **Instant Exit** button on the "
            "Dashboard. It calls `POST /api/trading/exit_all` which does two "
            "things, but only for **MIS option intraday** trades:\n\n"
            "1. Cancels every open or trigger-pending MIS option order.\n"
            "2. Squares off every non-zero MIS option position at MARKET, "
            "tagged `EXITALL`.\n\n"
            "Same-day equity buys (CNC, T+0 holdings), NRML positions and any "
            "CNC positions are **deliberately left untouched** — the switch "
            "is scoped strictly to intraday options."
        ),
    },
    "auto_squareoff": {
        "title": "Auto-Squareoff Fence (15:15)",
        "answer": (
            "A background loop in `core/auto_squareoff.py` fires once per "
            "trading day at the configured time (default **15:15 IST**, set "
            "via the `AUTO_SQUARE_OFF_TIME` env var). It iterates positions "
            "and force-exits every `product=='MIS'` leg on the option "
            "exchanges (NFO/BFO/CDS/MCX) with a MARKET order tagged "
            "`AUTO315`.\n\n"
            "CNC equity and NRML positions are never touched. You can also "
            "trigger it manually from the Dashboard with the **Run Squareoff "
            "Now** button (endpoint `POST /api/risk/squareoff_now`)."
        ),
    },
    "pnl_fence": {
        "title": "Advanced P&L Fence",
        "answer": (
            "The P&L Fence locks in profit and caps loss for the day. You "
            "set two values on the Dashboard:\n\n"
            "• **Lock profit** — once total intraday option P&L reaches this, "
            "the system runs an immediate kill-switch on MIS options and "
            "blocks new option orders.\n"
            "• **Max loss** — same behaviour on the loss side.\n\n"
            "A watcher loop checks live P&L every 5 seconds. The trigger "
            "auto-resets the next trading day. Config is stored per-user at "
            "`data/risk_fence/<user_id>.json`."
        ),
    },
    "loss_control": {
        "title": "Day-Loss Control",
        "answer": (
            "Day-Loss Control is a separate **block-new-orders** gate. You "
            "set a `max_day_loss` (e.g. 10000). The moment intraday MIS "
            "option P&L is ≤ -10000, every new manual or strategy option "
            "order is rejected with HTTP **423 Locked** and the system also "
            "tries to flatten existing option positions.\n\n"
            "Trading only resumes when you toggle the switch off on the "
            "Dashboard (or after the next trading day, when triggers reset)."
        ),
    },
    "manual_trading": {
        "title": "Manual Trading",
        "answer": (
            "The Manual Trading page lets you place option orders by hand "
            "with optional SL/TGT/Trailing attached at entry. After an order "
            "is placed you can:\n\n"
            "• **Modify** price, quantity, trigger or order type from the "
            "Open Orders table (`POST /api/manual/order/modify`).\n"
            "• **Attach or edit SL/TGT** even after fill, via "
            "`POST /api/manual/monitor/attach` — the system fills in missing "
            "fields from your live positions.\n\n"
            "Every placement is gated by the Day-Loss Control switch."
        ),
    },
    "madhav": {
        "title": "About Madhav",
        "answer": (
            "I'm **Madhav** — QuantFlux's built-in help bot. I read the "
            "knowledge base, README, application & strategy docs, and the "
            "steps file, and answer questions about how the platform works. "
            "I have curated answers for the common topics (strategies, kill "
            "switch, fences, manual trading) and fall back to doc search for "
            "everything else. Ask me anything — for example *'explain "
            "strategy 3'*, *'how does the kill switch work?'*, *'what is "
            "loss control?'*."
        ),
    },
    "dashboard": {
        "title": "Dashboard",
        "answer": (
            "The Dashboard shows live account margin, Day P&L, position "
            "count, open orders, strategy cards (one per strategy with "
            "Start/Stop), the three Risk-Fence cards, and the Instant Exit "
            "kill switch. Data is refreshed every 5 seconds via "
            "`GET /api/dashboard/summary` and supplemented by a WebSocket "
            "stream for strategy state updates."
        ),
    },
    "app_overview": {
        "title": "How QuantFlux works",
        "answer": (
            "QuantFlux is an intraday options trading platform built on "
            "FastAPI + React, talking to Zerodha Kite.\n\n"
            "**Backend** (`main.py` → `app/server.py`) exposes REST routes "
            "for auth, dashboard, manual trading, nine strategies, settings "
            "and risk fences. Each user logs into the app with email/password "
            "and then OAuths into Zerodha; the Kite session is encrypted "
            "and cached per user.\n\n"
            "**Strategies** (`strategies/`) implement the trading logic. They "
            "run as async tasks managed by a small `TradingEngine`. Each "
            "strategy reads its own config, places MIS option orders, "
            "manages SL/TGT, and respects the global 15:15 hard fence.\n\n"
            "**Risk layer** has three independent guards: the 15:15 auto-"
            "squareoff, the P&L Fence (lock-profit / max-loss), and Day-Loss "
            "Control (blocks new orders past a daily loss).\n\n"
            "**Frontend** (`frontend/src`) is a Vite + React app with pages "
            "for Login, Dashboard, Manual Trading, each strategy, Analytics, "
            "Settings and more. It calls the REST API and listens to a "
            "WebSocket for live strategy state."
        ),
    },
    "settings": {
        "title": "Settings",
        "answer": (
            "The Settings page lets you toggle paper-trade mode, set the "
            "max position size, configure per-strategy risk limits, and "
            "manage your Zerodha credentials. Most values map directly to "
            "environment variables consumed in `config/settings.py`."
        ),
    },
}


# ── Intent matcher ────────────────────────────────────────────────────────

def _normalise(q: str) -> str:
    s = q.lower()
    s = re.sub(r"[\-_/]+", " ", s)
    s = re.sub(r"\bstrat\b", "strategy", s)
    s = re.sub(r"\bs(\d)\b", r"strategy \1", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_INTENTS: list[tuple[re.Pattern, str]] = [
    # "how many strategies", "list strategies", plain "strategies"
    (re.compile(r"\b(how\s*many\s*strateg|list\s*(of\s*)?strateg|all\s*strateg|"
                r"total\s*strateg|which\s*strateg|what\s*(are\s*)?(the\s*)?strateg|"
                r"available\s*strateg|strateg(y|ies)\s*list|^\s*strateg(y|ies)\s*$)\b"),
     "strategies_list"),
    (re.compile(r"\b(strategy\s*1|gann\s*cv|first\s*strategy)\b"), "strategy1"),
    (re.compile(r"\b(strategy\s*2|option\s*sell|second\s*strategy)\b"), "strategy2"),
    (re.compile(r"\b(strategy\s*3|cv\s*vwap|third\s*strategy)\b"), "strategy3"),
    (re.compile(r"\b(strategy\s*4|high\s*low\s*retest|fourth\s*strategy)\b"), "strategy4"),
    (re.compile(r"\b(strategy\s*5|gann\s*range|fifth\s*strategy)\b"), "strategy5"),
    (re.compile(r"\b(strategy\s*6|call\s*put\s*lines?|sixth\s*strategy)\b"), "strategy6"),
    (re.compile(r"\b(strategy\s*7|strike\s*lines?|seventh\s*strategy)\b"), "strategy7"),
    (re.compile(r"\b(strategy\s*8|reverse|mean\s*revers|eighth\s*strategy)\b"), "strategy8"),
    (re.compile(r"\b(strategy\s*9|loc|last\s*hour|ninth\s*strategy)\b"), "strategy9"),
    (re.compile(r"\b(kill\s*switch|instant\s*exit|exit\s*all)\b"), "kill_switch"),
    (re.compile(r"\b(auto\s*square\s*off|15[:\s]?15|3[:\s]?15\s*pm?|hard\s*fence)\b"), "auto_squareoff"),
    (re.compile(r"\b(p\s*&?\s*l\s*fence|pnl\s*fence|lock\s*profit|max\s*loss)\b"), "pnl_fence"),
    (re.compile(r"\b(loss\s*control|day\s*loss|max\s*day\s*loss)\b"), "loss_control"),
    (re.compile(r"\b(manual\s*trad|modify\s*order|attach\s*sl|set\s*sl|set\s*tgt|set\s*target)\b"), "manual_trading"),
    (re.compile(r"\b(who\s*are\s*you|what\s*are\s*you|about\s*madhav|who\s*is\s*madhav)\b"), "madhav"),
    (re.compile(r"\b(dashboard)\b"), "dashboard"),
    (re.compile(r"\b(setting|configure|env\s*var)\b"), "settings"),
    (re.compile(r"\b(how\s*(does\s*)?(this\s*)?(app|application|platform|system|quantflux)\s*work|"
                r"explain\s*(this\s*)?(app|application|platform|system|quantflux)|"
                r"overview|architecture)\b"),
     "app_overview"),
]


def _match_intent(q: str) -> str | None:
    norm = _normalise(q)
    for pat, topic in _INTENTS:
        if pat.search(norm):
            return topic
    return None


# ────────────────────────────────────────────────────────────────────────
# 2.  Doc fallback
# ────────────────────────────────────────────────────────────────────────

DOCS = [
    ("knowledge_base.md",              "Knowledge Base"),
    ("README.md",                      "README"),
    ("application_documentation.html", "Application Docs"),
    ("strategy_documentation.html",    "Strategy Docs"),
    ("steps.md",                       "Steps"),
]


def _strip_html(s: str) -> str:
    s = re.sub(r"<script[\s\S]*?</script>", " ", s, flags=re.I)
    s = re.sub(r"<style[\s\S]*?</style>",  " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    return s


def _split_sections(text: str, source: str) -> list[dict]:
    out: list[dict] = []
    if source.endswith(".html"):
        cleaned = _strip_html(text)
        for chunk in re.split(r"\n\s*\n", cleaned):
            chunk = chunk.strip()
            if len(chunk) < 60:
                continue
            title = chunk[:80].split(".")[0]
            out.append({"title": title, "body": chunk[:1500], "source": source})
        return out

    lines = text.splitlines()
    cur_title, cur_body = "Intro", []
    for ln in lines:
        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            if cur_body:
                out.append({
                    "title": cur_title,
                    "body": "\n".join(cur_body).strip()[:1800],
                    "source": source,
                })
            cur_title = m.group(2).strip()
            cur_body = []
        else:
            cur_body.append(ln)
    if cur_body:
        out.append({
            "title": cur_title,
            "body": "\n".join(cur_body).strip()[:1800],
            "source": source,
        })
    return out


@lru_cache(maxsize=1)
def _load_index() -> list[dict]:
    sections: list[dict] = []
    for fname, label in DOCS:
        p = ROOT / fname
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            logger.warning("Madhav: could not read %s: %s", fname, exc)
            continue
        for s in _split_sections(text, fname):
            s["label"] = label
            sections.append(s)
    logger.info("Madhav indexed %d sections from %d docs",
                len(sections), len(DOCS))
    return sections


def _tokenize(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) >= 2]


_STOPWORDS = set(
    "the and for what how does work why when where which who can will should "
    "with from this that have has had are was were been being into about your "
    "you yours mine over under more less than then would could just like need "
    "tell explain show give make want use using used".split()
)


def _score_section(qtok: set[str], sec: dict) -> float:
    body_tokens = _tokenize(sec["title"]) * 3 + _tokenize(sec["body"])
    if not body_tokens:
        return 0.0
    bag: dict[str, int] = {}
    for t in body_tokens:
        bag[t] = bag.get(t, 0) + 1
    return float(sum(bag.get(q, 0) for q in qtok))


def _humanise(md: str) -> str:
    """Turn raw markdown into something that reads like a paragraph."""
    lines: list[str] = []
    in_code = False
    for ln in md.splitlines():
        stripped = ln.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        # Drop markdown table separator rows like |---|---|
        if re.match(r"^\|?\s*:?-{2,}", stripped) and "|" in stripped:
            continue
        # Strip table pipes → " • "  separated cells
        if "|" in stripped and not in_code:
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            cells = [c for c in cells if c]
            if cells:
                lines.append(" • ".join(cells))
                continue
        m = re.match(r"^[\-\*\+]\s+(.*)$", stripped)
        if m:
            lines.append(f"• {m.group(1)}")
            continue
        lines.append(ln)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# Suggestion chips shown in the chat UI — user can click to ask.
SUGGESTIONS: list[dict] = [
    {"label": "How many strategies?",        "q": "how many strategies are there"},
    {"label": "Explain Strategy 1",          "q": "explain strategy 1"},
    {"label": "Explain Strategy 3",          "q": "explain strategy 3"},
    {"label": "What is the kill switch?",    "q": "what is the kill switch"},
    {"label": "Auto-squareoff at 15:15",     "q": "explain auto squareoff"},
    {"label": "Advanced P&L Fence",          "q": "explain pnl fence"},
    {"label": "Day-Loss Control",            "q": "explain loss control"},
    {"label": "Manual trading flow",         "q": "explain manual trading"},
    {"label": "Modify an order",             "q": "how do I modify an order"},
    {"label": "Attach SL / TGT after fill",  "q": "how to attach SL or target after fill"},
    {"label": "Dashboard overview",          "q": "explain the dashboard"},
    {"label": "How does the app work?",      "q": "how does the application work"},
    {"label": "Who are you?",                "q": "who are you"},
]


# ── routes ───────────────────────────────────────────────────────────────

class AskPayload(BaseModel):
    question: str
    top_k: int = 3


@router.post("/ask")
async def ask(payload: AskPayload, _user_id: int = Depends(login_required)):
    q = (payload.question or "").strip()
    if not q:
        return {
            "answer": (
                "Hi, I'm **Madhav** 👋 — your QuantFlux assistant.\n\n"
                "Tap any suggestion below, or type your own question."
            ),
            "sources": [],
            "suggestions": SUGGESTIONS,
        }

    # 1) Curated intent match — always preferred.
    topic_id = _match_intent(q)
    if topic_id and topic_id in TOPICS:
        t = TOPICS[topic_id]
        return {
            "answer": f"**{t['title']}**\n\n{t['answer']}",
            "sources": [{"title": t["title"], "label": "Madhav",
                         "source": "curated", "snippet": t["answer"][:280]}],
            "suggestions": [],
        }

    # 2) Doc fallback.
    qtok = set(_tokenize(q)) - _STOPWORDS
    if not qtok:
        return {
            "answer": ("I didn't quite catch that. Try one of these or rephrase "
                       "with a bit more detail:"),
            "sources": [],
            "suggestions": SUGGESTIONS,
        }

    index = _load_index()
    scored: list[tuple[float, dict]] = []
    for sec in index:
        sc = _score_section(qtok, sec)
        if sc > 0:
            scored.append((sc, sec))
    scored.sort(key=lambda x: -x[0])
    top = scored[: max(1, min(payload.top_k, 5))]

    if not top:
        return {
            "answer": (
                "I couldn't find that in the QuantFlux docs. Pick one of "
                "these common questions, or rephrase yours:"
            ),
            "sources": [],
            "suggestions": SUGGESTIONS,
        }

    best = top[0][1]
    body = _humanise(best["body"])[:1200].strip()
    answer = f"**{best['title']}**\n\n{body}"
    sources = [
        {"title": s["title"], "label": s["label"], "source": s["source"],
         "snippet": _humanise(s["body"])[:280]}
        for _, s in top
    ]
    return {"answer": answer, "sources": sources, "suggestions": []}


@router.get("/topics")
async def topics(_user_id: int = Depends(login_required)):
    """Return the FAQ suggestion chips for the chat UI."""
    return {"suggestions": SUGGESTIONS}


@router.post("/reload")
async def reload(_user_id: int = Depends(login_required)):
    """Force re-index of docs (call after editing knowledge_base.md)."""
    _load_index.cache_clear()
    n = len(_load_index())
    return {"status": "ok", "indexed_sections": n}
