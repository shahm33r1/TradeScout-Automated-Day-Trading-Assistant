"""Tests for the Early Momentum Scanner, including the read-only guarantees.

Run:  python3 -m unittest discover -s tests -v
"""

import ast
import datetime
import gzip
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "early_momentum")
FIX = os.path.join(ROOT, "tests", "fixtures")

from early_momentum import alerts as A
from early_momentum import bars as B
from early_momentum import catalyst as C
from early_momentum import levels as L
from early_momentum import net
from early_momentum import run as R
from early_momentum import score as S
from early_momentum import signals as SIG
from early_momentum import skills
from early_momentum import tracker as T

CFG = R.load_cfg()


def pkg_sources():
    for f in sorted(os.listdir(PKG)):
        if f.endswith(".py"):
            with open(os.path.join(PKG, f), encoding="utf-8") as fh:
                yield f, fh.read()


# ---------------------------------------------------------------- read-only guarantees

class ReadOnly(unittest.TestCase):
    def test_broker_hosts_are_refused(self):
        for url in ("https://paper-api.alpaca.markets/v2/orders",
                    "https://api.alpaca.markets/v2/orders",
                    "https://data.alpaca.markets/v2/stocks/bars",
                    "https://broker-api.alpaca.markets/v1/accounts"):
            with self.assertRaises(net.BlockedRequest, msg=url):
                net.check_url(url)

    def test_only_https_and_allowlisted_hosts(self):
        with self.assertRaises(net.BlockedRequest):
            net.check_url("http://query2.finance.yahoo.com/v8/finance/chart/X")
        with self.assertRaises(net.BlockedRequest):
            net.check_url("https://example.com/")
        self.assertEqual(net.check_url("https://query2.finance.yahoo.com/v8/finance/chart/X"),
                         "query2.finance.yahoo.com")

    def test_no_alpaca_host_on_the_allowlist(self):
        self.assertFalse([h for h in net.ALLOWED_HOSTS if "alpaca" in h])

    def test_no_trading_code_anywhere_in_the_package(self):
        forbidden = [r"/v2/orders", r"/v2/positions", r"place_stock_order", r"submit_order",
                     r"cancel_order", r"close_position", r"trader-broker", r"ticket\.py",
                     r"trader-risk", r"trader-budget", r"size\.py", r"alpaca\.trading",
                     r"TradingClient", r"APCA_API", r"APCA-API"]
        for name, src in pkg_sources():
            for pat in forbidden:
                self.assertIsNone(re.search(pat, src), "%s contains %r" % (name, pat))
            if name != "net.py":
                self.assertNotIn("alpaca.markets", src, name)

    def test_network_is_only_reached_through_net_get_with_GET(self):
        for name, src in pkg_sources():
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in ("urlopen", "Request", "HTTPSConnection", "HTTPConnection"):
                    self.assertEqual(name, "net.py", "%s touches the network directly (%s)" % (name, node.attr))
                if isinstance(node, ast.keyword) and node.arg == "method":
                    self.assertIsInstance(node.value, ast.Constant)
                    self.assertEqual(node.value.value, "GET", "%s uses method=%r" % (name, node.value.value))
            for word in ("POST", "PUT", "PATCH", "DELETE"):
                self.assertIsNone(re.search(r"['\"]%s['\"]" % word, src), "%s mentions HTTP %s" % (name, word))

    def test_imports_are_stdlib_or_own_package(self):
        allowed = {"argparse", "datetime", "json", "os", "sys", "time", "re", "hashlib", "urllib",
                   "importlib", "email", "xml", "gzip", "statistics"}
        for name, src in pkg_sources():
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.Import):
                    for n in node.names:
                        self.assertIn(n.name.split(".")[0], allowed, "%s imports %s" % (name, n.name))
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    self.assertIn(node.module.split(".")[0], allowed, "%s imports from %s" % (name, node.module))

    def test_only_the_scanner_skill_is_loaded(self):
        with open(os.path.join(PKG, "skills.py"), encoding="utf-8") as fh:
            src = fh.read()
        loads = re.findall(r'_load\("(\w+)",\s*"([^"]+)"\)', src)
        self.assertEqual(loads, [("scanner", "trader-scanner/scan.py")])

    def test_research_label_is_on_every_output(self):
        self.assertEqual(R.BANNER, "RESEARCH ONLY - NO AUTOMATED EXECUTION")

    def test_scheduled_task_instructions_are_research_only(self):
        with open(os.path.join(ROOT, "daily-task", "early-momentum-prompt.md"), encoding="utf-8") as fh:
            prompt = fh.read()
        self.assertIn("RESEARCH ONLY - NO AUTOMATED EXECUTION", prompt)
        self.assertIn("Never place, change, cancel or replace any order", prompt)
        for pat in (r"/v2/orders", r"/v2/positions", r"paper-api", r"api\.alpaca", r"place_stock_order",
                    r"ticket\.py", r"\bPOST\b", r"\bDELETE /"):
            self.assertIsNone(re.search(pat, prompt), "the momentum task mentions %r" % pat)
        self.assertNotRegex(prompt, r'collection:\s*"?(trades|lessons)"?')

    def test_dashboard_section_is_labelled(self):
        with open(os.path.join(ROOT, "journal", "trade-journal.html"), encoding="utf-8") as fh:
            page = fh.read()
        self.assertIn("RESEARCH ONLY – NO AUTOMATED EXECUTION", page)
        self.assertIn("EARLY MOMENTUM OPPORTUNITIES", page)


