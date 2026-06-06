# 📈 Autonomous Stock Portfolio Manager

A fully automated portfolio management system for CAC40 + AEX stocks, running on GitHub Actions with Telegram alerts. No VPS required.

**Target:** 15% net annual return | **Capital:** €1,890 | **Universe:** CAC40 + AEX (60+ stocks)

---

## 🗂 Architecture

```
main.py           ← Orchestrator (runs daily via GitHub Actions)
config.py         ← All constants and parameters (single source of truth)
regime.py         ← Market regime detection (BULL / NEUTRAL / BEAR)
universe.py       ← Stock universe + liquidity filter
scorer.py         ← 0-100 scoring model (50 pts technical + 50 pts fundamental)
portfolio.py      ← Portfolio construction and weight optimization
signals.py        ← Buy/sell/trailing stop signal generation
state.py          ← portfolio_state.json persistence
utils.py          ← Beta calculation, fee calculator, sector mapping
telegram_bot.py   ← Alerts + bidirectional command handler
```

---

## 🚀 Setup Guide (Step by Step)

### Step 1 — Fork / Create the GitHub Repository

1. Go to [github.com](https://github.com) and sign in
2. Click **New repository** → name it `portfolio-manager` (or any name)
3. Upload all files from this project, preserving the folder structure:
   ```
   portfolio_manager/
   ├── main.py
   ├── config.py
   ├── regime.py
   ├── universe.py
   ├── scorer.py
   ├── portfolio.py
   ├── signals.py
   ├── state.py
   ├── utils.py
   ├── telegram_bot.py
   ├── requirements.txt
   ├── portfolio_state.json
   └── .github/
       └── workflows/
           └── portfolio_manager.yml
   ```

### Step 2 — Create Your Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot` and follow the prompts:
   - Choose a name (e.g. `My Portfolio Bot`)
   - Choose a username (must end in `bot`, e.g. `myportfolio_bot`)
3. BotFather will reply with your **Bot Token** — looks like:
   ```
   1234567890:ABCDEFghijklmnopqrstuvwxyz1234567890
   ```
   Save this token.

### Step 3 — Get Your Telegram Chat ID

1. Start a conversation with your new bot (search for its username in Telegram)
2. Send any message (e.g. `/start`)
3. Open this URL in your browser (replace `YOUR_TOKEN`):
   ```
   https://api.telegram.org/botYOUR_TOKEN/getUpdates
   ```
4. Find `"chat":{"id":XXXXXXXX}` in the response — that number is your **Chat ID**

### Step 4 — Add Secrets to GitHub

1. In your GitHub repo: **Settings → Secrets and variables → Actions**
2. Click **New repository secret** for each:

   | Secret Name | Value |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | Your bot token from Step 2 |
   | `TELEGRAM_CHAT_ID` | Your chat ID from Step 3 |

### Step 5 — First Manual Run to Validate Setup

1. Go to **Actions** tab in your GitHub repo
2. Click **Portfolio Manager** in the left sidebar
3. Click **Run workflow** → **Run workflow**
4. Watch the logs — you should see:
   - Regime detection running
   - Universe scan (60+ tickers)
   - Scoring computation
   - Telegram message delivered to your bot
5. Check your Telegram — you should receive a weekly summary or buy signals

### Step 6 — Automatic Schedule

After setup, the system runs automatically:
- **Mon–Fri at 08:00 CET**: Full pipeline (regime → score → signals → alerts)
- **Every Monday**: Also sends weekly portfolio summary
- **Biweekly Mondays**: Rebalancing check

The `portfolio_state.json` is automatically committed back to the repo after each run.

---

## 📱 Telegram Command Reference

All commands must come from your authorized `TELEGRAM_CHAT_ID`.

### `/bought <TICKER> <NB_SHARES> <EXECUTION_PRICE>`
Record a confirmed buy execution.

```
/bought AIR.PA 3 145.50
```

Reply includes:
- Confirmed position details
- Stop-loss and take-profit levels
- Slippage vs model price
- Warning if ticker is outside model universe

---

### `/sold <TICKER> <NB_SHARES> <EXECUTION_PRICE>`
Record a confirmed sell execution.

```
/sold AIR.PA 3 152.80
```

Reply includes:
- Realized P&L (EUR and %)
- Updated portfolio snapshot

---

### `/portfolio`
Display all open positions with live P&L.

Shows:
- Each position: ticker, entry price, current price, P&L %, weight, beta
- Portfolio beta
- Sector exposure
- Cash available
- Total P&L vs initial capital

---

### `/status`
System status overview.

Shows:
- Current market regime
- Last run timestamp
- Number of open positions
- Cash available
- Universe size

---

### `/regime`
Detailed regime analysis.

Shows:
- CAC40 price, MA50, MA200
- Stoxx600 secondary confirmation
- Full parameters for current regime (thresholds, beta targets, stop levels)

---

## 📊 How the Scoring Works

Each stock receives a **0–100 score** composed of:

### Technical Block (50 pts)
| Component | Max pts | Description |
|---|---|---|
| Trend (MA alignment) | 15 | MA20 > MA50 > MA200 (5 pts each) |
| RSI 14d | 10 | Regime-adjusted optimal range |
| Volume confirmation | 10 | vs 20-day average (>200% = full) |
| MACD | 8 | Bullish crossover = full, bearish = 0 |
| 3M Momentum | 7 | vs universe median (top quartile = full) |

### Fundamental Block (50 pts)
| Component | Max pts | Description |
|---|---|---|
| EPS revisions | 15 | Analyst upgrades last 30 days |
| Relative valuation | 15 | PEG/P-E vs regime context |
| Balance sheet | 10 | Debt/EBITDA + FCF yield |
| Growth | 10 | Revenue + EPS 3Y CAGR |

### Regime Bonus
- **BULL**: +5 pts for stocks with beta > 1.3 (rewards momentum)
- **BEAR**: +5 pts for stocks with beta < 1.0 (rewards safety)

---

## 🌡 Market Regime Logic

| Regime | Condition | Beta Target | Max Lines | Cash |
|---|---|---|---|---|
| BULL | MA50 > MA200 AND price > MA50 | 1.3–1.6 | 7 | 0% |
| NEUTRAL | MA50 > MA200 BUT price < MA50 | 1.0–1.3 | 6 | 10–15% |
| BEAR | MA50 < MA200 | 0.7–1.0 | 5 | 20–30% |

---

## 🔴 Sell Triggers

1. **Stop-loss**: BULL −10%, NEUTRAL −8%, BEAR −6%
2. **Take-profit**: BULL +22%, NEUTRAL +18%, BEAR +14%
3. **Trailing stop** (BULL only): −8% from peak, activates after +15%
4. **Score degradation**: Below threshold for 2 consecutive runs
5. **Regime change to BEAR**: All positions with beta > 1.2 get immediate sell alert

---

## 💶 Fee Model (DEGIRO Euronext)

- Per order: **€0.50 + 0.004%** of order value
- Round-trip cost gate: skip trade if **buy+sell > 0.8%** of position
- Minimum position: **€200**

---

## ⚙️ Customization

All parameters live in `config.py`. Key settings:

```python
INITIAL_CAPITAL = 1890.0      # Your total capital in EUR
MAX_POSITION_PCT = 0.20        # 20% max per stock
MAX_SECTOR_PCT = 0.35          # 35% max per sector
REBALANCE_FREQUENCY = "biweekly"

# Add/remove tickers:
CAC40_TICKERS = [...]
AEX_TICKERS = [...]
```

---

## 🔒 Security

- **Telegram credentials** are stored as GitHub Secrets only — never in code
- Commands are only accepted from your `TELEGRAM_CHAT_ID`
- Unauthorized command attempts are logged and silently ignored
- `portfolio_state.json` is committed to your private repo

---

## 🐛 Troubleshooting

**Actions not triggering?**
- Check that the workflow file is at `.github/workflows/portfolio_manager.yml`
- Ensure Actions are enabled: repo Settings → Actions → Allow all actions

**Telegram messages not arriving?**
- Verify secrets are named exactly `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
- Confirm you started a conversation with the bot before the first run
- Test manually: run the workflow from the Actions tab

**Data errors in logs?**
- yfinance occasionally has rate limits — the system skips failing tickers gracefully
- If many tickers fail, check yfinance GitHub for known issues

**Score all zeros?**
- Usually a data availability issue. Check the logs for specific ticker errors.
- The system degrades gracefully: missing data = 0 pts for that component, not a crash.

---

## 📄 License

MIT — use freely, modify to fit your strategy.

> ⚠️ **Disclaimer**: This is an algorithmic decision-support tool, not financial advice. Always apply your own judgment before executing trades. Past model performance does not guarantee future returns.
