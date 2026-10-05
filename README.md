# Chart Guy Terminal — Downloads

Windows installer for **Chart Guy Terminal** by Chart Guy (LIARG Analyst).

## Install

1. Open **[Releases](https://github.com/muzamil-glitch/chartguy-terminal-releases/releases/latest)** and download the `.exe`.
2. Run it. If Windows shows *"Windows protected your PC"*, click **More info → Run anyway** (the installer is not code-signed yet).
3. Open **Chart Guy Terminal** from the Start menu.

## Inside

- Charts with one-click **Mark structure** (swings, BOS / CHoCH, previous day/week levels)
- **DESK**: top-down bias, 7-rule pre-trade checklist with lot size, trade journal, news context
- Optional **MT5** data from your own broker terminal (read-only)

Educational tool. Not financial advice. No buy/sell signals.

## Chart Guy Data

Free macro data used by the terminal, rebuilt automatically from official, public-domain US Government sources:

| File | What | Source |
|---|---|---|
| `data/calendar.json` | Release times (NFP, CPI, PPI, JOLTS, FOMC…) in UTC | BLS schedule, Federal Reserve |
| `data/nfp.json` | Payrolls change, unemployment rate, average hourly earnings, monthly since 2007 | BLS |
| `data/cpi.json` | Headline and core CPI, m/m and y/y | BLS |
| `data/cot.json` | Weekly COT positioning: EUR, GBP, JPY, CHF, CAD, AUD, NZD, MXN, DXY, gold, silver, oil, bitcoin | CFTC |

Values are the latest revised figures. Consensus forecasts are not included (official sources don't publish them).