# ---------------------------------------------------------------- bar math

def synth(day, closes, vols, start=datetime.time(9, 30), prev_days=3, prev_vol=1000, prev_close=10.0):
    """Build a Yahoo-like series: prev_days full sessions then today's bars from `start`."""
    t, o, h, l, c, v = [], [], [], [], [], []
    d = day
    days = []
    while len(days) < prev_days:
        d -= datetime.timedelta(days=1)
        if skills.session(d):
            days.append(d)
    for pd in reversed(days):
        for k in range(78):
            et = datetime.datetime.combine(pd, datetime.time(9, 30)) + datetime.timedelta(minutes=5 * k)
            t.append(int((skills.eastern_to_utc(et) - datetime.datetime(1970, 1, 1)).total_seconds()))
            o.append(prev_close); h.append(prev_close + 0.05); l.append(prev_close - 0.05); c.append(prev_close); v.append(prev_vol)
    for k, (px, vol) in enumerate(zip(closes, vols)):
        et = datetime.datetime.combine(day, start) + datetime.timedelta(minutes=5 * k)
        t.append(int((skills.eastern_to_utc(et) - datetime.datetime(1970, 1, 1)).total_seconds()))
        prev = closes[k - 1] if k else prev_close
        o.append(prev); h.append(max(prev, px) + 0.01); l.append(min(prev, px) - 0.01); c.append(px); v.append(vol)
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v, "meta": {}}


DAY = datetime.date(2026, 11, 25)


class Bars(unittest.TestCase):
    def test_only_closed_bars_are_used(self):
        ser = synth(DAY, [10.1, 10.2, 10.3], [100, 100, 100])
        bars = B.to_bars(ser, datetime.datetime(2026, 11, 25, 9, 44))
        today = [b for b in bars if b["et"].date() == DAY]
        self.assertEqual(len(today), 2)          # the 9:40 bar closes at 9:45: not yet

    def test_vwap(self):
        bars = [{"h": 11, "l": 9, "c": 10, "v": 100}, {"h": 13, "l": 11, "c": 12, "v": 300}]
        self.assertAlmostEqual(B.vwap(bars), (10 * 100 + 12 * 300) / 400.0)
        self.assertIsNone(B.vwap([{"h": 1, "l": 1, "c": 1, "v": 0}]))

    def test_relative_volume_same_time_of_day(self):
        ser = synth(DAY, [10.0] * 6, [3000] * 6, prev_vol=1000)
        now = datetime.datetime(2026, 11, 25, 10, 0)
        rv = B.relative_volume(B.to_bars(ser, now), DAY, now)
        self.assertAlmostEqual(rv, 3.0, places=6)

    def test_relative_volume_none_before_open(self):
        ser = synth(DAY, [10.0], [100], start=datetime.time(8, 0))
        now = datetime.datetime(2026, 11, 25, 9, 0)
        self.assertIsNone(B.relative_volume(B.to_bars(ser, now), DAY, now))

    def test_prev_close_is_last_regular_bar_before_today(self):
        ser = synth(DAY, [10.5], [100], prev_close=9.75)
        now = datetime.datetime(2026, 11, 25, 9, 40)
        pc, _ = B.prev_close(B.to_bars(ser, now), DAY)
        self.assertEqual(pc, 9.75)


class BlockPrints(unittest.TestCase):
    def test_flat_giant_bar_is_capped_but_real_spike_is_kept(self):
        t0 = datetime.datetime(2026, 11, 25, 10, 0)
        bars = [{"et": t0 + datetime.timedelta(minutes=5 * k), "o": 2.0, "h": 2.01, "l": 1.99, "c": 2.0, "v": 1000} for k in range(20)]
        bars[5] = dict(bars[5], v=100000)                      # flat price: a block print
        bars[9] = dict(bars[9], v=60000, c=2.2, h=2.25)         # +10% on volume: real buying
        out, blocked = B.dampen_blocks(bars)
        self.assertEqual(out[5]["v"], 3000)
        self.assertEqual(out[5]["v_raw"], 100000)
        self.assertEqual(out[9]["v"], 60000)
        self.assertEqual(blocked, [bars[5]["et"]])


