# TradeScout: Automated Paper Day-Trading Assistant

TradeScout is an automated day-trading assistant that runs every weekday on an Alpaca paper trading account. Each morning it scans about 5,900 U.S. stocks for long setups, checks the news, SEC filings and social media for the strongest ones, and tests each trade plan with 10,000 simulated sessions before any money is at risk. During the day it watches the chosen stocks bar by bar, buys when a trigger fires, sells everything before the close, and sends an end-of-day report with the profit and loss of every trade.

TradeScout also reviews every losing trade, finds out why it lost using real market data, and keeps a journal of lessons. When the same cause shows up in several losses, it becomes a rule that makes the next day's stock selection stricter.

This project uses paper trading only, with a $1,000 budget. No real money is involved, and nothing here is financial advice.

## How a Trading Day Works

1. **Pre-market scan (8:41 AM):** scans every U.S. listed stock and keeps long setups: 20-day closing-high breakouts, bull flags, and stocks reclaiming their 20-day average. It ranks them and checks news, SEC 8-K filings, StockTwits and Google News for the top 5.
2. **Pick 1 to 3 stocks:** each pick gets a trigger level (yesterday's high), a stop (yesterday's low or 1.5% below the trigger, whichever is closer), and targets at one and two times the risk.
3. **Time Machine test:** replays each plan 10,000 times using the stock's last 60 days of 5-minute bars, and drops any plan where the odds lean toward the stop.
4. **Watch from 9:45 AM:** checks each pick bar by bar until 3:30 PM.
5. **Buy at the trigger:** when a 5-minute bar closes above the trigger, it re-plans from the real price, sizes the trade to the $1,000 budget, runs a risk check, and places a bracket order with the stop and target attached.
6. **Flat by 3:55 PM:** sells anything still open so no trade is held overnight.
7. **End-of-day report:** lists every trade with its buy and sell prices, profit or loss, wins and losses, and the account's change for the day.
8. **Learn from losses:** tags each loss with its cause (market sell-off, news after entry, late entry, weak volume, and others) and saves it to the journal. A cause becomes an active rule only after 3 losses, and only if it would have blocked fewer winning trades than losing ones.

## Trading Rules

| Rule | Setting |
| --- | --- |
| Account | Alpaca paper trading only |
| Budget | $1,000 for all trades in a day |
| Cash kept aside | 10% ($100) |
| Largest position | 75% of the budget ($750) |
| New trades allowed | 9:45 AM to 3:30 PM New York time |
| Sell everything by | 3:55 PM New York time (12:55 PM on early-close days) |
| Stop-loss | Required on every trade |
| Options, margin, short selling | Not allowed |

## Early Momentum Scanner (Research Only)

**RESEARCH ONLY – NO AUTOMATED EXECUTION.** The Early Momentum Scanner looks for small-cap and low-float stocks that are starting to attract unusual buying interest, before a large move. It never places, changes or cancels an order and is completely separate from the trading strategy above.

- **Where it runs:** a separate cloud environment with no broker keys, every weekday from 4:05 AM to 4:00 PM New York time. All network requests go through one module that allows only read-only requests to a fixed list of data sources, and tests fail if any trading code appears.
- **How it finds stocks:** each morning it builds a list of about 2,300 U.S. stocks priced $0.80 to $25, then scans them every 5 to 10 minutes. Stocks that start moving get a full check of relative volume, volume acceleration, VWAP, higher highs and lows, premarket-high breaks, resistance levels, news catalysts and dilution risk.
- **Early Momentum Score (0–100):** volume (35), technical setup (25), catalyst (20), move size (10), supply (10) and liquidity (5), minus penalties for dilution, negative news and moves that are already extended. A low float or a big percentage gain alone cannot produce a high score, and every point comes with a written reason.
- **Dilution checks:** recent S-1, S-3 and 424B filings, 8-K items 3.02, 3.01 and 5.03, insider sales on Form 4, offering and reverse-split headlines, upcoming earnings and Nasdaq trading halts.
- **Alerts:** a push notification when a stock first qualifies, shows unusual volume, nears or breaks a key level, gets materially stronger, or develops new dilution or negative news. The same alert never repeats.
- **Tracking:** every flagged stock is tracked from first detection, with its price after 5, 15, 30 and 60 minutes and its best and worst move, to measure whether the scanner is actually early.
- **Dashboard:** an EARLY MOMENTUM OPPORTUNITIES section on the trade journal page, with filters, sorting, reference levels (information only) and reasons to stay out.

**Data limits:** prices and volume come from Yahoo Finance, which is free, unofficial and can lag. Yahoo reports no premarket volume, and no free source provides float, so float is shown as unknown (shares outstanding is shown as an upper bound). SEC filing checks need the `SEC_USER_AGENT` setting.

## Technologies Used

Python, Claude Code scheduled tasks, Alpaca Paper Trading API, Yahoo Finance market data, SEC EDGAR, StockTwits, Google News RSS, HTML and JavaScript (trade journal), and JSON.

## Project Purpose

The purpose of TradeScout is to test a complete day-trading process from start to finish without risking real money. By combining market scanning, news checks, simulation, strict risk rules, and a journal that learns from losses, the project shows how automation can enforce discipline in trading. Most people who day trade lose money, and this project is a way to measure honestly how a rules-based strategy performs over time.
