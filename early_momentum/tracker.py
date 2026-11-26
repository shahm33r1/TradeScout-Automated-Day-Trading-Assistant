"""Track every stock the scanner flags: price at detection, then +5, +15, +30 and
+60 minutes, the best gain and the worst drawdown after detection. This is what
shows whether the scanner is actually early.
"""

import datetime

CHECKPOINTS = (("5m", 5), ("15m", 15), ("30m", 30), ("60m", 60))


def record_detection(det, rec, now_et):
    sym = rec["ticker"]
    if sym in det:
        return False
    det[sym] = {
        "ticker": sym, "name": rec.get("name"),
        "first_detected": now_et.strftime("%Y-%m-%d %H:%M"),
        "session": rec["session"],
        "price_at_detection": rec["price"],
        "score_at_detection": rec["score"], "tier_at_detection": rec["tier"],
        "parts_at_detection": rec.get("score_parts"),
        "checkpoints": {k: None for k, _ in CHECKPOINTS},
        "max_gain_pct": 0.0, "max_drawdown_pct": 0.0, "last_price": rec["price"],
        "updated": now_et.strftime("%Y-%m-%d %H:%M"),
    }
    return True


def update(entry, today_bars, now_et):
    """today_bars: closed bars for the day [{et, h, l, c}]. Fills what time allows."""
    t0 = datetime.datetime.strptime(entry["first_detected"], "%Y-%m-%d %H:%M")
    p0 = entry["price_at_detection"]
    after = [b for b in today_bars if b["et"] >= t0]
    if not after or not p0:
        return entry
    for key, mins in CHECKPOINTS:
        if entry["checkpoints"].get(key) is None and now_et >= t0 + datetime.timedelta(minutes=mins):
            target = t0 + datetime.timedelta(minutes=mins)
            # the close of the bar that ends at or just after the checkpoint
            hit = [b for b in after if b["et"] + datetime.timedelta(minutes=5) >= target]
            if hit:
                entry["checkpoints"][key] = {"price": hit[0]["c"], "pct": round((hit[0]["c"] / p0 - 1) * 100, 2)}
    entry["max_gain_pct"] = round(max(entry["max_gain_pct"], (max(b["h"] for b in after) / p0 - 1) * 100), 2)
    entry["max_drawdown_pct"] = round(min(entry["max_drawdown_pct"], (min(b["l"] for b in after) / p0 - 1) * 100), 2)
    entry["last_price"] = after[-1]["c"]
    entry["updated"] = now_et.strftime("%Y-%m-%d %H:%M")
    return entry


def summary(detections):
    """Hit rates and averages across all tracked detections."""
    rows = list(detections.values())
    out = {"detections": len(rows)}
    for key, _ in CHECKPOINTS:
        vals = [r["checkpoints"][key]["pct"] for r in rows if r["checkpoints"].get(key)]
        out[key] = None if not vals else {
            "n": len(vals), "avg_pct": round(sum(vals) / len(vals), 2),
            "positive_rate": round(100.0 * sum(1 for v in vals if v > 0) / len(vals), 1)}
    gains = [r["max_gain_pct"] for r in rows]
    if gains:
        out["avg_max_gain_pct"] = round(sum(gains) / len(gains), 2)
        out["hit_10pct_rate"] = round(100.0 * sum(1 for g in gains if g >= 10) / len(gains), 1)
        out["hit_30pct_rate"] = round(100.0 * sum(1 for g in gains if g >= 30) / len(gains), 1)
        out["avg_max_drawdown_pct"] = round(sum(r["max_drawdown_pct"] for r in rows) / len(rows), 2)
    return out
