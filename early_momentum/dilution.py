"""Dilution and risk checks from SEC EDGAR filings, news and the Nasdaq halt list.

A low float is not an opportunity on its own: small companies often raise money
into a spike. This module looks for the paper trail of that.
"""

from . import data

REGISTRATIONS = {"S-1", "S-1/A", "F-1", "F-1/A"}
SHELFS = {"S-3", "S-3/A", "F-3", "F-3/A", "S-3ASR"}


def _days(a, b):
    return data._days_between(a, b)


def check(sym, today, news_negatives=None, halts=None, earnings_ts=None, now_utc=None):
    """-> {level: high|moderate|low|unknown, flags: [...], checked: [...], notes: [...]}"""
    flags, checked, notes = [], [], []
    cik, filings, source = None, None, None
    if data.sec_user_agent():
        try:
            cik = data.sec_cik(sym)
            if cik:
                filings = data.sec_filings(cik, days=365, today=today) or []
                source = "SEC EDGAR"
            else:
                notes.append("no SEC filer found for %s on EDGAR (foreign or non-SEC filer?)" % sym)
        except Exception as e:
            notes.append("SEC EDGAR unavailable: %s" % e)
    if filings is None:
        fh = data.fh_filings(sym, today, days=365)
        if fh is not None:
            filings, source = fh, "Finnhub filing list (no 8-K item numbers)"
    if filings is None:
        notes.append("SEC filings NOT checked: set SEC_USER_AGENT (name and email) or save a Finnhub key for finnhub.io")
    else:
        checked.append("%s, last 365 days" % source)
        for f in filings:
            age = _days(f["date"], today) if f.get("date") else 999
            form, items = f["form"], f.get("items") or ""
            link = f.get("link")
            if form in REGISTRATIONS and age <= 120:
                flags.append({"severity": "high", "what": "%s registration filed %s (%d days ago): shares may be sold" % (form, f["date"], age), "link": link})
            elif form in SHELFS and age <= 180:
                flags.append({"severity": "moderate", "what": "%s shelf filed %s: company can sell shares at any time" % (form, f["date"]), "link": link})
            elif form.startswith("424B") and age <= 45:
                flags.append({"severity": "high", "what": "%s prospectus %s (%d days ago): an offering or ATM sale is active or recent" % (form, f["date"], age), "link": link})
            elif form == "EFFECT" and age <= 30:
                flags.append({"severity": "high", "what": "registration declared effective %s (%d days ago): newly registered shares can be sold now" % (f["date"], age), "link": link})
            elif form == "8-K" and age <= 60:
                if "3.02" in items:
                    flags.append({"severity": "high", "what": "8-K item 3.02 on %s: unregistered share sale (PIPE, warrants or convertibles)" % f["date"], "link": link})
                if "5.03" in items:
                    flags.append({"severity": "moderate", "what": "8-K item 5.03 on %s: charter change (often a reverse split)" % f["date"], "link": link})
                if "3.01" in items:
                    flags.append({"severity": "moderate", "what": "8-K item 3.01 on %s: listing-standard deficiency notice" % f["date"], "link": link})
        raises = [f for f in filings if (f["form"] in REGISTRATIONS or f["form"] in SHELFS or f["form"].startswith("424B"))
                  and f.get("date") and _days(f["date"], today) <= 365]
        if len(raises) >= 2:
            flags.append({"severity": "moderate", "what": "%d offering-related filings in the last 12 months (%s): a history of raising money" % (
                len(raises), ", ".join(sorted({f["form"] for f in raises})))})
    # insider sales: Finnhub's coded transactions when available, else read Form 4s from EDGAR
    ins = data.fh_insider_sells(sym, today)
    if ins is not None:
        checked.append("insider transactions (Finnhub, 30 days)")
        if ins[0]:
            flags.append({"severity": "moderate", "what": "%d insider open-market sale(s) in the last 30 days (%s shares)" % (ins[0], "{:,}".format(int(ins[1])))})
    elif cik and filings:
        f4 = [f for f in filings if f["form"] == "4" and _days(f["date"], today) <= 30][:4]
        sells = 0
        for f in f4:
            try:
                r = data.sec_form4_direction(cik, f["accession"], f["doc"])
            except Exception:
                r = None
            if r and r[0] == "sell":
                sells += 1
        if f4:
            checked.append("Form 4 insider filings (30 days): %d read" % len(f4))
        if sells:
            flags.append({"severity": "moderate", "what": "%d insider sale filing(s) in the last 30 days" % sells})
    shares = None
    if cik:
        try:
            shares = data.sec_shares_outstanding(cik)
        except Exception:
            shares = None
    if shares is None:
        prof = data.fh_profile(sym)
        if prof and prof.get("shares_outstanding"):
            shares = {"shares": prof["shares_outstanding"], "as_of": None, "source": "Finnhub profile",
                      "market_cap": prof.get("market_cap")}
    fh_earn = data.fh_earnings_soon(sym, today)
    if fh_earn:
        flags.append({"severity": "moderate", "what": "earnings scheduled %s (Finnhub calendar)" % fh_earn})
        earnings_ts = None
    for n in news_negatives or []:
        sev = "high" if n["category"] in ("offering / dilution", "going concern") else "moderate"
        flags.append({"severity": sev, "what": "news: %s - %s" % (n["category"], n["title"]), "link": n.get("link")})
    checked.append("news headlines for offering, reverse split, delisting and legal terms")
    if halts is None:
        notes.append("Nasdaq halt list unavailable")
    else:
        checked.append("Nasdaq halt list")
        for h in halts.get(sym.upper(), []):
            flags.append({"severity": "high" if not h.get("resumed") else "moderate",
                          "what": "trading halt %s %s (code %s)%s" % (h.get("halt_date"), h.get("halt_time"), h.get("reason"),
                                                                      ", resumed " + h["resumed"] if h.get("resumed") else ", NOT resumed")})
    if earnings_ts and now_utc:
        import datetime
        when = datetime.datetime.fromtimestamp(earnings_ts, datetime.timezone.utc).replace(tzinfo=None)
        days = (when - now_utc).total_seconds() / 86400
        if 0 <= days <= 7:
            flags.append({"severity": "moderate", "what": "earnings expected within %d day(s)" % max(1, round(days))})
    if any(f["severity"] == "high" for f in flags):
        level = "high"
    elif any(f["severity"] == "moderate" for f in flags):
        level = "moderate"
    elif filings is not None:
        level = "low"
    else:
        level = "unknown"
    return {"level": level, "flags": flags, "checked": checked, "notes": notes,
            "shares_outstanding": shares, "cik": cik}
