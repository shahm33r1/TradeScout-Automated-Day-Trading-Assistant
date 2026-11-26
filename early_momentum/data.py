"""Data sources for the Early Momentum Scanner. Read-only, all through net.get.

Every value returned carries where it came from. Known limits, stated plainly:
- Yahoo Finance (prices, bars, screeners): free and unofficial; it can lag, and it
  reports NO premarket volume (premarket bars arrive with volume 0).
- Float: no free source is used, so float is unknown. Shares outstanding (SEC or
  Yahoo) is an upper bound on float, never presented as the float.
- SEC EDGAR needs a User-Agent with a contact (SEC_USER_AGENT). Without it, SEC
  checks fall back to Finnhub's filing list when Finnhub is available.
- Finnhub (optional): profile (shares outstanding, market cap), company news, SEC
  filing list, insider transactions, earnings calendar. The key is never stored in
  code: it comes from the environment's network secret for finnhub.io (header
  X-Finnhub-Token, injected by the proxy) or, for a local test, FINNHUB_API_KEY.
  Free plans have no intraday candles and no float.
"""

import json
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

from . import net
from . import skills

YAHOO = "https://query2.finance.yahoo.com"


def sec_user_agent():
    ua = (os.environ.get("SEC_USER_AGENT") or "").strip()
    return ua if ("@" in ua and "example.com" not in ua) else None


# ---------------------------------------------------------------- universe

def universe():
    """[(SYM, name)] from Nasdaq Trader's symbol directories, filtered by the scanner skill's rules."""
    texts = {f: net.get(skills.NASDAQ_URL + f, timeout=60) for f in skills.NASDAQ_FILES}
    kept, _counts, created = skills.build_universe(texts)
    return kept, created


# ---------------------------------------------------------------- Yahoo

def spark(symbols, rng="1d", interval="5m", prepost=True):
    """{SYM: series} closes only (no volume), 20 symbols a request."""
    out = {}
    for i in range(0, len(symbols), 20):
        chunk = symbols[i:i + 20]
        path = "/v7/finance/spark?symbols=%s&range=%s&interval=%s%s" % (
            ",".join(skills.to_yahoo(s) for s in chunk), rng, interval,
            "&includePrePost=true" if prepost else "")
        try:
            out.update(skills.parse_spark(net.get(YAHOO + path)))
        except Exception:
            continue
    return out


def chart(sym, rng="5d", interval="5m", prepost=True):
    """One v8 chart: OHLCV series with meta. Premarket volume is reported as 0 by Yahoo."""
    path = "/v8/finance/chart/%s?range=%s&interval=%s%s" % (
        skills.to_yahoo(sym), rng, interval, "&includePrePost=true" if prepost else "")
    return skills.parse_chart(net.get(YAHOO + path), sym)


def screener(scr_id, count=100):
    """Yahoo predefined screener quotes (e.g. small_cap_gainers). A cross-check source only."""
    path = "/v1/finance/screener/predefined/saved?scrIds=%s&count=%d" % (scr_id, count)
    try:
        doc = json.loads(net.get(YAHOO + path))
        return doc["finance"]["result"][0]["quotes"]
    except Exception:
        return []


def yahoo_headlines(sym):
    url = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=US&lang=en-US" % urllib.parse.quote(sym)
    return _rss(net.get(url), "Yahoo Finance")


def google_news(sym, name=None):
    q = '"%s"' % sym if not name else '%s OR "%s"' % (sym, _short_name(name))
    url = "https://news.google.com/rss/search?q=%s+when:2d&hl=en-US&gl=US&ceid=US:en" % urllib.parse.quote(q)
    return _rss(net.get(url), "Google News")


def _short_name(name):
    n = re.sub(r"\b(Inc|Corp|Corporation|Ltd|Limited|Holdings|Plc|Co|Common Stock|Class [A-Z]|Ordinary Shares)\b\.?,?",
               "", name or "", flags=re.I)
    return " ".join(n.split())[:60]


def _rss(text, source):
    items = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return items
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = (it.findtext("pubDate") or "").strip()
        src = it.find("source")
        items.append({"title": title, "link": link, "published": pub,
                      "outlet": (src.text.strip() if src is not None and src.text else source),
                      "via": source})
    return items


