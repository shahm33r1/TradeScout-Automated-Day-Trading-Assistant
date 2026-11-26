"""Reuse the existing trader-scanner skill's pure helpers (NYSE calendar, New York
time, Yahoo JSON parsing, the Nasdaq Trader universe rules) without copying them.

Only parsing and calendar functions are used. The skill's own network functions are
never called from here: all fetching goes through early_momentum.net.
"""

import importlib.util
import os

SKILLS_DIR = os.environ.get("TRADER_SKILLS_DIR", os.path.expanduser("~/.claude/skills"))


def _load(name, rel):
    path = os.path.join(SKILLS_DIR, rel)
    if not os.path.exists(path):
        raise ImportError("the %s skill is not installed at %s (run setup/setup.sh)" % (name, path))
    spec = importlib.util.spec_from_file_location("em_skill_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


scan = _load("scanner", "trader-scanner/scan.py")

# The helpers this package relies on, by name, so a change in the skill fails loudly.
to_eastern = scan.to_eastern
eastern_to_utc = scan.eastern_to_utc
ts_to_eastern = scan.ts_to_eastern
parse_now = scan.parse_now
trading_day = scan.trading_day
session = scan.session
parse_spark = scan.parse_spark
parse_chart = scan.parse_chart
build_universe = scan.build_universe
to_yahoo = scan.to_yahoo
NASDAQ_FILES = scan.NASDAQ_FILES
NASDAQ_URL = scan.NASDAQ_URL
