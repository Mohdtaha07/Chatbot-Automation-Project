"""
chatbot.py — Matrix Assistant (v2)

v1 was a keyword→canned-string lookup. This version is *data aware*: the
frontend passes the current `job_id` (or the visible rows) with every message,
and the assistant answers questions about the actual listings on screen:

    "what's the cheapest one?"            -> the real listing + price + link
    "best rated laptop under 60k"         -> filtered, sorted, formatted
    "which store is cheaper?"             -> compares real minima + the delta
    "any hp options?"                     -> brand filter over the result set
    "show me 16gb rtx 4050 deals"         -> spec filter
    "biggest discount"                    -> MRP-vs-price ranking
    "average price on flipkart"           -> aggregate stats

It stays 100% offline/deterministic — no API keys, no external calls — and
falls back to the FAQ knowledge base for how-to questions.

Public API
----------
    chatbot_bp                    Flask blueprint (POST /api/chat)
    register_data_source(fn)      fn(job_id) -> {"query":..., "rows":[...], "stats":{...}}
    answer(message, rows, stats)  pure function, unit-testable
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

chatbot_bp = Blueprint("chatbot", __name__)

# ---------------------------------------------------------------------------
# Data source wiring (app.py injects a lookup so the bot can see live results)
# ---------------------------------------------------------------------------
_DATA_SOURCE: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None


def register_data_source(fn: Callable[[str], Optional[Dict[str, Any]]]) -> None:
    global _DATA_SOURCE
    _DATA_SOURCE = fn


# ---------------------------------------------------------------------------
# FAQ knowledge base (fallback / how-to layer)
# ---------------------------------------------------------------------------
KNOWLEDGE_BASE: List[Dict[str, Any]] = [
    {
        "id": "howto",
        # "how do i use" was added because index.html's own quick chip is literally
        # "How do I use this?", which previously matched nothing and fell through to
        # the generic "I'm not sure how to read that one" reply.
        "keywords": ["how to use", "how do i use", "use the website", "getting started", "how it works",
                     "guide", "help me", "what can you do", "instructions"],
        "answer": ("Here's the quick tour:\n"
                   "1. Type a product into **Search product** (e.g. \"gaming laptop under 80000\").\n"
                   "2. Pick how many result pages to pull per store — 2 is a good default.\n"
                   "3. Hit **Compare prices**. Amazon and Flipkart are scraped in parallel and the "
                   "log streams live.\n"
                   "4. Use the filters, sort menu and KPI cards to explore, then export with **CSV** or **XLSX**.\n\n"
                   "Once results are on screen you can also ask me things like *\"cheapest under 50k\"* "
                   "or *\"best rated Flipkart option\"*."),
    },
    {
        "id": "platforms",
        "keywords": ["which platform", "which store", "what stores", "amazon or flipkart",
                     "sites do you", "sources"],
        "answer": ("I compare **Amazon India** and **Flipkart** side by side. Both are scraped "
                   "concurrently with a real browser, then listings are matched across stores using "
                   "brand + model-number similarity so the price difference column compares like with like."),
    },
    {
        "id": "export",
        "keywords": ["download", "csv", "export", "excel", "xlsx", "save results", "spreadsheet"],
        "answer": ("Use the **CSV** button in the header for the raw comparison table, or the caret "
                   "next to it for **XLSX** (formatted, with a summary sheet) and **JSON**. "
                   "Exports respect your active filters."),
    },
    {
        "id": "filters",
        "keywords": ["filter", "sort", "narrow", "search within", "specs", "specification",
                     "price range", "budget slider", "hide sponsored"],
        "answer": ("Above the table you can:\n"
                   "• switch between **All / Amazon / Flipkart**\n"
                   "• type spec keywords like `16GB`, `i7`, `RTX 4050`\n"
                   "• set a **min/max price** window\n"
                   "• sort by price, rating, discount or **value score**\n"
                   "• toggle **Hide sponsored** and **Matched only** to see just cross-store pairs."),
    },
    {
        "id": "status",
        "keywords": ["status", "idle", "scanning", "progress", "stuck", "how long", "cancel"],
        "answer": ("The pill in the header shows the run state: **Idle → Scanning (with %) → Done**. "
                   "A 2-page scan of both stores usually takes 20–45 seconds. "
                   "You can stop a run any time with the **Cancel** button next to the progress bar."),
    },
    {
        "id": "valuescore",
        "keywords": ["value score", "what is value", "how is value", "scoring", "ranked"],
        "answer": ("**Value score** (0–100) blends four signals so you don't have to eyeball them:\n"
                   "• 42% price position within the result set\n"
                   "• 30% star rating\n"
                   "• 16% review volume (credibility)\n"
                   "• 12% discount off MRP\n"
                   "Sort by it to find the best all-round deal rather than just the cheapest."),
    },
    {
        "id": "accuracy",
        "keywords": ["accurate", "trust", "real time", "outdated", "wrong price", "match score"],
        "answer": ("Prices are scraped live at run time, so they're as current as the store pages. "
                   "Cross-store matches carry a **match score** — hover a *vs. Market* cell to see it. "
                   "Anything below ~0.42 is treated as “no reliable match” rather than guessing."),
    },
    {
        "id": "blocked",
        "keywords": ["captcha", "blocked", "no results", "zero results", "empty", "failed", "error"],
        "answer": ("If a store returns nothing it's usually a bot wall. Try: fewer pages, wait a minute, "
                   "run with `headless: false` once to solve a CAPTCHA and warm cookies, or check the "
                   "scan log — each retry and block is reported there. A failure on one store no longer "
                   "kills the other store's results."),
    },
]

GREETINGS = ("hi", "hello", "hey", "yo", "namaste", "hola", "good morning", "good evening", "good afternoon")
THANKS = ("thanks", "thank you", "thx", "ty", "appreciate")


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
def _money(n: Optional[float]) -> str:
    if n is None:
        return "—"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    # Indian digit grouping
    s = f"{int(round(n)):,}"
    x = str(int(round(n)))
    if len(x) > 3:
        last3, rest = x[-3:], x[:-3]
        parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        s = ",".join(parts + [last3])
    return f"₹{s}"


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        m = re.search(r"\d+(?:\.\d+)?", str(v).replace(",", ""))
        f = float(m.group(0)) if m else None
    return f


_BUDGET_RE = re.compile(
    r"(?:under|below|less than|cheaper than|within|upto|up to|max|<=?)\s*"
    r"(?:rs\.?|inr|₹)?\s*([\d,.]+)\s*(k|lakh|lac|l)?", re.I)
_ABOVE_RE = re.compile(
    r"(?:over|above|more than|at least|minimum|min|>=?)\s*"
    r"(?:rs\.?|inr|₹)?\s*([\d,.]+)\s*(k|lakh|lac|l)?", re.I)
_BETWEEN_RE = re.compile(
    r"between\s*(?:rs\.?|inr|₹)?\s*([\d,.]+)\s*(k|lakh|lac|l)?\s*(?:and|to|-|–)\s*"
    r"(?:rs\.?|inr|₹)?\s*([\d,.]+)\s*(k|lakh|lac|l)?", re.I)


def _scale(value: str, suffix: Optional[str]) -> float:
    n = float(str(value).replace(",", ""))
    s = (suffix or "").lower()
    if s == "k":
        n *= 1_000
    elif s in {"lakh", "lac", "l"}:
        n *= 100_000
    elif n < 1000:  # "under 60" almost always means 60k in this context
        n *= 1_000
    return n


def parse_budget(text: str) -> Tuple[Optional[float], Optional[float]]:
    m = _BETWEEN_RE.search(text)
    if m:
        lo = _scale(m.group(1), m.group(2))
        hi = _scale(m.group(3), m.group(4))
        return (min(lo, hi), max(lo, hi))
    lo = hi = None
    m = _BUDGET_RE.search(text)
    if m:
        hi = _scale(m.group(1), m.group(2))
    m = _ABOVE_RE.search(text)
    if m:
        lo = _scale(m.group(1), m.group(2))
    return lo, hi


SPEC_HINTS = [
    (re.compile(r"\b(\d{1,2})\s?gb\b(?!\s?(?:ssd|hdd|storage|rom))", re.I), lambda m: f"{m.group(1)}gb"),
    (re.compile(r"\b(rtx\s?\d{4}|gtx\s?\d{3,4})\b", re.I), lambda m: m.group(1).replace(" ", "")),
    (re.compile(r"\b(i[3579])\b", re.I), lambda m: m.group(1)),
    (re.compile(r"\bryzen\s?([3579])\b", re.I), lambda m: f"ryzen {m.group(1)}"),
    (re.compile(r"\b(\d{3,4})\s?gb\s?ssd\b", re.I), lambda m: f"{m.group(1)}gb ssd"),
    (re.compile(r"\b(\d\s?tb)\b", re.I), lambda m: m.group(1)),
    (re.compile(r"\b(\d{2,3})\s?hz\b", re.I), lambda m: f"{m.group(1)}hz"),
    (re.compile(r"\b(5g|4g|amoled|oled|ips|retina|touchscreen|wireless|bluetooth|noise cancell\w*)\b", re.I),
     lambda m: m.group(1).lower()),
]


def parse_specs(text: str) -> List[str]:
    out: List[str] = []
    for pat, fmt in SPEC_HINTS:
        for m in pat.finditer(text):
            token = fmt(m).lower()
            if token not in out:
                out.append(token)
    return out


def parse_platform(text: str) -> Optional[str]:
    t = text.lower()
    if "amazon" in t and "flipkart" in t:
        return None
    if "amazon" in t or re.search(r"\bamzn?\b", t):
        return "Amazon"
    if "flipkart" in t or re.search(r"\bfk\b", t):
        return "Flipkart"
    return None


def parse_brand(text: str, rows: List[Dict[str, Any]]) -> Optional[str]:
    brands = {str(r.get("brand") or "").strip().lower() for r in rows if r.get("brand")}
    t = text.lower()
    for b in sorted(brands, key=len, reverse=True):
        if b and len(b) > 1 and re.search(rf"\b{re.escape(b)}\b", t):
            return b
    return None


def parse_topn(text: str, default: int = 3) -> int:
    m = re.search(r"\b(?:top|best|first|show me|list)\s+(\d{1,2})\b", text.lower())
    if m:
        return max(1, min(10, int(m.group(1))))
    if re.search(r"\ball\b", text.lower()):
        return 10
    return default


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------
def _clean(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in rows or []:
        price = _num(r.get("price"))
        if price is None or price <= 0:
            continue
        c = dict(r)
        c["price"] = price
        c["rating"] = _num(r.get("rating"))
        c["reviews"] = _num(r.get("reviews")) or 0
        c["mrp"] = _num(r.get("mrp"))
        c["value_score"] = _num(r.get("value_score"))
        c["discount_pct"] = _num(r.get("discount_pct"))
        c["title"] = str(r.get("title") or "").strip()
        c["platform"] = str(r.get("platform") or "").strip()
        c["brand"] = str(r.get("brand") or "").strip()
        out.append(c)
    return out


def _apply_filters(rows: List[Dict[str, Any]], text: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    notes: List[str] = []
    out = rows

    platform = parse_platform(text)
    if platform:
        out = [r for r in out if r["platform"] == platform]
        notes.append(f"on {platform}")

    brand = parse_brand(text, rows)
    if brand:
        out = [r for r in out if brand in (r["brand"].lower() + " " + r["title"].lower())]
        notes.append(f"brand “{brand.title()}”")

    lo, hi = parse_budget(text)
    if lo is not None:
        out = [r for r in out if r["price"] >= lo]
    if hi is not None:
        out = [r for r in out if r["price"] <= hi]
    if lo is not None and hi is not None:
        notes.append(f"between {_money(lo)} and {_money(hi)}")
    elif hi is not None:
        notes.append(f"under {_money(hi)}")
    elif lo is not None:
        notes.append(f"over {_money(lo)}")

    for spec in parse_specs(text):
        key = spec.replace(" ", "")
        filtered = [r for r in out
                    if key in r["title"].lower().replace(" ", "")
                    or key in str(r.get("specs") or "").lower().replace(" ", "")]
        if filtered:
            out = filtered
            notes.append(spec.upper())
    return out, notes


def _fmt_listing(r: Dict[str, Any], idx: Optional[int] = None) -> str:
    title = r["title"]
    if len(title) > 96:
        title = title[:93].rstrip() + "…"
    bits = [f"{_money(r['price'])}", r["platform"] or "—"]
    if r.get("rating"):
        stars = f"★ {r['rating']:.1f}"
        if r.get("reviews"):
            stars += f" ({int(r['reviews']):,})"
        bits.append(stars)
    if r.get("discount_pct"):
        bits.append(f"{int(r['discount_pct'])}% off")
    prefix = f"{idx}. " if idx else "• "
    line = f"{prefix}{title}\n   {' · '.join(bits)}"
    link = r.get("link") or ""
    if link.startswith("http"):
        line += f"\n   {link}"
    return line


def _stats_for(rows: List[Dict[str, Any]], platform: Optional[str] = None) -> Optional[Dict[str, float]]:
    sub = [r for r in rows if not platform or r["platform"] == platform]
    prices = [r["price"] for r in sub]
    if not prices:
        return None
    prices_sorted = sorted(prices)
    mid = len(prices_sorted) // 2
    median = prices_sorted[mid] if len(prices_sorted) % 2 else (prices_sorted[mid - 1] + prices_sorted[mid]) / 2
    return {
        "count": len(prices),
        "min": min(prices),
        "max": max(prices),
        "avg": sum(prices) / len(prices),
        "median": median,
    }


# ---------------------------------------------------------------------------
# Intent handlers
# ---------------------------------------------------------------------------
def _intent(text: str) -> str:
    t = text.lower()
    if any(t.startswith(g) or t == g for g in GREETINGS):
        return "greeting"
    if any(w in t for w in THANKS):
        return "thanks"
    if re.search(r"\b(which (store|platform|site)|who is cheaper|amazon or flipkart|cheaper overall|"
                 r"where should i buy|better deal overall)\b", t):
        return "compare_platforms"
    if re.search(r"\b(cheap|cheapest|lowest|least expensive|budget|affordable|minimum price)\b", t):
        return "cheapest"
    if re.search(r"\b(expensive|costliest|highest|premium|most expensive|maximum price)\b", t):
        return "most_expensive"
    if re.search(r"\b(best rated|highest rated|top rated|best rating|most reviewed|reviews)\b", t):
        return "best_rated"
    if re.search(r"\b(discount|off|deal of|savings|save the most|biggest drop|mrp)\b", t):
        return "best_discount"
    if re.search(r"\b(best value|value for money|best overall|recommend|suggest|should i buy|worth it|best pick)\b", t):
        return "best_value"
    if re.search(r"\b(average|mean|median|typical|price range|spread|statistics|stats|summary|overview|how many)\b", t):
        return "stats"
    if re.search(r"\b(match(es|ed)?|same product|both stores|cross[- ]store|"
                 r"price (difference|differences|gap|gaps)|difference between|biggest gap)\b", t):
        return "matches"
    if re.search(r"\b(show|list|find|any|options|what.*(available|have))\b", t):
        return "list"
    return "unknown"


def _faq(text: str) -> Optional[str]:
    t = text.lower()
    best, best_hits = None, 0
    for item in KNOWLEDGE_BASE:
        hits = sum(1 for kw in item["keywords"] if kw in t)
        if hits > best_hits:
            best, best_hits = item, hits
    return best["answer"] if best else None


def _no_data_hint(intent: str) -> str:
    return ("I don't have any results loaded yet, so I can't answer that against real listings.\n\n"
            "Run a search above (or click **Preview with sample data**) and then ask me again — "
            "I can find the cheapest option, filter by budget and specs, compare the two stores, "
            "or point you at the best value pick.")


def answer(message: str,
           rows: Optional[List[Dict[str, Any]]] = None,
           stats: Optional[Dict[str, Any]] = None,
           query: str = "") -> Dict[str, Any]:
    """Pure, testable core. Returns {"reply": str, "intent": str, "used_data": bool}."""
    text = (message or "").strip()
    if not text:
        return {"reply": "Ask me anything about the results — try “cheapest under 50k”.",
                "intent": "empty", "used_data": False}

    data = _clean(rows or [])
    intent = _intent(text)

    if intent == "greeting":
        extra = (f" You currently have **{len(data)} listings** loaded"
                 + (f" for “{query}”" if query else "") + ".") if data else ""
        return {"reply": f"Hey! I'm the Matrix Assistant.{extra}\n\n"
                         "Ask me things like *“cheapest under 60k”*, *“best rated Flipkart option”*, "
                         "*“biggest discount”* or *“which store is cheaper?”*.",
                "intent": intent, "used_data": bool(data)}

    if intent == "thanks":
        return {"reply": "Anytime! Ping me if you want a different cut of the data.",
                "intent": intent, "used_data": False}

    # How-to questions always take the FAQ path.
    faq_first = re.search(r"\b(how do i|how to|how can i|what is|what does|explain|where is)\b", text.lower())
    if faq_first:
        faq = _faq(text)
        if faq:
            return {"reply": faq, "intent": "faq", "used_data": False}

    if intent in {"cheapest", "most_expensive", "best_rated", "best_discount", "best_value",
                  "compare_platforms", "stats", "matches", "list"} and not data:
        faq = _faq(text)
        return {"reply": faq or _no_data_hint(intent), "intent": intent, "used_data": False}

    filtered, notes = _apply_filters(data, text)
    scope = (" " + ", ".join(notes)) if notes else ""

    if not filtered and data:
        lo, hi = parse_budget(text)
        cheapest = min(data, key=lambda r: r["price"])
        msg = f"Nothing in the current results matches{scope}."
        if hi is not None:
            msg += f" The cheapest listing overall is {_money(cheapest['price'])} on {cheapest['platform']}."
        return {"reply": msg + "\n\nTry loosening the budget or dropping a spec keyword.",
                "intent": intent, "used_data": True}

    n = parse_topn(text)

    if intent == "cheapest":
        picks = sorted(filtered, key=lambda r: r["price"])[:n]
        head = f"Cheapest{scope}:" if scope else "Cheapest listings right now:"
        body = "\n".join(_fmt_listing(r, i + 1) for i, r in enumerate(picks))
        tail = ""
        if len(picks) > 1:
            spread = picks[-1]["price"] - picks[0]["price"]
            if spread > 0:
                tail = f"\n\nThat's a {_money(spread)} spread across the top {len(picks)}."
        return {"reply": f"{head}\n{body}{tail}", "intent": intent, "used_data": True}

    if intent == "most_expensive":
        picks = sorted(filtered, key=lambda r: -r["price"])[:n]
        body = "\n".join(_fmt_listing(r, i + 1) for i, r in enumerate(picks))
        return {"reply": f"Priciest{scope or ' listings'}:\n{body}", "intent": intent, "used_data": True}

    if intent == "best_rated":
        rated = [r for r in filtered if r.get("rating")]
        if not rated:
            return {"reply": f"None of the listings{scope} came back with a star rating — "
                             "the stores sometimes omit it on sponsored cards. "
                             "Try sorting by **Value score** instead.",
                    "intent": intent, "used_data": True}
        rated.sort(key=lambda r: (round(r["rating"], 1), r["reviews"]), reverse=True)
        body = "\n".join(_fmt_listing(r, i + 1) for i, r in enumerate(rated[:n]))
        return {"reply": f"Best rated{scope}:\n{body}", "intent": intent, "used_data": True}

    if intent == "best_discount":
        disc = [r for r in filtered if r.get("discount_pct")]
        if not disc:
            return {"reply": f"No MRP was published for the listings{scope}, so I can't rank discounts. "
                             "The **Value score** column still factors in price position and rating.",
                    "intent": intent, "used_data": True}
        disc.sort(key=lambda r: r["discount_pct"], reverse=True)
        body = "\n".join(_fmt_listing(r, i + 1) for i, r in enumerate(disc[:n]))
        top = disc[0]
        saved = (top.get("mrp") or 0) - top["price"]
        return {"reply": f"Biggest discounts{scope}:\n{body}\n\n"
                         f"Top saving is {_money(saved)} off MRP.",
                "intent": intent, "used_data": True}

    if intent == "best_value":
        scored = [r for r in filtered if r.get("value_score") is not None]
        if scored:
            scored.sort(key=lambda r: r["value_score"], reverse=True)
            top = scored[0]
            rest = scored[1:n]
            reply = (f"My pick{scope}: **{top['title'][:100]}**\n"
                     f"{_money(top['price'])} on {top['platform']}"
                     + (f" · ★ {top['rating']:.1f}" if top.get("rating") else "")
                     + f" · value score {top['value_score']:.0f}/100")
            if top.get("link", "").startswith("http"):
                reply += f"\n{top['link']}"
            if rest:
                reply += "\n\nRunners-up:\n" + "\n".join(_fmt_listing(r) for r in rest)
            reply += ("\n\nValue score = 42% price position, 30% rating, 16% review volume, 12% discount.")
            return {"reply": reply, "intent": intent, "used_data": True}
        cheap = min(filtered, key=lambda r: r["price"])
        return {"reply": f"Best price I can see{scope}:\n{_fmt_listing(cheap)}",
                "intent": intent, "used_data": True}

    if intent == "compare_platforms":
        a, f = _stats_for(filtered, "Amazon"), _stats_for(filtered, "Flipkart")
        if not a and not f:
            return {"reply": "I don't have priced listings from either store in the current view.",
                    "intent": intent, "used_data": True}
        if not a:
            return {"reply": f"Only Flipkart returned priced listings ({f['count']}), "
                             f"from {_money(f['min'])} to {_money(f['max'])}.",
                    "intent": intent, "used_data": True}
        if not f:
            return {"reply": f"Only Amazon returned priced listings ({a['count']}), "
                             f"from {_money(a['min'])} to {_money(a['max'])}.",
                    "intent": intent, "used_data": True}
        if a["min"] < f["min"]:
            winner, delta = "Amazon", f["min"] - a["min"]
        elif f["min"] < a["min"]:
            winner, delta = "Flipkart", a["min"] - f["min"]
        else:
            winner, delta = None, 0.0

        lines = [
            f"**Amazon** — {a['count']} listings · from {_money(a['min'])} · avg {_money(a['avg'])}",
            f"**Flipkart** — {f['count']} listings · from {_money(f['min'])} · avg {_money(f['avg'])}",
        ]
        verdict = (f"\n**{winner} wins on entry price** by {_money(delta)}."
                   if winner else "\nBoth stores start at exactly the same price.")
        if a["avg"] != f["avg"]:
            cheaper_avg = "Amazon" if a["avg"] < f["avg"] else "Flipkart"
            verdict += f" On average across the whole set, {cheaper_avg} is cheaper."
        return {"reply": "\n".join(lines) + verdict, "intent": intent, "used_data": True}

    if intent == "stats":
        s = _stats_for(filtered)
        a, f = _stats_for(filtered, "Amazon"), _stats_for(filtered, "Flipkart")
        lines = [f"**{s['count']} listings**{scope}",
                 f"Range {_money(s['min'])} – {_money(s['max'])}",
                 f"Average {_money(s['avg'])} · median {_money(s['median'])}"]
        if a:
            lines.append(f"Amazon: {a['count']} · from {_money(a['min'])}")
        if f:
            lines.append(f"Flipkart: {f['count']} · from {_money(f['min'])}")
        rated = [r["rating"] for r in filtered if r.get("rating")]
        if rated:
            lines.append(f"Average rating ★ {sum(rated)/len(rated):.2f} across {len(rated)} rated items")
        return {"reply": "\n".join(lines), "intent": intent, "used_data": True}

    if intent == "matches":
        matched = [r for r in filtered if _num(r.get("match_score")) and _num(r.get("price_diff")) is not None]
        if not matched:
            return {"reply": "I couldn't confidently match any product across both stores in this set. "
                             "That usually means the two catalogues carry different variants — "
                             "widen the search or add more pages.",
                    "intent": intent, "used_data": True}
        matched.sort(key=lambda r: abs(_num(r.get("price_diff")) or 0), reverse=True)
        lines = []
        for r in matched[:n]:
            diff = _num(r.get("price_diff")) or 0
            cheaper = r.get("cheaper_on") or ("—")
            title = r["title"][:80]
            lines.append(f"• {title}\n   {_money(r['price'])} on {r['platform']} · "
                         f"gap {_money(abs(diff))} · cheaper on **{cheaper}**")
        return {"reply": f"Largest cross-store price gaps ({len(matched)} matched pairs):\n"
                         + "\n".join(lines),
                "intent": intent, "used_data": True}

    if intent == "list" or notes:
        picks = sorted(filtered, key=lambda r: r["price"])[:max(n, 3)]
        body = "\n".join(_fmt_listing(r, i + 1) for i, r in enumerate(picks))
        return {"reply": f"Found **{len(filtered)}** listings{scope} — cheapest first:\n{body}",
                "intent": "list", "used_data": True}

    faq = _faq(text)
    if faq:
        return {"reply": faq, "intent": "faq", "used_data": False}

    if data:
        s = _stats_for(data)
        return {"reply": ("I'm not sure how to read that one. With the current "
                          f"{s['count']} listings I can answer:\n"
                          "• “cheapest under 50k”\n"
                          "• “best rated Flipkart option”\n"
                          "• “biggest discount”\n"
                          "• “which store is cheaper?”\n"
                          "• “average price” / “price range”\n"
                          "• “biggest price differences between stores”"),
                "intent": "unknown", "used_data": True}

    return {"reply": _no_data_hint(intent), "intent": "unknown", "used_data": False}


# Back-compat shim for anything importing the v1 helper.
def get_bot_response(user_text: str) -> str:
    return answer(user_text)["reply"]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
@chatbot_bp.post("/api/chat")
def chat():
    body = request.get_json(force=True, silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Message is required"}), 400

    rows: List[Dict[str, Any]] = body.get("rows") or []
    stats: Dict[str, Any] = body.get("stats") or {}
    query: str = (body.get("query") or "").strip()

    job_id = (body.get("job_id") or "").strip()
    if job_id and _DATA_SOURCE and not rows:
        snapshot = _DATA_SOURCE(job_id)
        if snapshot:
            rows = snapshot.get("rows") or []
            stats = snapshot.get("stats") or stats
            query = snapshot.get("query") or query

    if len(rows) > 1200:  # keep the request cheap
        rows = rows[:1200]

    result = answer(message, rows=rows, stats=stats, query=query)
    return jsonify({
        "reply": result["reply"],
        "intent": result["intent"],
        "used_data": result["used_data"],
        "context_rows": len(rows),
    })


@chatbot_bp.get("/api/chat/suggestions")
def suggestions():
    """Context-aware quick chips for the UI."""
    job_id = (request.args.get("job_id") or "").strip()
    has_data = False
    if job_id and _DATA_SOURCE:
        snap = _DATA_SOURCE(job_id)
        has_data = bool(snap and snap.get("rows"))
    if has_data:
        chips = ["What's the cheapest option?", "Which store is cheaper?",
                 "Best value for money", "Biggest discount",
                 "Biggest price differences", "Average price"]
    else:
        chips = ["How do I use this?", "What stores do you compare?",
                 "How do I export results?", "What is the value score?"]
    return jsonify({"suggestions": chips})
