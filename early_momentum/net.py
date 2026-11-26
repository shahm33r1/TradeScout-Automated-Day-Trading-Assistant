"""The only door to the network for the Early Momentum Scanner.

Read-only by construction:
- HTTPS GET only. There is no function here that sends POST, PUT, PATCH or DELETE.
- Only the hosts in ALLOWED_HOSTS. Any broker trading host is refused, by name,
  even if someone adds it to the list by mistake.

Every response can be recorded to a folder and replayed from it (--offline), so a
whole day can be re-run and tested without the network.
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

ALLOWED_HOSTS = frozenset({
    "query1.finance.yahoo.com", "query2.finance.yahoo.com", "feeds.finance.yahoo.com",
    "www.nasdaqtrader.com",
    "data.sec.gov", "www.sec.gov", "efts.sec.gov",
    "api.stocktwits.com",
    "news.google.com",
    "finnhub.io",
})
# Free Finnhub plans allow 60 calls a minute: keep well under it.
HOST_PAUSE = {"finnhub.io": 1.1}
# Never reachable from this package, whatever ALLOWED_HOSTS says.
FORBIDDEN_HOSTS = frozenset({"paper-api.alpaca.markets", "api.alpaca.markets",
                             "broker-api.alpaca.markets", "broker-api.sandbox.alpaca.markets"})

BROWSER_UA = "Mozilla/5.0 (TradeScout research; read-only)"
PAUSE = 0.15

_state = {"offline": None, "record": None, "cache": None, "sleep": time.sleep, "calls": 0}


class BlockedRequest(Exception):
    """A request this package is not allowed to make."""


class FetchError(Exception):
    """The source did not answer usefully."""


def configure(offline=None, record=None, sleep=None, cache=None):
    """offline: replay only. record: save every live response. cache: replay when
    saved, otherwise fetch live and save (for replays that wander past a recording)."""
    _state["offline"] = offline
    _state["record"] = record or cache
    _state["cache"] = cache
    if sleep is not None:
        _state["sleep"] = sleep


def check_url(url):
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    if p.scheme != "https":
        raise BlockedRequest("only https is allowed: %s" % url)
    if host in FORBIDDEN_HOSTS or "alpaca" in host:
        raise BlockedRequest("broker hosts are never contacted by the Early Momentum Scanner: %s" % host)
    if host not in ALLOWED_HOSTS:
        raise BlockedRequest("host not on the research allowlist: %s" % host)
    return host


def _key(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]


def get(url, headers=None, timeout=20):
    """HTTPS GET -> text. Raises BlockedRequest or FetchError."""
    host = check_url(url)
    if _state["cache"]:
        cpath = os.path.join(_state["cache"], _key(url) + ".json")
        if os.path.exists(cpath):
            with open(cpath, encoding="utf-8") as fh:
                return json.load(fh)["body"]
    if _state["offline"]:
        path = os.path.join(_state["offline"], _key(url) + ".json")
        if not os.path.exists(path):
            raise FetchError("not in the offline folder: %s" % url)
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)["body"]
    h = {"User-Agent": BROWSER_UA, "Accept": "*/*"}
    h.update(headers or {})
    last = None
    for attempt in range(2):
        req = urllib.request.Request(url, headers=h, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
            _state["calls"] += 1
            if _state["record"]:
                os.makedirs(_state["record"], exist_ok=True)
                with open(os.path.join(_state["record"], _key(url) + ".json"), "w", encoding="utf-8") as fh:
                    json.dump({"url": url, "body": body}, fh)
            _state["sleep"](HOST_PAUSE.get(host, PAUSE))
            return body
        except urllib.error.HTTPError as e:
            last = "HTTP %d from %s" % (e.code, host)
            try:
                text = e.read().decode("utf-8", "replace")
            except Exception:
                text = ""
            e.close()
            if e.code == 429 and attempt == 0:
                _state["sleep"](5)
                continue
            if 400 <= e.code < 500 and text.strip().startswith("{"):
                return text
            break
        except (urllib.error.URLError, OSError) as e:
            last = "%s: %s" % (host, getattr(e, "reason", e))
            if attempt == 0:
                _state["sleep"](2)
                continue
    raise FetchError(last or "no answer from %s" % host)


def calls():
    return _state["calls"]
