"""Early Momentum Scanner: research-only discovery of small-cap stocks that are
starting to draw unusual buying interest.

RESEARCH ONLY - NO AUTOMATED EXECUTION. This package reads market data, news and
SEC filings. It never places, changes or cancels an order, never reads or changes
positions, and never talks to a broker's trading API. Every outbound request goes
through early_momentum.net, which allows only HTTPS GET requests to a fixed list
of data hosts.
"""

__all__ = ["net", "data", "bars", "signals", "catalyst", "dilution", "score", "levels",
           "tracker", "alerts", "run"]