class RealData(unittest.TestCase):
    """KYNB on 2026-11-25 (real Yahoo bars): +34.8% by the close on heavy volume."""

    @classmethod
    def setUpClass(cls):
        with gzip.open(os.path.join(FIX, "KYNB-2026-11-25-5d5m.json.gz"), "rt") as fh:
            cls.series = skills.parse_chart(fh.read(), "KYNB")

    def test_snapshot_at_10_50(self):
        snap = SIG.analyze(self.series, datetime.datetime(2026, 11, 25, 10, 50))
        self.assertEqual(snap["session"], "regular")
        self.assertGreater(snap["rvol"], 10)                 # far above normal volume
        self.assertTrue(0 < snap["change_pct"] < 15)         # still early in the move
        self.assertIsNone(snap["signals"]["unusual_premarket_volume"])   # Yahoo has no premarket volume

    def test_no_future_bars_leak(self):
        early = SIG.analyze(self.series, datetime.datetime(2026, 11, 25, 10, 50))
        self.assertTrue(all(b["t"] <= "10:45" for b in early["last_bars"]))


# ---------------------------------------------------------------- scoring

def snap_stub(**kw):
    s = {"price": 3.0, "session": "regular", "change_pct": 8.0, "premarket_change_pct": 4.0,
         "rvol": None, "volume_accel": None, "vwap": 2.9, "premarket_high": 3.1,
         "dist_to_resistance_pct": None, "resistance": [], "support": [], "dollar_volume_today": 500000,
         "day_high": 3.05, "last_bars": [{"l": 2.9}],
         "signals": {k: None for k in ("unusual_premarket_volume", "rvol_rising", "buying_pressure", "near_resistance",
                                       "premarket_high_break", "above_vwap", "higher_highs_lows", "volume_on_up_moves",
                                       "unusual_volume")}}
    s.update(kw)
    return s


class Scoring(unittest.TestCase):
    def test_low_float_and_big_move_alone_cannot_score_high(self):
        snap = snap_stub(change_pct=12.0)
        sc = S.compute(CFG, snap, None, {"level": "low"}, float_shares=2e6)
        self.assertLessEqual(sc["parts"]["move"] + sc["parts"]["supply"], 20)
        self.assertLessEqual(sc["score"], 45)

    def test_missing_data_scores_zero_and_is_said(self):
        sc = S.compute(CFG, snap_stub(), None, {"level": "low"})
        self.assertEqual(sc["parts"]["volume"], 0)
        self.assertTrue(any("no data" in n for n in sc["notes"]))

    def test_extended_move_is_penalised_and_capped(self):
        snap = snap_stub(change_pct=90.0, rvol=12.0, volume_accel=2.5)
        snap["signals"].update(above_vwap=True, higher_highs_lows=True, buying_pressure=True, volume_on_up_moves=True)
        sc = S.compute(CFG, snap, {"best": {"category": "FDA approval", "quality": 20, "age_hours": 1}, "negatives": []},
                       {"level": "low"}, float_shares=5e6)
        self.assertLessEqual(sc["score"], 50)
        self.assertTrue(any("extended" in p["why"] for p in sc["penalties"]))

    def test_dilution_subtracts(self):
        snap = snap_stub(rvol=6.0, volume_accel=2.0)
        snap["signals"].update(above_vwap=True, higher_highs_lows=True)
        clean = S.compute(CFG, snap, None, {"level": "low"})["score"]
        dirty = S.compute(CFG, snap, None, {"level": "high"})["score"]
        self.assertEqual(clean - dirty, 25)

    def test_every_point_has_a_reason(self):
        snap = snap_stub(rvol=4.0, volume_accel=1.6)
        snap["signals"].update(above_vwap=True)
        sc = S.compute(CFG, snap, None, {"level": "low"})
        self.assertTrue(any("relative volume" in r for r in sc["reasons"]))
        self.assertTrue(any("VWAP" in r for r in sc["reasons"]))

    def test_borderline_is_not_rejection(self):
        snap = snap_stub(price=22.0, rvol=3.5, change_pct=5.0)
        crit, label = S.criteria_match(CFG, snap, None, 15e6, 200e6)
        self.assertEqual(crit["price"], "borderline")
        self.assertEqual(label, "borderline")

    def test_score_is_bounded(self):
        snap = snap_stub(rvol=50.0, volume_accel=9.0, change_pct=10.0)
        snap["signals"].update(above_vwap=True, higher_highs_lows=True, premarket_high_break=True,
                               buying_pressure=True, volume_on_up_moves=True)
        sc = S.compute(CFG, snap, {"best": {"category": "FDA approval", "quality": 20, "age_hours": 1}, "negatives": []},
                       {"level": "low"}, float_shares=3e6, spread_pct=0.2)
        self.assertTrue(0 <= sc["score"] <= 100)


