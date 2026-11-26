"""Early Momentum Score, 0-100, with a written reason for every point.

Design rules:
- Each part has a hard cap, so no single factor can carry a stock.
- A low float plus a big percentage gain together are worth at most 20 points, and a
  stock with no volume or technical support is capped at 45.
- Missing data scores 0 for that part and says "no data"; it is never guessed.
- Dilution, negative news, halts and wide spreads subtract.
- A stock that has already run (past late_move_pct) is penalised: the goal is early.
"""

WEIGHTS_VERSION = "v1.1-2026-11-26"  # v1.1: relative volume of 10x+ earns 22 (volume cap 35)


def _band(value, band):
    lo, hi = band
    if value is None:
        return False
    return (lo is None or value >= lo) and (hi is None or value <= hi)


def criteria_match(cfg, snap, float_shares, shares_out, market_cap):
    """Each criterion -> strong | borderline | miss | unknown, plus an overall label."""
    c = cfg["criteria"]
    move = snap["premarket_change_pct"] if snap["session"] == "premarket" else snap["change_pct"]
    supply = float_shares if float_shares is not None else shares_out
    vals = {
        "price": snap["price"],
        "float_shares": supply,
        "rvol": snap["rvol"],
        "premarket_pct": move,
        "market_cap": market_cap,
    }
    out = {}
    for k, v in vals.items():
        if v is None:
            out[k] = "unknown"
        elif _band(v, c[k]["strong"]):
            out[k] = "strong"
        elif _band(v, c[k]["borderline"]):
            out[k] = "borderline"
        else:
            out[k] = "miss"
    if float_shares is None and shares_out is not None and out["float_shares"] in ("strong", "borderline"):
        out["float_shares"] += " (shares outstanding, float unknown)"
    known = [s for s in out.values() if s != "unknown"]
    misses = sum(1 for s in known if s.startswith("miss"))
    strongs = sum(1 for s in known if s.startswith("strong"))
    if known and misses == 0 and strongs == len(known) and len(known) >= 3:
        label = "strong match"
    elif misses <= 1 and len(known) >= 2:
        label = "borderline"
    else:
        label = "outside criteria"
    return out, label


