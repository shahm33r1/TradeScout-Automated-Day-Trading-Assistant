# TradeScout: Automated Paper Day Trading Assistant

TradeScout is an automated day trading assistant that runs every weekday on an Alpaca paper trading account. Each morning it scans about 5,900 U.S. stocks for long setups, checks the news, SEC filings and social media for the strongest ones, and tests each trade plan with 10,000 simulated sessions before any money is at risk. During the day it watches the chosen stocks bar by bar, buys when a trigger fires, sells everything before the close, and sends an end-of-day report with the profit and loss of every trade.

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

## Technologies Used

Python, Claude Code scheduled tasks, Alpaca Paper Trading API, Yahoo Finance market data, SEC EDGAR, StockTwits, Google News RSS, HTML and JavaScript (trade journal), and JSON.

## Project Purpose

The purpose of TradeScout is to test a complete day-trading process from start to finish without risking real money. By combining market scanning, news checks, simulation, strict risk rules, and a journal that learns from losses, the project shows how automation can enforce discipline in trading. Most people who day trade lose money, and this project is a way to measure honestly how a rules-based strategy performs over time.
