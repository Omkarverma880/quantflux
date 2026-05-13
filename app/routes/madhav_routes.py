"""
Madhav — QuantFlux's built-in help chatbot.

No external LLM required. Performs simple keyword + section matching
against:
  • knowledge_base.md
  • README.md
  • application_documentation.html (stripped)
  • strategy_documentation.html (stripped)

Returns the most relevant section(s) for the user's question.
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

DOCS = [
    ("knowledge_base.md",            "Knowledge Base"),
    ("README.md",                    "README"),
    ("application_documentation.html", "Application Docs"),
    ("strategy_documentation.html",  "Strategy Docs"),
    ("steps.md",                     "Steps"),
]


# ── doc indexing ─────────────────────────────────────

def _strip_html(s: str) -> str:
    s = re.sub(r"<script[\s\S]*?</script>", " ", s, flags=re.I)
    s = re.sub(r"<style[\s\S]*?</style>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    return s


def _split_sections(text: str, source: str) -> list[dict]:
    """Split markdown by ## / ### headers, or HTML by H1-H4."""
    out = []
    if source.endswith(".html"):
        # treat as one big section — heuristic split on capitalized lines
        cleaned = _strip_html(text)
        # split on double newlines / headings
        chunks = re.split(r"\n\s*\n", cleaned)
        for i, ch in enumerate(chunks):
            ch = ch.strip()
            if len(ch) < 60:
                continue
            title = ch[:80].split(".")[0]
            out.append({"title": title, "body": ch[:1500], "source": source})
        return out

    # markdown
    lines = text.splitlines()
    cur_title, cur_body = "Intro", []
    for ln in lines:
        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            if cur_body:
                out.append({"title": cur_title, "body": "\n".join(cur_body).strip()[:1800],
                            "source": source})
            cur_title = m.group(2).strip()
            cur_body = []
        else:
            cur_body.append(ln)
    if cur_body:
        out.append({"title": cur_title, "body": "\n".join(cur_body).strip()[:1800],
                    "source": source})
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
    return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2]


_STOPWORDS = set("the and for what how does work why when where which who can will should "
                 "with from this that have has had are was were been being into about your "
                 "you yours mine over under more less than then would could just like need "
                 "tell explain show give make want use using used".split())


def _score_section(qtok: set[str], sec: dict) -> float:
    body_tokens = _tokenize(sec["title"]) * 3 + _tokenize(sec["body"])
    if not body_tokens:
        return 0
    bag = {}
    for t in body_tokens:
        bag[t] = bag.get(t, 0) + 1
    score = 0.0
    for q in qtok:
        if q in bag:
            score += bag[q]
    return score


# ── routes ───────────────────────────────────────────

class AskPayload(BaseModel):
    question: str
    top_k: int = 3


@router.post("/ask")
async def ask(payload: AskPayload, _user_id: int = Depends(login_required)):
    q = (payload.question or "").strip()
    if not q:
        return {"answer": "Hi, I'm Madhav 👋 — ask me anything about QuantFlux.",
                "sources": []}
    qtok = set(_tokenize(q)) - _STOPWORDS
    if not qtok:
        return {"answer": "Could you rephrase that with a bit more detail?",
                "sources": []}

    index = _load_index()
    scored = []
    for sec in index:
        sc = _score_section(qtok, sec)
        if sc > 0:
            scored.append((sc, sec))
    scored.sort(key=lambda x: -x[0])
    top = scored[: max(1, min(payload.top_k, 5))]

    if not top:
        return {
            "answer": ("I couldn't find that in the QuantFlux docs. "
                       "Try keywords like 'kill switch', 'manual trade', "
                       "'strategy 1', 'auto squareoff', 'risk fence'."),
            "sources": [],
        }

    best = top[0][1]
    answer_lines = [f"**{best['title']}** — _{best['label']}_", "",
                    best["body"][:1200].strip()]
    sources = [{"title": s["title"], "label": s["label"], "source": s["source"],
                "snippet": s["body"][:280]} for _, s in top]

    return {"answer": "\n".join(answer_lines), "sources": sources}


@router.post("/reload")
async def reload(_user_id: int = Depends(login_required)):
    """Force re-index of docs (e.g. after editing knowledge_base.md)."""
    _load_index.cache_clear()
    n = len(_load_index())
    return {"status": "ok", "indexed_sections": n}
