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
    cik = None
    if not data.sec_user_agent():
        notes.append("SEC filings NOT checked: set SEC_USER_AGENT (name and email) so S-1, S-3, 424B, 8-K and Form 4 filings can be read")
    else:
        try:
            cik = data.sec_cik(sym)
        except Exception as e:
            notes.append("SEC ticker lookup failed: %s" % e)
        if cik is None and not notes:
            notes.append("no SEC filer found for %s (foreign or non-SEC filer?)" % sym)
    shares = None
    if cik:
        try:
            filings = data.sec_filings(cik, days=180, today=today) or []
            checked.append("SEC filings (180 days)")
        except Exception as e:
            filings = []
            notes.append("SEC filings unavailable: %s" % e)
        for f in filings:
            age = _days(f["date"], today)
            form, items = f["form"], f["items"]
            if form in REGISTRATIONS and age <= 120:
                flags.append({"severity": "high", "what": "%s registration filed %s (%d days ago): shares may be sold" % (form, f["date"], age)})
            elif form in SHELFS and age <= 180:
                flags.append({"severity": "moderate", "what": "%s shelf filed %s: company can sell shares at any time" % (form, f["date"])})
            elif form.startswith("424B") and age <= 45:
                flags.append({"severity": "high", "what": "%s prospectus %s (%d days ago): an offering or ATM sale is active or recent" % (form, f["date"], age)})
            elif form == "8-K" and age <= 60:
                if "3.02" in items:
                    flags.append({"severity": "high", "what": "8-K item 3.02 on %s: unregistered share sale (PIPE, warrants or convertibles)" % f["date"]})
                if "5.03" in items:
                    flags.append({"severity": "moderate", "what": "8-K item 5.03 on %s: charter change (often a reverse split)" % f["date"]})
                if "3.01" in items:
                    flags.append({"severity": "moderate", "what": "8-K item 3.01 on %s: listing-standard deficiency notice" % f["date"]})
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
        try:
            shares = data.sec_shares_outstanding(cik)
        except Exception:
            shares = None
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
    elif cik:
        level = "low"
    else:
        level = "unknown"
    return {"level": level, "flags": flags, "checked": checked, "notes": notes,
            "shares_outstanding": shares, "cik": cik}
