"""Early momentum signals for one stock at one moment. Each signal is True, False,
or None (no data). None is never counted for or against a stock.
"""

from . import bars as B


def analyze(series, now_et, rvol_30m_ago=None):
    """series (v8 chart, 5d of 5-minute bars with premarket) -> technical snapshot."""
    day = now_et.date()
    raw = B.to_bars(series, now_et)
    allb, blocked = B.dampen_blocks(raw)
    pre, reg = B.split_day(allb, day)
    today = pre + reg
    pc, pc_day = B.prev_close(allb, day)
    # Prefer Yahoo's official previous close (the closing auction) when the data's latest
    # session is the day being analyzed; the last 5-minute bar can differ from it.
    meta_pc = (series.get("meta") or {}).get("previousClose")
    latest_day = raw[-1]["et"].date() if raw else None
    if meta_pc and latest_day == day:
        pc = float(meta_pc)
    blocks_today = [t.strftime("%H:%M") for t in blocked if t.date() == day]
    if not today or pc is None:
        return None
    last = today[-1]
    price = last["c"]
    pm_high = max(b["h"] for b in pre) if pre else None
    pm_close = pre[-1]["c"] if pre else None
    in_regular = bool(reg)
    vw = B.vwap(reg) if reg else None
    rvol = B.relative_volume(allb, day, now_et)
    accel = B.volume_acceleration(reg) if reg else None
    ud = B.up_down_volume(reg) if reg else None
    hhhl = B.higher_highs_lows(today if not reg or len(reg) < 12 else reg)
    res = B.resistance_levels(allb, day, price, pm_high)
    sup = B.support_levels(allb, day, price)
    day_vol = sum(b["v"] for b in reg)
    day_vol_raw = sum(b.get("v_raw", b["v"]) for b in reg)
    change_pct = (price / pc - 1) * 100
    pm_change_pct = ((pm_close / pc - 1) * 100) if pm_close else None
    structural = [r for r in res if r["name"] != "round number"]
    nearest = structural[0] if structural else None
    dist_res = ((nearest["price"] / price - 1) * 100) if nearest else None

    sig = {}
    # Yahoo reports no premarket volume: the premarket volume signal is always "no data" here.
    sig["unusual_premarket_volume"] = None
    sig["rvol_rising"] = (None if rvol is None or rvol_30m_ago is None
                          else rvol >= rvol_30m_ago * 1.25 and rvol >= 2)
    sig["buying_pressure"] = (None if not ud or (ud["up_volume"] + ud["down_volume"]) == 0
                              else ud["up_volume"] >= 1.5 * max(ud["down_volume"], 1))
    sig["near_resistance"] = None if dist_res is None else 0 <= dist_res <= 3.0
    sig["premarket_high_break"] = (None if not (in_regular and pm_high)
                                   else any(b["c"] > pm_high for b in reg[-3:]))
    if vw is None:
        sig["above_vwap"] = None
    else:
        recent = reg[-6:]
        sig["above_vwap"] = price > vw and sum(1 for b in recent if b["c"] >= vw) >= max(1, int(len(recent) * 0.7))
    sig["higher_highs_lows"] = hhhl
    sig["volume_on_up_moves"] = (None if not ud or ud["up_bars"] < 2 or ud["down_bars"] < 1
                                 else ud["avg_up"] >= 1.3 * ud["avg_down"])
    sig["unusual_volume"] = None if rvol is None else rvol >= 3

    return {
        "price": round(price, 4), "as_of": last["et"].strftime("%Y-%m-%d %H:%M"),
        "prev_close": round(pc, 4), "prev_close_date": pc_day.isoformat(),
        "change_pct": round(change_pct, 2),
        "premarket_change_pct": None if pm_change_pct is None else round(pm_change_pct, 2),
        "premarket_high": None if pm_high is None else round(pm_high, 4),
        "vwap": None if vw is None else round(vw, 4),
        "rvol": None if rvol is None else round(rvol, 2),
        "volume_accel": None if accel is None else round(accel, 2),
        "volume_today": day_vol_raw, "volume_today_dampened": round(day_vol),
        "block_prints": blocks_today,
        "dollar_volume_today": round(day_vol * price),
        "day_high": round(max(b["h"] for b in today), 4),
        "day_low": round(min(b["l"] for b in today), 4),
        "resistance": res, "support": sup,
        "dist_to_resistance_pct": None if dist_res is None else round(dist_res, 2),
        "session": "regular" if in_regular else "premarket",
        "signals": sig,
        "last_bars": [{"t": b["et"].strftime("%H:%M"), "c": b["c"], "h": b["h"], "l": b["l"], "v": b["v"]}
                      for b in today[-6:]],
        "_reg_bars": reg, "_today": today,
    }
