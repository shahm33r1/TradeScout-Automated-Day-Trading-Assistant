"""Bar math on Yahoo 5-minute series: sessions, VWAP, relative volume, structure,
resistance. Only bars that have CLOSED by `now` are ever used.
"""

import datetime
import statistics

from . import skills

STEP = datetime.timedelta(minutes=5)
PRE_OPEN = datetime.time(4, 0)


def to_bars(series, now_et, step=STEP):
    """series -> [{et, o, h, l, c, v}] for closed bars only (start + 5 min <= now)."""
    out = []
    t, o, h, l, c, v = (series.get(k) or [] for k in ("t", "o", "h", "l", "c", "v"))
    for i, ts in enumerate(t):
        et = skills.ts_to_eastern(ts)
        if et + step > now_et:
            continue
        close = c[i] if i < len(c) else None
        if close is None:
            continue
        out.append({"et": et,
                    "o": (o[i] if o and o[i] is not None else close),
                    "h": (h[i] if h and h[i] is not None else close),
                    "l": (l[i] if l and l[i] is not None else close),
                    "c": close,
                    "v": (v[i] or 0) if v and i < len(v) else 0})
    return out


def dampen_blocks(bars, factor=20.0, flat_pct=0.5, cap=3.0):
    """Yahoo's 5-minute bars include large single prints (likely block trades) that the
    official daily volume excludes. A bar with more than `factor` x the day's median
    bar volume and an open-to-close move under `flat_pct`% is capped at `cap` x median,
    so one print cannot fake 'unusual volume'. Returns (bars, [blocked bar times])."""
    by_day, out, blocked = {}, [], []
    for b in bars:
        by_day.setdefault(b["et"].date(), []).append(b)
    for d in sorted(by_day):
        vols = [b["v"] for b in by_day[d] if b["v"] > 0]
        med = statistics.median(vols) if vols else 0
        for b in by_day[d]:
            if med and b["v"] > factor * med and b["o"] and abs(b["c"] - b["o"]) / b["o"] * 100 < flat_pct:
                out.append(dict(b, v=cap * med, v_raw=b["v"]))
                blocked.append(b["et"])
            else:
                out.append(b)
    return out, blocked


def split_day(bars, day):
    """(premarket, regular) bars for one New York date."""
    s = skills.session(day)
    if not s:
        return [], []
    open_t, close_t = s
    pre = [b for b in bars if b["et"].date() == day and PRE_OPEN <= b["et"].time() < open_t]
    reg = [b for b in bars if b["et"].date() == day and open_t <= b["et"].time() < close_t]
    return pre, reg


def prior_days(bars, day, n=5):
    days = sorted({b["et"].date() for b in bars if b["et"].date() < day and skills.session(b["et"].date())})
    return days[-n:]


def prev_close(bars, day):
    for d in reversed(prior_days(bars, day, 10)):
        _, reg = split_day(bars, d)
        if reg:
            return reg[-1]["c"], d
    return None, None


def vwap(bars):
    pv = sum((b["h"] + b["l"] + b["c"]) / 3.0 * b["v"] for b in bars)
    vol = sum(b["v"] for b in bars)
    return (pv / vol) if vol > 0 else None


def cum_volume_until(bars, day, tod):
    """Regular-session volume on `day` from the open up to time-of-day `tod` (exclusive)."""
    _, reg = split_day(bars, day)
    return sum(b["v"] for b in reg if b["et"].time() < tod)


def relative_volume(bars, day, now_et, min_days=2):
    """Today's regular-session volume so far / average of prior days at the same time of day.
    None before the open, or with fewer than `min_days` usable prior days."""
    s = skills.session(day)
    if not s or now_et.time() < s[0]:
        return None
    tod = now_et.time()
    today = cum_volume_until(bars, day, tod)
    hist = [cum_volume_until(bars, d, tod) for d in prior_days(bars, day)]
    hist = [h for h in hist if h > 0]
    if len(hist) < min_days or today <= 0:
        return None
    return today / (sum(hist) / len(hist))


def volume_acceleration(reg, recent=3, base=6):
    if len(reg) < recent + base:
        return None
    r = [b["v"] for b in reg[-recent:]]
    b = [x["v"] for x in reg[-(recent + base):-recent]]
    avg_b = sum(b) / len(b)
    if avg_b <= 0:
        return None
    return (sum(r) / len(r)) / avg_b


def up_down_volume(reg, n=12):
    seg = reg[-n:]
    up = [b["v"] for b in seg if b["c"] > b["o"]]
    dn = [b["v"] for b in seg if b["c"] < b["o"]]
    return {"up_volume": sum(up), "down_volume": sum(dn),
            "avg_up": (sum(up) / len(up)) if up else 0.0,
            "avg_down": (sum(dn) / len(dn)) if dn else 0.0,
            "up_bars": len(up), "down_bars": len(dn)}


def higher_highs_lows(bars, n=12):
    """Split the last n bars into 3 chunks; True when both chunk highs and chunk lows rise."""
    seg = bars[-n:]
    if len(seg) < n:
        return None
    k = n // 3
    chunks = [seg[i * k:(i + 1) * k] for i in range(3)]
    highs = [max(b["h"] for b in ch) for ch in chunks]
    lows = [min(b["l"] for b in ch) for ch in chunks]
    return highs[0] < highs[1] < highs[2] and lows[0] < lows[1] < lows[2]


def round_levels_above(price, pct=0.12):
    """Whole and half-dollar levels (dimes under $2) between price and +pct."""
    step = 0.10 if price < 2 else (0.50 if price < 10 else 1.0)
    out, lvl = [], (int(price / step) + 1) * step
    while lvl <= price * (1 + pct):
        out.append(round(lvl, 2))
        lvl += step
    return out


def resistance_levels(bars, day, price, pm_high=None):
    """Named levels above the current price, nearest first."""
    lv = []
    pdays = prior_days(bars, day, 5)
    if pdays:
        _, reg = split_day(bars, pdays[-1])
        if reg:
            lv.append(("prior day high", max(b["h"] for b in reg)))
    hi5 = [b["h"] for d in pdays for b in split_day(bars, d)[1]]
    if hi5:
        lv.append(("5-day high", max(hi5)))
    if pm_high:
        lv.append(("premarket high", pm_high))
    for r in round_levels_above(price):
        lv.append(("round number", r))
    seen, structural, rounds = set(), [], []
    for name, p in sorted(lv, key=lambda x: x[1]):
        if p > price and round(p, 2) not in seen:
            seen.add(round(p, 2))
            (rounds if name == "round number" else structural).append({"name": name, "price": round(p, 4)})
    return sorted(structural + rounds[:3], key=lambda r: r["price"])


def support_levels(bars, day, price):
    out = []
    pdays = prior_days(bars, day, 5)
    if pdays:
        _, reg = split_day(bars, pdays[-1])
        if reg:
            out.append(("prior day low", min(b["l"] for b in reg)))
            out.append(("prior close", reg[-1]["c"]))
    return [{"name": n, "price": round(p, 4)} for n, p in sorted(out, key=lambda x: -x[1]) if p < price]