class Catalysts(unittest.TestCase):
    NOW = datetime.datetime(2026, 11, 25, 15, 0)

    def item(self, title, hours_ago):
        dt = self.NOW - datetime.timedelta(hours=hours_ago)
        return {"title": title, "link": "https://example.org/x", "published": dt.strftime("%a, %d %b %Y %H:%M:%S GMT")}

    def test_fda_beats_product(self):
        r = C.classify([self.item("Acme launches new widget", 1), self.item("Acme receives FDA approval for drug", 2)], self.NOW)
        self.assertEqual(r["best"]["category"], "FDA approval")

    def test_old_and_future_news_are_ignored(self):
        r = C.classify([self.item("Acme receives FDA approval", 100), self.item("Acme receives FDA approval", -3)], self.NOW)
        self.assertIsNone(r["best"])

    def test_offering_is_negative(self):
        r = C.classify([self.item("Acme announces $5 million registered direct offering", 1)], self.NOW)
        self.assertEqual(r["negatives"][0]["category"], "offering / dilution")


class AlertsAndTracking(unittest.TestCase):
    def rec(self, score=60, **kw):
        r = {"ticker": "ABC", "name": "ABC Corp", "score": score, "tier": "watch", "price": 2.0, "session": "regular",
             "move_pct": 8.0, "rvol": 4.0, "premarket_high": 1.9, "match": "borderline",
             "signals": {"unusual_volume": True, "premarket_high_break": False},
             "plan": {"key_level": {"name": "prior day high", "price": 2.03}},
             "dilution": {"level": "low", "flags": []}, "catalyst": {"best": None, "negatives": []}}
        r.update(kw)
        return r

    def test_no_repeat_notifications(self):
        st = {}
        first = A.evaluate(self.rec(), st, CFG)
        again = A.evaluate(self.rec(), st, CFG)
        self.assertTrue(first)
        self.assertEqual(again, [])

    def test_material_change_notifies_again(self):
        st = {}
        A.evaluate(self.rec(60), st, CFG)
        more = A.evaluate(self.rec(72), st, CFG)
        self.assertEqual([a["type"] for a in more], ["stronger_momentum"])

    def test_new_dilution_risk_notifies(self):
        st = {}
        A.evaluate(self.rec(), st, CFG)
        risky = self.rec(dilution={"level": "high", "flags": [{"severity": "high", "what": "424B5 prospectus"}]})
        self.assertEqual([a["type"] for a in A.evaluate(risky, st, CFG)], ["risk"])

    def test_combine_one_message_per_stock(self):
        out = A.combine(A.evaluate(self.rec(), {}, CFG))
        self.assertEqual(len(out), 1)

    def test_tracker_checkpoints_and_extremes(self):
        det = {}
        T.record_detection(det, {"ticker": "ABC", "name": "x", "session": "regular", "price": 2.0, "score": 60, "tier": "watch"},
                           datetime.datetime(2026, 11, 25, 10, 0))
        bars = [{"et": datetime.datetime(2026, 11, 25, 10, 0) + datetime.timedelta(minutes=5 * k),
                 "h": 2.0 + 0.1 * k, "l": 1.9, "c": 2.0 + 0.1 * k} for k in range(13)]
        T.update(det["ABC"], bars, datetime.datetime(2026, 11, 25, 11, 5))
        e = det["ABC"]
        self.assertAlmostEqual(e["checkpoints"]["5m"]["price"], 2.0)
        self.assertAlmostEqual(e["checkpoints"]["60m"]["price"], 3.1)
        self.assertAlmostEqual(e["max_gain_pct"], 60.0)
        self.assertAlmostEqual(e["max_drawdown_pct"], -5.0)


class Levels(unittest.TestCase):
    def test_levels_are_information_only_and_consistent(self):
        snap = snap_stub(price=2.95, premarket_high=3.10, vwap=2.80,
                         resistance=[{"name": "premarket high", "price": 3.10}, {"name": "5-day high", "price": 3.60}],
                         support=[{"name": "prior close", "price": 2.70}], last_bars=[{"l": 2.85}])
        p = L.plan(snap, {}, {"level": "low", "flags": []}, {"best": None, "negatives": []})
        self.assertTrue(p["for_information_only"])
        self.assertLess(p["stop_reference"], p["entry_zone"][0])
        self.assertGreater(p["target_1"], p["entry_zone"][1])
        self.assertGreater(p["target_2"], p["target_1"])
        self.assertIn("no confirmed catalyst: moves without news fade more often", p["reasons_to_avoid"])


if __name__ == "__main__":
    unittest.main()