def stocktwits(sym):
    """Latest 30 StockTwits posts for a cashtag: [{created_at, body, sentiment}]."""
    try:
        doc = json.loads(net.get("https://api.stocktwits.com/api/2/streams/symbol/%s.json" % urllib.parse.quote(sym)))
    except Exception:
        return None
    out = []
    for m in doc.get("messages") or []:
        s = ((m.get("entities") or {}).get("sentiment") or {}) or {}
        out.append({"created_at": m.get("created_at"), "body": (m.get("body") or "")[:280],
                    "sentiment": s.get("basic")})
    return out


def halts():
    """Current Nasdaq Trader halt list: {SYM: [{reason, time, resumed}]}."""
    try:
        text = net.get("https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts")
        root = ET.fromstring(text)
    except Exception:
        return None
    out = {}
    for it in root.iter("item"):
        vals = {}
        for child in it:
            tag = child.tag.split("}")[-1]
            vals[tag] = (child.text or "").strip()
        sym = vals.get("IssueSymbol") or vals.get("title") or ""
        if sym:
            out.setdefault(sym.upper(), []).append({
                "reason": vals.get("ReasonCode"), "halt_date": vals.get("HaltDate"),
                "halt_time": vals.get("HaltTime"), "resumed": vals.get("ResumptionTradeTime") or None})
    return out


# ---------------------------------------------------------------- SEC EDGAR

def _sec_get(url):
    ua = sec_user_agent()
    if not ua:
        return None
    return net.get(url, headers={"User-Agent": ua, "Accept-Encoding": "identity"})


_TICKERS = {}


def sec_cik(sym):
    if not _TICKERS:
        text = _sec_get("https://www.sec.gov/files/company_tickers.json")
        if not text:
            return None
        for v in json.loads(text).values():
            _TICKERS[str(v["ticker"]).upper()] = int(v["cik_str"])
    return _TICKERS.get(sym.upper().replace(".", "-")) or _TICKERS.get(sym.upper())


def sec_filings(cik, days=180, today=None):
    """Recent filings: [{form, date, accession, items, doc}] newest first."""
    text = _sec_get("https://data.sec.gov/submissions/CIK%010d.json" % cik)
    if not text:
        return None
    rec = (json.loads(text).get("filings") or {}).get("recent") or {}
    n = len(rec.get("form") or [])
    out = []
    for i in range(n):
        d = rec["filingDate"][i]
        if today and _days_between(d, today) > days:
            continue
        out.append({"form": rec["form"][i], "date": d, "accession": rec["accessionNumber"][i],
                    "items": (rec.get("items") or [""] * n)[i] or "",
                    "doc": (rec.get("primaryDocument") or [""] * n)[i] or ""})
    return out


def sec_shares_outstanding(cik):
    """Latest dei:EntityCommonStockSharesOutstanding (shares, as-of date) or None."""
    text = _sec_get("https://data.sec.gov/api/xbrl/companyconcept/CIK%010d/dei/EntityCommonStockSharesOutstanding.json" % cik)
    if not text or not text.strip().startswith("{"):
        return None
    try:
        units = json.loads(text)["units"]["shares"]
    except (KeyError, ValueError):
        return None
    best = max(units, key=lambda u: (u.get("end") or "", u.get("filed") or ""))
    return {"shares": int(best["val"]), "as_of": best.get("end"), "filed": best.get("filed")}


def sec_form4_direction(cik, accession, doc):
    """Parse one Form 4: ('sell'|'buy'|'other', shares). Best effort; None if unreadable."""
    raw = doc.split("/")[-1] if doc else ""
    if not raw.endswith(".xml"):
        return None
    url = "https://www.sec.gov/Archives/edgar/data/%d/%s/%s" % (cik, accession.replace("-", ""), raw)
    text = _sec_get(url)
    if not text:
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    sold = bought = 0.0
    for tr in root.iter("nonDerivativeTransaction"):
        code = (tr.findtext("transactionCoding/transactionCode") or "").strip()
        try:
            sh = float(tr.findtext("transactionAmounts/transactionShares/value") or 0)
        except ValueError:
            sh = 0.0
        if code == "S":
            sold += sh
        elif code == "P":
            bought += sh
    if sold > bought:
        return ("sell", sold)
    if bought > 0:
        return ("buy", bought)
    return ("other", 0.0)


