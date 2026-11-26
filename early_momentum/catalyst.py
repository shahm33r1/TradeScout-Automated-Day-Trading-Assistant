"""Classify news headlines into catalyst types, with quality and freshness.

Headlines are leads: the scanner lists the link for every item it scores, and a
human (or the session) reads the story before calling it a fact.
"""

import email.utils
import re

# (category, base quality 0-20, pattern). First match wins, so order matters.
POSITIVE = [
    ("FDA approval", 20, r"\bFDA\b.*\b(approv|clear(s|ed|ance)|grant(s|ed)|breakthrough|fast track|orphan)"),
    ("clinical trial", 14, r"\b(phase\s*(1|2|3|i{1,3})|topline|primary endpoint|clinical (trial|data|results)|trial results)\b"),
    ("merger / acquisition", 16, r"\b(acquir(e|es|ed|ing|sition)|merger|to be acquired|buyout|take[- ]private|definitive agreement)\b"),
    ("government contract", 15, r"\b(department of (defense|energy|war)|DoD|army|navy|air force|NASA|government contract|federal contract|DARPA)\b"),
    ("major contract", 14, r"\b(contract|purchase order|award(ed|s)?|supply agreement)\b.*\$?\d"),
    ("earnings surprise", 12, r"\b(beat|record revenue|raises? guidance|tops estimates|exceed(s|ed) (expectations|estimates)|profit(ability)?)\b"),
    ("partnership", 10, r"\b(partner(s|ship)?|collaborat(e|ion)|strategic alliance|joint venture|licens(e|ing) agreement)\b"),
    ("analyst upgrade", 8, r"\b(upgrade[sd]?|initiat(es|ed) .* buy|price target (raised|increase))\b"),
    ("product announcement", 8, r"\b(launch(es|ed)?|unveil(s|ed)?|introduc(es|ed)|announces? (new|first))\b"),
]
NEGATIVE = [
    ("offering / dilution", r"\b(public offering|registered direct|private placement|at[- ]the[- ]market|ATM program|priced? (its|an|a) .*offering|warrants?|convertible (note|debenture|preferred))\b"),
    ("reverse split", r"\breverse (stock )?split\b"),
    ("delisting / compliance", r"\b(delist|deficiency notice|non-?compliance|minimum bid)\b"),
    ("going concern", r"\bgoing concern\b"),
    ("legal / investigation", r"\b(investigation|lawsuit|class action|subpoena|SEC charges|fraud)\b"),
    ("downgrade", r"\bdowngrade[sd]?\b"),
    ("halt", r"\bhalt(ed|s)?\b"),
]


def age_hours(published, now_utc):
    try:
        dt = email.utils.parsedate_to_datetime(published)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return (now_utc - dt).total_seconds() / 3600.0


def freshness(hours):
    if hours is None:
        return 0.3
    if hours < 0:
        return 0.0          # a future timestamp: do not trust it
    if hours <= 6:
        return 1.0
    if hours <= 24:
        return 0.75
    if hours <= 72:
        return 0.4
    return 0.0


def classify(items, now_utc, sym=None):
    """items [{title, link, published, outlet}] -> {best, positives, negatives}."""
    pos, neg, seen = [], [], set()
    for it in items or []:
        title = it.get("title") or ""
        key = re.sub(r"\W+", " ", title.lower()).strip()[:90]
        if not title or key in seen:
            continue
        seen.add(key)
        h = age_hours(it.get("published"), now_utc)
        f = freshness(h)
        if f == 0.0:
            continue
        rec = {"title": title, "link": it.get("link"), "outlet": it.get("outlet"),
               "published": it.get("published"), "age_hours": None if h is None else round(h, 1)}
        for cat, rx in NEGATIVE:
            if re.search(rx, title, re.I):
                neg.append(dict(rec, category=cat))
                break
        for cat, q, rx in POSITIVE:
            if re.search(rx, title, re.I):
                pos.append(dict(rec, category=cat, quality=round(q * f, 1)))
                break
    pos.sort(key=lambda r: -r["quality"])
    return {"best": pos[0] if pos else None, "positives": pos[:5], "negatives": neg[:5]}
