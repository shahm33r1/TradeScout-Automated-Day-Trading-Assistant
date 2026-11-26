"""Decide which changes are worth a notification, and never repeat one.

Alert types: new_match, unusual_volume, approaching_breakout, breakout,
stronger_momentum, risk. Each fires once per stock per day per key (a level, a
risk flag); stronger_momentum fires again only after a further score jump.
"""


def evaluate(rec, state, cfg):
    a = cfg["alerts"]
    sym = rec["ticker"]
    st = state.setdefault(sym, {"sent": [], "last_score": None, "risk_seen": []})
    out = []

    def fire(kind, key, text):
        k = "%s:%s" % (kind, key)
        if k in st["sent"]:
            return
        st["sent"].append(k)
        out.append({"ticker": sym, "type": kind, "text": text, "score": rec["score"], "price": rec["price"]})

    notified_before = st["last_score"] is not None
    best = (rec.get("catalyst") or {}).get("best")
    pm_ok = (rec["session"] == "premarket" and rec["score"] >= a.get("premarket_min_score", 25)
             and best and best["quality"] >= a.get("premarket_min_catalyst", 10)
             and rec["match"] != "outside criteria" and (rec["dilution"] or {}).get("level") != "high")
    if pm_ok and not notified_before:
        fire("new_match", "pm", "%s %s: premarket catalyst mover %+.1f%%, score %d (premarket volume unavailable). Catalyst: %s - %s" % (
            sym, rec.get("name") or "", rec["move_pct"] or 0, rec["score"], best["category"], best["title"]))
        st["last_score"] = rec["score"]
    elif rec["score"] >= a["min_score"] and not notified_before:
        fire("new_match", "1", "%s %s: new early-momentum candidate, score %d (%s), %s %+.1f%%" % (
            sym, rec.get("name") or "", rec["score"], rec["tier"], rec["session"], rec["move_pct"] or 0))
        st["last_score"] = rec["score"]
    elif notified_before and rec["score"] >= st["last_score"] + a["score_jump"]:
        fire("stronger_momentum", str(rec["score"]), "%s: momentum strengthening, score %d (was %d)" % (
            sym, rec["score"], st["last_score"]))
        st["last_score"] = rec["score"]

    # volume, approach and breakout alerts only for stocks that qualify (or already did)
    gate = rec["score"] >= a["min_score"] or st["last_score"] is not None
    extreme = (rec["rvol"] or 0) >= a.get("extreme_rvol", 5) and rec["score"] >= a.get("extreme_rvol_min_score", 35)
    if rec["signals"].get("unusual_volume") and (gate or extreme):
        best_c = (rec.get("catalyst") or {}).get("best")
        fire("unusual_volume", "1", "%s %s: unusual volume, %.1fx normal for this time of day, %+.1f%%, score %d%s" % (
            sym, rec.get("name") or "", rec["rvol"], rec["move_pct"] or 0, rec["score"],
            "" if best_c else " (no catalyst found: moves without news fade more often)"))
    key = rec["plan"]["key_level"]
    if key["price"] and rec["price"] < key["price"] and key["name"] != "round number":
        gap = (key["price"] / rec["price"] - 1) * 100
        if gap <= a["approach_pct"] and gate:
            fire("approaching_breakout", "%.4g" % key["price"], "%s: %.1f%% under the %s %s" % (
                sym, gap, key["name"], key["price"]))
    if rec["signals"].get("premarket_high_break") and gate:
        fire("breakout", "pmh", "%s: broke above the premarket high %s" % (sym, rec["premarket_high"]))

    # Risk alerts only for stocks the user has already heard about.
    if notified_before or st["last_score"] is not None:
        for f in rec["dilution"]["flags"]:
            if f["severity"] == "high" and f["what"] not in st["risk_seen"]:
                st["risk_seen"].append(f["what"])
                fire("risk", f["what"][:60], "%s RISK: %s" % (sym, f["what"]))
        for n in rec["catalyst"].get("negatives") or []:
            tag = "news:" + n["category"]
            if tag not in st["risk_seen"]:
                st["risk_seen"].append(tag)
                fire("risk", tag, "%s RISK: negative news (%s): %s" % (sym, n["category"], n["title"]))
    return out


def combine(alerts):
    """One notification per stock per pass: the strongest type first, the rest joined."""
    order = ["risk", "new_match", "breakout", "stronger_momentum", "approaching_breakout", "unusual_volume"]
    by = {}
    for x in alerts:
        by.setdefault(x["ticker"], []).append(x)
    out = []
    for sym, xs in by.items():
        xs.sort(key=lambda x: order.index(x["type"]))
        out.append(dict(xs[0], types=[x["type"] for x in xs], text=" | ".join(x["text"] for x in xs)))
    return out
