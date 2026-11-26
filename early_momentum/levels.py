"""Reference levels for a flagged stock. FOR INFORMATION ONLY: these are numbers for
the user's own decision, never orders. Nothing in this package places a trade.
"""


def _r(x):
    return None if x is None else round(x, 4 if x < 1 else 2)


def plan(snap, score_doc, dilution, catalyst):
    price = snap["price"]
    res = snap["resistance"]
    pmh = snap["premarket_high"]
    vw = snap["vwap"]
    s = snap["signals"]
    if snap["session"] == "premarket" and pmh and pmh > price:
        key, key_name = pmh, "premarket high"
    elif snap["session"] == "regular" and pmh and price <= pmh * 1.03 and not s.get("premarket_high_break"):
        key, key_name = pmh, "premarket high"
    elif res:
        key, key_name = res[0]["price"], res[0]["name"]
    else:
        key, key_name = snap["day_high"], "day high"
    extended = price > key * 1.03
    zone = (key, key * 1.02)
    lows = [b["l"] for b in snap["last_bars"]]
    candidates = [x for x in (vw, min(lows) if lows else None) if x is not None and x < zone[0]]
    for sp in snap["support"]:
        if sp["price"] < zone[0]:
            candidates.append(sp["price"])
    stop = max(candidates) if candidates else zone[0] * 0.95
    if (zone[0] - stop) / zone[0] < 0.01:
        stop = zone[0] * 0.985
    entry_mid = sum(zone) / 2
    risk = entry_mid - stop
    above = [r["price"] for r in res if r["price"] > zone[1]]
    t1 = above[0] if above and risk > 0 and entry_mid + risk <= above[0] <= entry_mid + 3 * risk else entry_mid + 1.5 * risk
    t2 = max(entry_mid + 3 * risk, above[1] if len(above) > 1 else 0)
    rr1 = (t1 - entry_mid) / risk if risk > 0 else None
    rr2 = (t2 - entry_mid) / risk if risk > 0 else None

    avoid = []
    if dilution.get("level") == "high":
        avoid.append("high dilution risk: " + "; ".join(f["what"] for f in dilution["flags"] if f["severity"] == "high")[:240])
    if dilution.get("level") == "unknown":
        avoid.append("dilution could not be fully checked")
    if extended:
        avoid.append("price is already %.1f%% above the %s: chasing; wait for a pullback toward %s" % ((price / key - 1) * 100, key_name, _r(vw) or _r(key)))
    move = snap["premarket_change_pct"] if snap["session"] == "premarket" else snap["change_pct"]
    if move is not None and move >= 30:
        avoid.append("already up %.0f%% today: late in the move" % move)
    if snap["rvol"] is not None and snap["rvol"] < 1.5 and snap["session"] == "regular":
        avoid.append("relative volume only %.1fx: weak participation" % snap["rvol"])
    if snap["dollar_volume_today"] < 250000 and snap["session"] == "regular":
        avoid.append("thin trading (under $250k today): fills and exits can be poor")
    if (catalyst or {}).get("negatives"):
        avoid.append("negative headlines: " + "; ".join(n["category"] for n in catalyst["negatives"]))
    if not (catalyst or {}).get("best"):
        avoid.append("no confirmed catalyst: moves without news fade more often")
    if (zone[0] - stop) / zone[0] > 0.10:
        avoid.append("stop reference is more than 10% below the entry zone: wide risk")

    return {
        "for_information_only": True,
        "key_level": {"name": key_name, "price": _r(key)},
        "entry_zone": [_r(zone[0]), _r(zone[1])],
        "breakout_confirmation": "a 5-minute bar CLOSES above %s (%s) on volume at least 1.5x the prior 30-minute average" % (_r(key), key_name),
        "stop_reference": _r(stop),
        "target_1": _r(t1), "target_2": _r(t2),
        "reward_risk_1": None if rr1 is None else round(rr1, 2),
        "reward_risk_2": None if rr2 is None else round(rr2, 2),
        "invalidation": "a 5-minute close below %s, or a break above %s that falls back under it within two bars%s" % (
            _r(stop), _r(key), ", or losing VWAP %s" % _r(vw) if vw else ""),
        "reasons_to_avoid": avoid,
        "extended": extended,
    }
