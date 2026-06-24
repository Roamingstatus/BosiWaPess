# BosiWaPess

**EMA Pullback Forex Alert Bot** — a Python Telegram bot that scans forex pairs for bullish EMA pullback patterns and sends alerts.

> **This is an alert-only bot. It does NOT execute trades.**

**Requires Python 3.10+**

## What it does

The bot scans at each **15-minute candle close** (UTC boundaries at `:00`, `:15`, `:30`, `:45`, plus a 30-second buffer) and checks these forex pairs:

| Pair    | Yahoo Finance Ticker |
|---------|----------------------|
| EUR/USD | `EURUSD=X`           |
| GBP/USD | `GBPUSD=X`           |
| USD/JPY | `USDJPY=X`           |

It downloads 15-minute candles (5 days of history), calculates **EMA20** and **EMA50**, and looks for this pattern on the most recently **completed** candle:

1. EMA20 is above EMA50 (uptrend)
2. Previous candle closed below EMA20 (pullback)
3. Current candle closed back above EMA20 (bounce)
4. Current candle is bullish (close > open)

When a signal is found, it sends **one Telegram alert per candle** per pair. Alert history is saved to `alert_state.json` so restarts do not re-send the same alert.

On startup, the bot sends a **“Bot started”** test message so you can confirm Telegram credentials work.

## Install dependencies

```bash
# Create a virtual environment (recommended, Python 3.10+)
python3.10 -m venv venv

# Activate it
# Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# macOS / Linux:
source venv/bin/activate

# Install packages
pip install -r requirements.txt
```

## Set Telegram environment variables

### Local development (recommended)

Copy the example file and fill in your values — the bot loads `.env` automatically via `python-dotenv`:

```bash
cp .env.example .env
```

Edit `.env`:

```
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

### Manual export (alternative)

```bash
# Windows PowerShell
$env:TELEGRAM_BOT_TOKEN = "your_bot_token_here"
$env:TELEGRAM_CHAT_ID = "your_chat_id_here"

# macOS / Linux
export TELEGRAM_BOT_TOKEN="your_bot_token_here"
export TELEGRAM_CHAT_ID="your_chat_id_here"
```

**How to get credentials:**

1. Create a bot with [@BotFather](https://t.me/BotFather) on Telegram and copy the **bot token**.
2. Get your **chat ID** (message [@userinfobot](https://t.me/userinfobot) or add your bot to a group and use the group chat ID).

**Never commit your real token or chat ID to git.**

## Run locally

```bash
python main.py
```

You should see timestamped log output for each scan. On startup you will receive a Telegram “Bot started” message. When a bullish EMA pullback is detected, you will receive a signal alert.

Press `Ctrl+C` to stop the bot.

## Deploy to Railway

[Railway](https://railway.app/) can run this bot as a background worker.

1. Push this project to a GitHub repository.
2. Create a new project on Railway and connect your repo.
3. Railway reads `runtime.txt` and uses **Python 3.13** (required for mise attestation verification on current build images).
4. Railway detects the `Procfile` and runs: `worker: python main.py`
5. In Railway **Variables**, add:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
6. Deploy. The worker aligns scans to 15-minute candle closes.

> **Note:** On Railway, set environment variables in the dashboard — do not rely on a `.env` file in the repo.

## Project structure

```
BosiWaPess/
├── main.py           # Bot logic
├── requirements.txt  # Python dependencies
├── runtime.txt       # Python 3.13 for Railway
├── Procfile          # Railway worker command
├── .env.example      # Template for local .env
├── .gitignore
└── README.md
```

## Features

- **Candle-aligned scheduling** — scans shortly after each 15-minute UTC close
- **Persistent dedup** — `alert_state.json` prevents duplicate alerts after restarts
- **Retry with backoff** — Telegram and yfinance calls retry on transient failures
- **Structured logging** — timestamped log levels instead of plain print statements

## Disclaimer

This bot is for educational and informational purposes only. It sends alerts based on a simple technical pattern and does not constitute financial advice. Always do your own research before trading.