def _days_between(a, b):
    ta = time.strptime(a[:10], "%Y-%m-%d")
    tb = time.strptime(str(b)[:10], "%Y-%m-%d")
    return abs(int((time.mktime(tb) - time.mktime(ta)) // 86400))


# ---------------------------------------------------------------- Finnhub (optional)

FINNHUB = "https://finnhub.io/api/v1"
_FH = {"ok": None, "why": None}


def finnhub(path):
    """GET a Finnhub path -> parsed JSON, or None when Finnhub is unavailable.
    The first refusal for a missing or invalid key turns Finnhub off for the run."""
    if _FH["ok"] is False:
        return None
    key = (os.environ.get("FINNHUB_API_KEY") or "").strip() or "proxy-injected"
    try:
        text = net.get(FINNHUB + path, headers={"X-Finnhub-Token": key})
    except Exception as e:
        _FH["why"] = str(e)
        if _FH["ok"] is None:
            _FH["ok"] = False
        return None
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if isinstance(doc, dict) and doc.get("error"):
        err = str(doc["error"])
        if "API key" in err or "token" in err.lower():
            _FH["ok"], _FH["why"] = False, err
        return None
    _FH["ok"] = True
    return doc


def finnhub_status():
    return {"available": _FH["ok"], "why": _FH["why"]}


def fh_profile(sym):
    d = finnhub("/stock/profile2?symbol=%s" % urllib.parse.quote(sym))
    if not d or not d.get("ticker"):
        return None
    so = d.get("shareOutstanding")        # millions of shares
    mc = d.get("marketCapitalization")    # millions of dollars
    return {"name": d.get("name"), "shares_outstanding": int(so * 1e6) if so else None,
            "market_cap": float(mc) * 1e6 if mc else None, "industry": d.get("finnhubIndustry"),
            "exchange": d.get("exchange")}


def fh_news(sym, today):
    start = (time.strftime("%Y-%m-%d", time.gmtime(time.mktime(time.strptime(today, "%Y-%m-%d")) - 3 * 86400)))
    d = finnhub("/company-news?symbol=%s&from=%s&to=%s" % (urllib.parse.quote(sym), start, today))
    out = []
    for n in d or []:
        ts = n.get("datetime")
        if not ts or not n.get("headline"):
            continue
        out.append({"title": n["headline"], "link": n.get("url"), "outlet": n.get("source") or "Finnhub",
                    "published": time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(int(ts))), "via": "Finnhub"})
    return out


def fh_filings(sym, today, days=180):
    """[{form, date, accession, items: ''}] newest first (Finnhub lists forms, not 8-K items)."""
    start = time.strftime("%Y-%m-%d", time.gmtime(time.mktime(time.strptime(today, "%Y-%m-%d")) - days * 86400))
    d = finnhub("/stock/filings?symbol=%s&from=%s&to=%s" % (urllib.parse.quote(sym), start, today))
    if d is None:
        return None
    return [{"form": f.get("form", ""), "date": (f.get("filedDate") or "")[:10], "accession": f.get("accessNumber", ""),
             "items": "", "doc": "", "link": f.get("filingUrl") or f.get("reportUrl")} for f in d]


def fh_insider_sells(sym, today, days=30):
    """(number of open-market sales, shares sold) by insiders in the last `days`."""
    d = finnhub("/stock/insider-transactions?symbol=%s" % urllib.parse.quote(sym))
    if not d:
        return None
    n, sh = 0, 0
    for t in d.get("data") or []:
        dt = t.get("transactionDate") or t.get("filingDate") or ""
        if t.get("transactionCode") == "S" and dt and _days_between(dt, today) <= days:
            n += 1
            sh += abs(t.get("change") or 0)
    return n, sh


def fh_earnings_soon(sym, today, days=7):
    end = time.strftime("%Y-%m-%d", time.gmtime(time.mktime(time.strptime(today, "%Y-%m-%d")) + days * 86400))
    d = finnhub("/calendar/earnings?from=%s&to=%s&symbol=%s" % (today, end, urllib.parse.quote(sym)))
    if not d:
        return None
    cal = d.get("earningsCalendar") or []
    return cal[0].get("date") if cal else None