def compute(cfg, snap, catalyst, dilution, float_shares=None, shares_out=None, spread_pct=None):
    parts, reasons, notes = {}, [], []
    s = snap["signals"]

    # 1. Volume: up to 35
    rv = snap["rvol"]
    if rv is None:
        v_rv = 0
        notes.append("relative volume: no data (%s)" % ("Yahoo reports no premarket volume" if snap["session"] == "premarket" else "too little history"))
    else:
        v_rv = 22 if rv >= 10 else 18 if rv >= 5 else 14 if rv >= 3 else 9 if rv >= 2 else 5 if rv >= 1.5 else 0
        reasons.append("relative volume %.1fx: +%d" % (rv, v_rv))
    ac = snap["volume_accel"]
    v_ac = 0 if ac is None else (7 if ac >= 2 else 5 if ac >= 1.5 else 3 if ac >= 1.2 else 0)
    if ac is not None:
        reasons.append("volume last 15 min vs prior 30 min %.1fx: +%d" % (ac, v_ac))
    v_up = 5 if s.get("volume_on_up_moves") else 0
    if v_up:
        reasons.append("heavier volume on up bars than down bars: +5")
    parts["volume"] = min(35, v_rv + v_ac + v_up)

    # 2. Technical setup: up to 25
    t = 0
    if s.get("above_vwap"):
        t += 6; reasons.append("holding above VWAP %.4g: +6" % snap["vwap"])
    if s.get("higher_highs_lows"):
        t += 6; reasons.append("higher highs and higher lows: +6")
    if s.get("premarket_high_break"):
        t += 8; reasons.append("broke above the premarket high %.4g: +8" % snap["premarket_high"])
    elif s.get("near_resistance"):
        lvl = next(r for r in snap["resistance"] if r["name"] != "round number")
        t += 5; reasons.append("%.1f%% under the %s %s: +5" % (snap["dist_to_resistance_pct"], lvl["name"], lvl["price"]))
    if s.get("buying_pressure"):
        t += 5; reasons.append("up-bar volume at least 1.5x down-bar volume: +5")
    parts["technical"] = min(25, t)

    # 3. Catalyst: up to 20
    best = (catalyst or {}).get("best")
    if best:
        parts["catalyst"] = min(20, round(best["quality"]))
        reasons.append("catalyst (%s, %s h old): +%d" % (best["category"], best["age_hours"], parts["catalyst"]))
    else:
        parts["catalyst"] = 0
        notes.append("no fresh company-specific catalyst found")

    # 4. Move size: up to 10 (early is good, late is penalised below)
    move = snap["premarket_change_pct"] if snap["session"] == "premarket" else snap["change_pct"]
    strong, border = cfg["criteria"]["premarket_pct"]["strong"], cfg["criteria"]["premarket_pct"]["borderline"]
    if move is None:
        parts["move"] = 0
    elif _band(move, strong):
        parts["move"] = 10
    elif _band(move, border):
        parts["move"] = 6
    elif 0 < move < border[0]:
        parts["move"] = 2
    else:
        parts["move"] = 0
    if move is not None:
        reasons.append("%s move %+.1f%%: +%d" % (snap["session"], move, parts["move"]))

    # 5. Supply (float): up to 10
    if float_shares is not None:
        parts["supply"] = 10 if float_shares <= 10e6 else 8 if float_shares <= 20e6 else 4 if float_shares <= 40e6 else 0
        reasons.append("float %.1fM: +%d" % (float_shares / 1e6, parts["supply"]))
    elif shares_out is not None:
        parts["supply"] = 6 if shares_out <= 20e6 else 3 if shares_out <= 50e6 else 0
        reasons.append("shares outstanding %.1fM (float unknown, this is an upper bound): +%d" % (shares_out / 1e6, parts["supply"]))
    else:
        parts["supply"] = 2
        notes.append("float and shares outstanding unknown: +2 (neutral)")
    # A low float and a big move together can never be worth more than 20.
    if parts["move"] + parts["supply"] > 20:
        parts["supply"] = 20 - parts["move"]

    # 6. Liquidity and spread: up to 5
    dv = snap["dollar_volume_today"]
    liq = 3 if dv >= 1e6 else 1 if dv >= 250e3 else 0
    if spread_pct is not None:
        liq += 2 if spread_pct <= 0.5 else 1 if spread_pct <= 1.0 else (-3 if spread_pct > 2.0 else 0)
    elif snap["session"] == "regular":
        notes.append("bid-ask spread unknown")
    parts["liquidity"] = max(-3, min(5, liq))
    reasons.append("dollar volume today $%s%s: %+d" % ("{:,.0f}".format(dv), "" if spread_pct is None else ", spread %.2f%%" % spread_pct, parts["liquidity"]))

    raw = sum(parts.values())

    # Penalties
    pen = []
    lvl = (dilution or {}).get("level", "unknown")
    if lvl == "high":
        pen.append(("high dilution risk", -25))
    elif lvl == "moderate":
        pen.append(("moderate dilution risk", -10))
    elif lvl == "unknown":
        pen.append(("dilution not fully checked", -5))
    negs = (catalyst or {}).get("negatives") or []
    if negs:
        pen.append(("negative news (%s)" % ", ".join(sorted({n["category"] for n in negs})), -min(20, 10 * len(negs))))
    if move is not None and move >= cfg["extended_move_pct"]:
        pen.append(("already up %.0f%%: extended, not early" % move, -20))
    elif move is not None and move >= cfg["late_move_pct"]:
        pen.append(("already up %.0f%%: late" % move, -10))
    if snap["session"] == "regular" and s.get("above_vwap") is False and s.get("higher_highs_lows") is False:
        pen.append(("below VWAP without an uptrend", -5))
    score = raw + sum(p for _, p in pen)

    caps = []
    if parts["volume"] + parts["technical"] < 10:
        caps.append((45, "little volume or technical support: capped at 45"))
    if (rv is None or rv < 2) and not best:
        caps.append((60, "no strong relative volume and no catalyst: capped at 60"))
    if move is not None and move >= cfg["extended_move_pct"]:
        caps.append((50, "extended move: capped at 50"))
    for cap, why in caps:
        if score > cap:
            score = cap
            notes.append(why)
    score = int(max(0, min(100, round(score))))
    t_cfg = cfg["tiers"]
    tier = ("strong" if score >= t_cfg["strong"] else "watch" if score >= t_cfg["watch"]
            else "borderline" if score >= t_cfg["borderline"] else "weak")
    return {"score": score, "tier": tier, "parts": parts,
            "penalties": [{"why": w, "points": p} for w, p in pen],
            "reasons": reasons, "notes": notes, "weights_version": WEIGHTS_VERSION}
