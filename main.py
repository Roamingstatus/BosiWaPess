"""
BosiWaPess - EMA Pullback Forex Alert Bot

This bot scans forex pairs for bullish EMA pullback patterns and sends
Telegram alerts. It does NOT place trades — alerts only.

Requires Python 3.10+.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv

# Load .env automatically for local development (Railway uses platform env vars)
load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Forex pairs to scan: friendly name -> Yahoo Finance ticker
FOREX_PAIRS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "USDJPY=X",
}

CANDLE_INTERVAL = "15m"
LOOKBACK_DAYS = "5d"
CANDLE_MINUTES = 15

EMA_FAST = 20
EMA_SLOW = 50

# Wait this many seconds after each 15-minute boundary so the closed candle
# is available from Yahoo Finance before we scan.
POST_CLOSE_BUFFER_SECONDS = 30

# Retry settings for Telegram and yfinance calls
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 2

# File that stores the last alerted candle per pair (survives restarts)
STATE_FILE = Path("alert_state.json")

# In-memory cache loaded from STATE_FILE at startup
last_alerted_candle: dict[str, pd.Timestamp] = {}


# ---------------------------------------------------------------------------
# Persistent alert state
# ---------------------------------------------------------------------------


def load_alert_state() -> dict[str, pd.Timestamp]:
    """Load last alerted candle timestamps from JSON file."""
    if not STATE_FILE.exists():
        logger.info("No alert state file found — starting fresh.")
        return {}

    try:
        with STATE_FILE.open("r", encoding="utf-8") as file:
            raw = json.load(file)

        state = {pair: pd.Timestamp(ts) for pair, ts in raw.items()}
        logger.info("Loaded alert state for %d pair(s) from %s.", len(state), STATE_FILE)
        return state
    except (json.JSONDecodeError, OSError, ValueError) as error:
        logger.warning("Could not load alert state (%s) — starting fresh.", error)
        return {}


def save_alert_state(state: dict[str, pd.Timestamp]) -> None:
    """Save last alerted candle timestamps to JSON file."""
    try:
        serializable = {pair: ts.isoformat() for pair, ts in state.items()}
        with STATE_FILE.open("w", encoding="utf-8") as file:
            json.dump(serializable, file, indent=2)
        logger.debug("Alert state saved to %s.", STATE_FILE)
    except OSError as error:
        logger.error("Failed to save alert state: %s", error)


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------


def call_with_backoff(operation_name: str, func, *args, **kwargs):
    """
    Call a function with exponential backoff on transient failures.

    Retries up to MAX_RETRIES times with delays of 2s, 4s, 8s, etc.
    """
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except Exception as error:
            last_error = error
            if attempt == MAX_RETRIES:
                break

            delay = BASE_BACKOFF_SECONDS**attempt
            logger.warning(
                "%s failed (attempt %d/%d): %s. Retrying in %ds...",
                operation_name,
                attempt,
                MAX_RETRIES,
                error,
                delay,
            )
            time.sleep(delay)

    logger.error("%s failed after %d attempts: %s", operation_name, MAX_RETRIES, last_error)
    raise last_error


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


def seconds_until_next_scan() -> float:
    """
    Seconds to wait until the next scan aligned to a 15-minute candle close.

    Scans run shortly after UTC boundaries (:00, :15, :30, :45) plus a small
    buffer so Yahoo Finance has the completed candle.
    """
    now = datetime.now(timezone.utc)
    seconds_into_block = (
        (now.minute % CANDLE_MINUTES) * 60
        + now.second
        + now.microsecond / 1_000_000
    )

    # If we are still inside the post-close buffer, scan immediately
    if seconds_into_block <= POST_CLOSE_BUFFER_SECONDS:
        return 0.0

    seconds_until_boundary = (CANDLE_MINUTES * 60) - seconds_into_block
    return seconds_until_boundary + POST_CLOSE_BUFFER_SECONDS


def sleep_until_next_scan() -> None:
    """Sleep until the next candle-aligned scan time."""
    delay = seconds_until_next_scan()
    if delay <= 0:
        return

    wake_at = datetime.now(timezone.utc).timestamp() + delay
    wake_str = datetime.fromtimestamp(wake_at, tz=timezone.utc).strftime("%H:%M:%S UTC")
    logger.info("Next scan in %.0fs (at %s).", delay, wake_str)
    time.sleep(delay)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


def send_telegram_alert(message: str) -> bool:
    """
    Send a message to Telegram using bot token and chat ID from environment variables.

    Returns True if the message was sent successfully, False otherwise.
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        logger.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variables.")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }

    def _post_message():
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return response

    try:
        call_with_backoff("Telegram sendMessage", _post_message)
        preview = message.replace("\n", " ")[:80]
        logger.info("Telegram alert sent: %s...", preview)
        return True
    except Exception as error:
        logger.error("Failed to send Telegram alert: %s", error)
        return False


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------


