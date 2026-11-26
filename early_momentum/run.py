"""Early Momentum Scanner command line. RESEARCH ONLY - NO AUTOMATED EXECUTION.

  python3 -m early_momentum.run prepare            # once a day: build the small-cap candidate list
  python3 -m early_momentum.run scan               # one pass: write trader/momentum/latest.json
  python3 -m early_momentum.run monitor --until 10:30   # repeat passes; exit 0 when there are new alerts
  python3 -m early_momentum.run report             # print the latest results

Flags: --now "2026-11-25 09:20" (New York) pins the clock, --offline DIR replays saved
responses, --record DIR saves every response for a later replay.

Exit codes: scan 0 = ok; monitor 0 = new alerts written, 1 = reached --until or the close.
"""

import argparse
import datetime
import json
import os
import sys
import time

from . import alerts as A
from . import catalyst as C
from . import data as D
from . import dilution as DIL
from . import levels as L
from . import net
from . import score as S
from . import signals as SIG
from . import skills
from . import tracker as T

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join("trader", "momentum")
BANNER = "RESEARCH ONLY - NO AUTOMATED EXECUTION"


def load_cfg(path=None):
    with open(path or os.path.join(HERE, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _p(*parts):
    return os.path.join(BASE, *parts)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, default=str)
    os.replace(tmp, path)


def now_clock(text=None):
    utc = skills.parse_now(text)
    return utc, skills.to_eastern(utc)


def window(now_et):
    t = now_et.time()
    if t < datetime.time(9, 30):
        return "premarket"
    if t < datetime.time(10, 30):
        return "opening"
    return "intraday"


# ---------------------------------------------------------------- prepare

def prepare(cfg, now_utc, now_et, log=print):
    day = now_et.date().isoformat()
    out = _p("candidates-%s.json" % day)
    kept, created = D.universe()
    log("Universe: %d US-listed common stocks and ADRs (Nasdaq Trader files of %s ET)." % (len(kept), created))
    names = dict(kept)
    syms = [s for s, _ in kept]
    sp = D.spark(syms, rng="5d", interval="1d", prepost=False)
    u = cfg["universe"]
    cands = []
    for s in syms:
        ser = sp.get(s)
        if not ser:
            continue
        m = ser["meta"]
        price = m.get("regularMarketPrice")
        vol = m.get("regularMarketVolume")
        if price is None or vol is None:
            continue
        if u["min_price"] <= price <= u["max_price"] and vol >= u["min_avg_volume"]:
            cands.append({"ticker": s, "name": names.get(s), "last_price": price, "last_volume": vol})
    doc = {"date": day, "created": now_et.strftime("%Y-%m-%d %H:%M"), "fetched": len(sp), "universe": len(syms),
           "filter": u, "note": "last_volume is the latest session's volume from Yahoo spark meta",
           "candidates": cands}
    write_json(out, doc)
    log("Candidates: %d priced $%.2f-$%.2f with %s+ shares last session (fetched %d of %d charts)." % (
        len(cands), u["min_price"], u["max_price"], "{:,}".format(u["min_avg_volume"]), len(sp), len(syms)))
    return doc


# ---------------------------------------------------------------- one pass

def stage1(cfg, cands, now_et):
    """Spark closes (with premarket) for every candidate -> shortlist by early-move pre-score."""
    syms = [c["ticker"] for c in cands]
    sp = D.spark(syms, rng="1d", interval="5m", prepost=True)
    rows = []
    for c in cands:
        ser = sp.get(c["ticker"])
        if not ser:
            continue
        pc = ser["meta"].get("chartPreviousClose") or ser["meta"].get("previousClose")
        pts = [(skills.ts_to_eastern(t), v) for t, v in zip(ser["t"], ser["c"]) if v is not None]
        pts = [(e, v) for e, v in pts if e + datetime.timedelta(minutes=5) <= now_et and e.date() == now_et.date()]
        if not pc or not pts:
            continue
        last = pts[-1][1]
        chg = (last / pc - 1) * 100
        back = [v for e, v in pts if e <= now_et - datetime.timedelta(minutes=30)]
        slope = ((last / back[-1] - 1) * 100) if back else 0.0
        hi = max(v for _, v in pts)
        near_hi = (last / hi) if hi else 0
        s1 = cfg["stage1"]
        if not (s1["min_change_pct"] <= chg <= s1["max_change_pct"]):
            continue
        # early-move pre-score: moving up, still near the high, not yet extended
        pre = min(chg, 25) + 2 * max(min(slope, 10), -5) + 10 * near_hi - max(0, chg - cfg["late_move_pct"]) * 0.5
        rows.append({"ticker": c["ticker"], "name": c.get("name"), "change_pct": round(chg, 2),
                     "slope30_pct": round(slope, 2), "pre_score": round(pre, 2)})
    rows.sort(key=lambda r: -r["pre_score"])
    return rows, len(sp)


def screener_index(cfg):
    idx = {}
    for scr in ("small_cap_gainers", "aggressive_small_caps", "day_gainers"):
        for q in D.screener(scr, 100):
            p = q.get("regularMarketPrice")
            if p is None or not (cfg["universe"]["min_price"] <= p <= cfg["universe"]["max_price"]):
                continue
            idx.setdefault(q["symbol"].replace("-", "."), dict(q, _screener=scr))
    return idx


def research(cfg, sym, name, now_utc, now_et, st, scr, halts):
    """Catalyst and dilution checks, cached in `st` (news 30 min, dilution once a day)."""
    cache = st.setdefault("research", {}).setdefault(sym, {})
    t = now_et.strftime("%H:%M")
    fresh = cache.get("news_at")
    if not fresh or (now_et - datetime.datetime.strptime(fresh, "%Y-%m-%d %H:%M")).total_seconds() > cfg["stage2"]["news_refresh_minutes"] * 60:
        items = []
        for fn in (lambda: D.yahoo_headlines(sym), lambda: D.google_news(sym, name)):
            try:
                items += fn()
            except Exception:
                pass
        cache["news"] = C.classify(items, now_utc, sym)
        tw = D.stocktwits(sym)
        if tw is not None:
            recent = [m for m in tw if m.get("created_at") and
                      0 <= (now_utc - datetime.datetime.strptime(m["created_at"], "%Y-%m-%dT%H:%M:%SZ")).total_seconds() <= 3600]
            cache["social"] = {"posts_last_hour": len(recent),
                               "bullish": sum(1 for m in recent if m["sentiment"] == "Bullish"),
                               "bearish": sum(1 for m in recent if m["sentiment"] == "Bearish")}
        cache["news_at"] = now_et.strftime("%Y-%m-%d %H:%M")
    if "dilution" not in cache:
        q = scr.get(sym) or {}
        # SEC and halt checks once a day; news is added below on every refresh
        base = DIL.check(sym, now_et.date().isoformat(), news_negatives=None,
                         halts=halts, earnings_ts=q.get("earningsTimestamp"), now_utc=now_utc)
        cache["dilution_base"] = base
    base = cache["dilution_base"]
    news_flags = [{"severity": "high" if n["category"] in ("offering / dilution", "going concern") else "moderate",
                   "what": "news: %s - %s" % (n["category"], n["title"]), "link": n.get("link")}
                  for n in (cache["news"] or {}).get("negatives") or []]
    flags = base["flags"] + news_flags
    if any(f["severity"] == "high" for f in flags):
        level = "high"
    elif flags:
        level = "moderate"
    else:
        level = base["level"]            # low (SEC checked, clean) or unknown (not checked)
    cache["dilution"] = dict(base, flags=flags, level=level)
    return cache


def scan_pass(cfg, now_utc, now_et, log=print, replay=False):
    """One pass. replay=True (a pinned --now) uses the Yahoo screeners only for static
    fields (market cap, shares, bid/ask), never to pick stocks, since their lists
    reflect the present, not the replayed moment."""
    day = now_et.date().isoformat()
    cand_doc = read_json(_p("candidates-%s.json" % day), None)
    if not cand_doc:
        cand_doc = prepare(cfg, now_utc, now_et, log)
    st = read_json(_p("state-%s.json" % day), {"rvol_hist": {}, "alerts": {}, "research": {}})
    det = read_json(_p("detections-%s.json" % day), {})
    calls0 = net.calls()

    short, fetched = stage1(cfg, cand_doc["candidates"], now_et)
    scr = screener_index(cfg)
    pick = [r["ticker"] for r in short[:cfg["stage1"]["shortlist"]]]
    for sym in ([] if replay else scr):
        if sym not in pick and len(pick) < cfg["stage1"]["shortlist"] + 10 and any(c["ticker"] == sym for c in cand_doc["candidates"]):
            pick.append(sym)
    pick += [s for s in det if s not in pick]          # keep tracking everything already flagged
    # stocks that showed unusual volume earlier today stay on the list (stage 1 sees prices only)
    pick += [s for s, h in st["rvol_hist"].items() if s not in pick and max(x[1] for x in h) >= cfg["sticky_rvol"]]
    names = {c["ticker"]: c.get("name") for c in cand_doc["candidates"]}

    halts = D.halts()
    snaps = {}
    for sym in pick:
        try:
            ser = D.chart(sym, "5d", "5m", True)
        except Exception as e:
            log("  %s: chart unavailable (%s)" % (sym, e))
            continue
        hist = st["rvol_hist"].get(sym, [])
        back = [h for h in hist if h[0] <= (now_et - datetime.timedelta(minutes=30)).strftime("%H:%M")]
        snap = SIG.analyze(ser, now_et, rvol_30m_ago=back[-1][1] if back else None)
        if not snap:
            continue
        snap["name"] = ser["meta"].get("longName") or ser["meta"].get("shortName") or names.get(sym)
        snaps[sym] = snap
        if snap["rvol"] is not None:
            st["rvol_hist"].setdefault(sym, []).append([now_et.strftime("%H:%M"), snap["rvol"]])
        if sym in det:
            T.update(det[sym], snap["_today"], now_et)

    # preliminary score (no news, no dilution) to choose who gets the slower research calls
    prelim = []
    for sym, snap in snaps.items():
        q = scr.get(sym) or {}
        ps = S.compute(cfg, snap, None, {"level": "low"}, None, q.get("sharesOutstanding"))
        prelim.append((ps["score"], sym))
    prelim.sort(reverse=True)
    researched = {s for _, s in prelim[:cfg["stage2"]["research_top"]]} | {s for s in det if s in snaps}

    records = []
    for sym, snap in snaps.items():
        q = scr.get(sym) or {}
        if sym in researched:
            rc = research(cfg, sym, snap["name"], now_utc, now_et, st, scr, halts)
            cat, dil, social = rc.get("news"), rc.get("dilution"), rc.get("social")
        else:
            cat, dil, social = None, {"level": "unknown", "flags": [], "checked": [], "notes": ["not researched this pass (lower preliminary score)"]}, None
        so = (dil or {}).get("shares_outstanding") or {}
        shares_out = so.get("shares") if so else q.get("sharesOutstanding")
        mcap = q.get("marketCap") or (shares_out * snap["price"] if shares_out else None)
        spread = None
        if not replay and q.get("bid") and q.get("ask") and q["ask"] >= q["bid"] > 0:
            spread = (q["ask"] - q["bid"]) / ((q["ask"] + q["bid"]) / 2) * 100
        sc = S.compute(cfg, snap, cat, dil, None, shares_out, spread)
        crit, match = S.criteria_match(cfg, snap, None, shares_out, mcap)
        plan = L.plan(snap, sc, dil or {}, cat or {})
        move = snap["premarket_change_pct"] if snap["session"] == "premarket" else snap["change_pct"]
        stale_min = (now_et - datetime.datetime.strptime(snap["as_of"], "%Y-%m-%d %H:%M")).total_seconds() / 60 - 5
        data_notes = ["prices and volume: Yahoo Finance, free and unofficial, can lag",
                      "float: unknown (no float source configured)"]
        if snap["session"] == "premarket":
            data_notes.append("premarket volume: not available from Yahoo")
        if stale_min > 15:
            data_notes.append("STALE: last bar is %d minutes old" % stale_min)
        if spread is None:
            data_notes.append("bid-ask spread: unknown")
        if snap.get("block_prints"):
            data_notes.append("block print(s) at %s: large flat-price prints capped when measuring relative volume" % ", ".join(snap["block_prints"]))
        rec = {
            "ticker": sym, "name": snap["name"], "price": snap["price"], "as_of": snap["as_of"],
            "session": snap["session"], "change_pct": snap["change_pct"],
            "premarket_change_pct": snap["premarket_change_pct"], "move_pct": move,
            "market_cap": mcap, "market_cap_source": "Yahoo screener" if q.get("marketCap") else ("price x SEC shares outstanding" if mcap else None),
            "float_shares": None, "shares_outstanding": shares_out,
            "rvol": snap["rvol"], "volume_today": snap["volume_today"], "volume_accel": snap["volume_accel"],
            "vwap": snap["vwap"], "premarket_high": snap["premarket_high"], "prev_close": snap["prev_close"],
            "resistance": snap["resistance"], "support": snap["support"],
            "spread_pct": None if spread is None else round(spread, 2),
            "signals": snap["signals"], "catalyst": cat or {"best": None, "positives": [], "negatives": []},
            "social": social, "dilution": dil, "criteria": crit, "match": match,
            "score": sc["score"], "tier": sc["tier"], "score_parts": sc["parts"], "penalties": sc["penalties"],
            "reasons": sc["reasons"], "notes": sc["notes"], "plan": plan,
            "researched": sym in researched, "data_notes": data_notes, "last_bars": snap["last_bars"],
            "label": BANNER,
        }
        records.append(rec)

    records.sort(key=lambda r: (-r["score"], -(r["rvol"] or 0)))
    new_alerts = []
    for rec in records:
        flagged = rec["score"] >= cfg["alerts"]["min_score"]
        fired = A.evaluate(rec, st["alerts"], cfg)
        new_alerts += fired
        if flagged or fired or st["alerts"].get(rec["ticker"], {}).get("last_score") is not None:
            if T.record_detection(det, rec, now_et):
                T.update(det[rec["ticker"]], snaps[rec["ticker"]]["_today"], now_et)

    doc = {"label": BANNER, "date": day, "as_of": now_et.strftime("%Y-%m-%d %H:%M"), "window": window(now_et),
           "candidates": len(cand_doc["candidates"]), "stage1_fetched": fetched, "shortlisted": len(pick),
           "analyzed": len(snaps), "requests": net.calls() - calls0,
           "sec_checked": bool(D.sec_user_agent()),
           "data_sources": {"prices": "Yahoo Finance (free, unofficial, can lag; no premarket volume)",
                            "float": "not available (no float provider key)",
                            "filings": "SEC EDGAR" if D.sec_user_agent() else "SEC EDGAR NOT checked: SEC_USER_AGENT not set",
                            "news": "Yahoo Finance RSS, Google News RSS", "social": "StockTwits", "halts": "Nasdaq Trader"},
           "results": [r for r in records if r["tier"] != "weak" or r["ticker"] in det
                       or (r["session"] == "premarket" and r["score"] >= cfg["premarket_show_min_score"])],
           "considered": [{"ticker": r["ticker"], "score": r["score"], "move_pct": r["move_pct"], "rvol": r["rvol"],
                           "tier": r["tier"], "parts": r["score_parts"], "penalties": [p["points"] for p in r["penalties"]]}
                          for r in records[:30]],
           "tracking": T.summary(det)}
    write_json(_p("latest.json"), doc)
    write_json(_p("detections-%s.json" % day), det)
    write_json(_p("state-%s.json" % day), st)
    if new_alerts:
        stamp = now_et.strftime("%Y-%m-%d %H:%M")
        pending = read_json(_p("alerts-pending.json"), [])
        new_alerts = A.combine(new_alerts)
        pending += [dict(a, at=stamp) for a in new_alerts]
        write_json(_p("alerts-pending.json"), pending)
    log("%s %s ET [%s]: %d candidates, %d shortlisted, %d analyzed, %d shown, %d new alerts, %d requests. %s" % (
        BANNER, doc["as_of"], doc["window"], doc["candidates"], len(pick), len(snaps), len(doc["results"]),
        len(new_alerts), doc["requests"], "" if doc["sec_checked"] else "SEC filings not checked (SEC_USER_AGENT unset)."))
    return doc, new_alerts


# ---------------------------------------------------------------- export for the dashboard

def _link(u):
    return u if isinstance(u, str) and u.startswith("https://") else None


def compact(r):
    """One dashboard row: only what the page shows, links only if https."""
    cat = r.get("catalyst") or {}
    best = cat.get("best")
    news = [{"category": n["category"], "title": n["title"][:200], "link": _link(n.get("link")),
             "outlet": n.get("outlet"), "age_hours": n.get("age_hours")} for n in (cat.get("positives") or [])[:3]]
    neg = [{"category": n["category"], "title": n["title"][:200], "link": _link(n.get("link"))} for n in (cat.get("negatives") or [])[:3]]
    dil = r.get("dilution") or {}
    return {
        "label": BANNER, "date": r["as_of"][:10], "as_of": r["as_of"], "session": r["session"],
        "ticker": r["ticker"], "name": r.get("name"), "price": r["price"],
        "change_pct": r["change_pct"], "premarket_change_pct": r["premarket_change_pct"],
        "market_cap": r.get("market_cap"), "market_cap_source": r.get("market_cap_source"),
        "float_shares": r.get("float_shares"), "shares_outstanding": r.get("shares_outstanding"),
        "rvol": r.get("rvol"), "volume_today": r.get("volume_today"), "spread_pct": r.get("spread_pct"),
        "vwap": r.get("vwap"), "premarket_high": r.get("premarket_high"), "prev_close": r.get("prev_close"),
        "resistance": (r.get("resistance") or [])[:4], "support": (r.get("support") or [])[:2],
        "catalyst": None if not best else {"category": best["category"], "title": best["title"][:200],
                                           "link": _link(best.get("link")), "outlet": best.get("outlet"),
                                           "age_hours": best.get("age_hours")},
        "news": news, "negative_news": neg, "social": r.get("social"),
        "dilution": {"level": dil.get("level", "unknown"),
                     "flags": [{"severity": f["severity"], "what": f["what"][:220], "link": _link(f.get("link"))} for f in (dil.get("flags") or [])[:6]],
                     "notes": (dil.get("notes") or [])[:3]},
        "score": r["score"], "tier": r["tier"], "match": r["match"], "criteria": r.get("criteria"),
        "score_parts": r.get("score_parts"), "penalties": r.get("penalties"),
        "reasons": r.get("reasons"), "notes": r.get("notes"),
        "plan": r.get("plan"), "signals": r.get("signals"), "data_notes": r.get("data_notes"),
    }


def export(doc, det, out_dir, top=25):
    """Write the two dashboard documents the scheduled session saves with ArtifactData:
    momentum_meta/latest (summary + top results) and momentum_tracking/<date> (detections)."""
    os.makedirs(out_dir, exist_ok=True)
    meta = {"label": BANNER, "date": doc["date"], "as_of": doc["as_of"], "window": doc["window"],
            "candidates": doc["candidates"], "analyzed": doc["analyzed"], "shown": len(doc["results"]),
            "data_sources": doc["data_sources"], "sec_checked": doc["sec_checked"],
            "tracking_today": doc["tracking"], "weights_version": S.WEIGHTS_VERSION,
            "results": [compact(r) for r in doc["results"][:top]]}
    tracking = {"label": BANNER, "date": doc["date"], "updated": doc["as_of"],
                "detections": sorted(det.values(), key=lambda e: e["first_detected"]),
                "summary": T.summary(det)}
    p1, p2 = os.path.join(out_dir, "meta-latest.json"), os.path.join(out_dir, "tracking-%s.json" % doc["date"])
    write_json(p1, meta)
    write_json(p2, tracking)
    writes = [{"op": "set", "collection": "momentum_meta", "doc_id": "latest", "file_path": os.path.abspath(p1)},
              {"op": "set", "collection": "momentum_tracking", "doc_id": doc["date"], "file_path": os.path.abspath(p2)}]
    write_json(os.path.join(out_dir, "writes.json"), writes)
    return writes, os.path.getsize(p1), os.path.getsize(p2)


# ---------------------------------------------------------------- report

def report(doc, n=15, out=print):
    out("%s  |  %s ET  |  %s window" % (doc["label"], doc["as_of"], doc["window"]))
    out("%-6s %5s %-10s %8s %7s %7s %6s %-9s %-8s %s" % ("TICKER", "SCORE", "TIER", "PRICE", "CHG%", "PM%", "RVOL", "DILUTION", "MATCH", "CATALYST"))
    for r in doc["results"][:n]:
        best = (r["catalyst"] or {}).get("best")
        out("%-6s %5d %-10s %8.4g %7s %7s %6s %-9s %-8s %s" % (
            r["ticker"], r["score"], r["tier"], r["price"],
            "%+.1f" % r["change_pct"], "-" if r["premarket_change_pct"] is None else "%+.1f" % r["premarket_change_pct"],
            "-" if r["rvol"] is None else "%.1fx" % r["rvol"], (r["dilution"] or {}).get("level", "?"),
            r["match"].split()[0], (best["category"] if best else "-")))
    t = doc.get("tracking") or {}
    out("Tracked today: %s detections. %s" % (t.get("detections", 0), json.dumps({k: t[k] for k in ("15m", "60m") if t.get(k)})))


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description="Early Momentum Scanner (" + BANNER + ")")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("prepare", "scan", "monitor", "report", "export"):
        p = sub.add_parser(name)
        p.add_argument("--now")
        p.add_argument("--offline")
        p.add_argument("--record")
        p.add_argument("--cache", help="replay saved responses, fetch and save the rest")
        p.add_argument("--config")
        p.add_argument("--json", action="store_true")
        if name == "monitor":
            p.add_argument("--until", required=True, help="New York time HH:MM to stop")
        if name == "report":
            p.add_argument("--top", type=int, default=15)
    a = ap.parse_args(argv)
    net.configure(offline=a.offline, record=a.record, cache=a.cache)
    cfg = load_cfg(a.config)

    if a.cmd == "export":
        doc = read_json(_p("latest.json"), None)
        if not doc:
            print("No scan yet.")
            return 1
        det = read_json(_p("detections-%s.json" % doc["date"]), {})
        writes, b1, b2 = export(doc, det, _p("export"))
        for w in writes:
            print("%s/%s <- %s" % (w["collection"], w["doc_id"], w["file_path"]))
        print("Sizes: %d and %d bytes." % (b1, b2))
        return 0

    if a.cmd == "report":
        doc = read_json(_p("latest.json"), None)
        if not doc:
            print("No scan yet. Run: python3 -m early_momentum.run scan")
            return 1
        print(json.dumps(doc, indent=1, default=str)) if a.json else report(doc, a.top)
        return 0

    now_utc, now_et = now_clock(a.now)
    if not skills.session(now_et.date()):
        print("%s: the market is closed today (%s). Nothing to scan." % (now_et.date(), skills.trading_day(now_et.date())[1]))
        return 1
    if a.cmd == "prepare":
        prepare(cfg, now_utc, now_et)
        return 0
    if a.cmd == "scan":
        doc, _ = scan_pass(cfg, now_utc, now_et, replay=bool(a.now))
        if not a.json:
            report(doc)
        return 0

    # monitor
    hh, mm = (int(x) for x in a.until.split(":"))
    until = datetime.datetime.combine(now_et.date(), datetime.time(hh, mm))
    close = datetime.datetime.combine(now_et.date(), skills.session(now_et.date())[1])
    until = min(until, close)
    simulated = bool(a.now)
    while now_et < until:
        _doc, new = scan_pass(cfg, now_utc, now_et, replay=simulated)
        if new:
            return 0
        step = cfg["scan_interval_minutes"][window(now_et)]
        if simulated:
            now_utc += datetime.timedelta(minutes=step)
            now_et += datetime.timedelta(minutes=step)
        else:
            time.sleep(step * 60)
            now_utc, now_et = now_clock()
    print("Reached %s ET without new alerts." % until.strftime("%H:%M"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