def get_candles(ticker: str) -> pd.DataFrame | None:
    """
    Download 5 days of 15-minute candle data for a forex ticker.

    Returns a DataFrame with Open, High, Low, Close columns, or None on failure.
    """
    logger.info("Downloading %s of %s candles for %s...", LOOKBACK_DAYS, CANDLE_INTERVAL, ticker)

    def _download():
        data = yf.download(
            ticker,
            period=LOOKBACK_DAYS,
            interval=CANDLE_INTERVAL,
            progress=False,
            auto_adjust=True,
        )
        if data is None or data.empty:
            raise ValueError(f"No candle data returned for {ticker}")
        return data

    try:
        data = call_with_backoff(f"yfinance download ({ticker})", _download)

        # yfinance sometimes returns MultiIndex columns — flatten them
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        data = data.dropna(subset=["Open", "High", "Low", "Close"])

        if len(data) < EMA_SLOW + 2:
            logger.warning(
                "Not enough candles for %s (need at least %d, got %d).",
                ticker,
                EMA_SLOW + 2,
                len(data),
            )
            return None

        logger.info("Loaded %d candles for %s.", len(data), ticker)
        return data

    except Exception as error:
        logger.error("Failed to download candles for %s: %s", ticker, error)
        return None


def check_ema_pullback(data: pd.DataFrame) -> tuple[bool, pd.Timestamp | None]:
    """
    Detect a bullish EMA pullback on the most recently completed candle.

    Bullish EMA pullback rules:
      1. EMA20 is above EMA50 (uptrend)
      2. Previous candle closed below EMA20 (pulled back)
      3. Current candle closed back above EMA20 (bounce)
      4. Current candle is bullish (close > open)

    We use the second-to-last row as the "current" candle because the last row
    from Yahoo Finance is often still forming (not yet closed).

    Returns (signal_found, candle_timestamp).
    """
    try:
        df = data.copy()

        df["EMA20"] = df["Close"].ewm(span=EMA_FAST, adjust=False).mean()
        df["EMA50"] = df["Close"].ewm(span=EMA_SLOW, adjust=False).mean()

        current = df.iloc[-2]
        previous = df.iloc[-3]
        candle_time = df.index[-2]

        ema20_above_ema50 = current["EMA20"] > current["EMA50"]
        prev_closed_below_ema20 = previous["Close"] < previous["EMA20"]
        curr_closed_above_ema20 = current["Close"] > current["EMA20"]
        curr_bullish = current["Close"] > current["Open"]

        signal = (
            ema20_above_ema50
            and prev_closed_below_ema20
            and curr_closed_above_ema20
            and curr_bullish
        )

        return signal, candle_time

    except Exception as error:
        logger.error("Failed EMA pullback check: %s", error)
        return False, None


def format_alert_message(pair_name: str, ticker: str, candle_time: pd.Timestamp) -> str:
    """Build a readable Telegram alert message."""
    candle_str = candle_time.strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"<b>Bullish EMA Pullback Detected</b>\n\n"
        f"Pair: <b>{pair_name}</b> ({ticker})\n"
        f"Candle: {candle_str}\n"
        f"Pattern: EMA{EMA_FAST} pullback in uptrend (EMA{EMA_FAST} &gt; EMA{EMA_SLOW})\n\n"
        f"This is an alert only — no trade was placed."
    )


def format_startup_message() -> str:
    """Build the startup confirmation message sent when the bot boots."""
    pairs = ", ".join(FOREX_PAIRS.keys())
    return (
        "<b>BosiWaPess Bot Started</b>\n\n"
        f"Scanning: {pairs}\n"
        f"Interval: {CANDLE_INTERVAL} (aligned to candle close)\n\n"
        "Alert-only bot — no trades are placed."
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run_scan() -> None:
    """Scan all forex pairs once and send alerts for new signals."""
    scan_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    logger.info("--- Scan started at %s ---", scan_time)

    for pair_name, ticker in FOREX_PAIRS.items():
        try:
            logger.info("Checking %s (%s)...", pair_name, ticker)

            data = get_candles(ticker)
            if data is None:
                continue

            signal, candle_time = check_ema_pullback(data)

            if not signal or candle_time is None:
                logger.info("No bullish EMA pullback on %s.", pair_name)
                continue

            if last_alerted_candle.get(pair_name) == candle_time:
                logger.info("Already alerted for %s candle at %s.", pair_name, candle_time)
                continue

            message = format_alert_message(pair_name, ticker, candle_time)
            if send_telegram_alert(message):
                last_alerted_candle[pair_name] = candle_time
                save_alert_state(last_alerted_candle)
                logger.info("Alert recorded for %s at %s.", pair_name, candle_time)

        except Exception as error:
            logger.error("Unexpected error checking %s: %s", pair_name, error)


def run_bot() -> None:
    """
    Main bot loop: scan forex pairs on 15-minute candle boundaries and send alerts.
    """
    global last_alerted_candle

    logger.info("=" * 60)
    logger.info("BosiWaPess EMA Pullback Alert Bot")
    logger.info("Alert-only bot — does NOT execute trades.")
    logger.info("=" * 60)

    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        logger.error("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID before running.")
        logger.error("Copy .env.example to .env and fill in your values for local dev.")
        return

    last_alerted_candle = load_alert_state()

    logger.info("Scanning pairs: %s", ", ".join(FOREX_PAIRS.keys()))
    logger.info("Interval: %s | Lookback: %s", CANDLE_INTERVAL, LOOKBACK_DAYS)
    logger.info(
        "Scans aligned to %d-minute UTC candle closes (+%ds buffer).",
        CANDLE_MINUTES,
        POST_CLOSE_BUFFER_SECONDS,
    )

    if not send_telegram_alert(format_startup_message()):
        logger.warning("Startup test message failed — check your Telegram credentials.")

    while True:
        sleep_until_next_scan()
        run_scan()


if __name__ == "__main__":
    run_bot()
