#!/usr/bin/env python3
"""
Telegram bot front end for the Binance candle report.

It talks to the user one question at a time (coin, timeframe, candle count),
then downloads the data via binance_logic.get_report, replies with a short
summary, and attaches a CSV file.

Setup:
    pip install python-telegram-bot python-dotenv
    Create a .env file next to this script with:
        TELEGRAM_TOKEN=123456:your-bot-token
        ALLOWED_USERS=11111111,22222222

Run:
    python bot.py
"""

import asyncio
import functools
import logging
import os
import tempfile

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from binance_logic import VALID_INTERVALS, get_report, write_csv

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Conversation steps.
COIN, TIMEFRAME, COUNT = range(3)


def parse_allowed(raw):
    """Turn a comma-separated string of Telegram user IDs into a set of ints."""
    ids = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if part:
            try:
                ids.add(int(part))
            except ValueError:
                logger.warning("Ignoring invalid user id in ALLOWED_USERS: %r", part)
    return ids


load_dotenv()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ALLOWED_USERS = parse_allowed(os.environ.get("ALLOWED_USERS"))


def restricted(func):
    """Ignore any message from a user who is not on the allow-list."""
    @functools.wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if user is None or user.id not in ALLOWED_USERS:
            logger.info("Ignoring message from user not on allow-list: %s", user.id if user else None)
            return ConversationHandler.END
        return await func(update, context, *args, **kwargs)
    return wrapped


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply with the sender's Telegram user ID (open to everyone)."""
    await update.message.reply_text(f"Your Telegram user ID is: {update.effective_user.id}")


@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Greet the user and ask the first question."""
    await update.message.reply_text(
        "Hi! I download crypto candle data from Binance and send you a CSV report.\n\n"
        "Which coin? (e.g. ETHUSDT)"
    )
    return COIN


@restricted
async def ask_timeframe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Store the coin, then ask for the timeframe."""
    context.user_data["symbol"] = update.message.text.strip().upper()
    await update.message.reply_text("Which timeframe? (e.g. 1h, 1d)")
    return TIMEFRAME


@restricted
async def ask_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Validate the timeframe, then ask how many candles."""
    timeframe = update.message.text.strip()
    if timeframe not in VALID_INTERVALS:
        await update.message.reply_text(
            "That timeframe is not valid. Please pick one like 1m, 5m, 1h, 4h, 1d, 1w.\n\n"
            "Which timeframe? (e.g. 1h, 1d)"
        )
        return TIMEFRAME

    context.user_data["interval"] = timeframe
    await update.message.reply_text("How many candles? (e.g. 20)")
    return COUNT


@restricted
async def make_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Validate the count, run the report, reply with a summary + CSV file."""
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text(
            "Please send a positive whole number.\n\n"
            "How many candles? (e.g. 20)"
        )
        return COUNT

    symbol = context.user_data["symbol"]
    interval = context.user_data["interval"]
    limit = int(text)

    await update.message.reply_text("Downloading...")

    try:
        # get_report does blocking network I/O — run it off the event loop.
        report = await asyncio.to_thread(get_report, symbol, interval, limit)
    except Exception:
        logger.exception("get_report failed for %s %s %s", symbol, interval, limit)
        await update.message.reply_text("Something went wrong, please try again.")
        return ConversationHandler.END

    rows = report["rows"]
    if not rows:
        await update.message.reply_text("No data returned, please try again.")
        return ConversationHandler.END

    stats = report["stats"]
    summary = (
        f"Coin: {report['symbol'].upper()}\n"
        f"Timeframe: {report['interval']}\n"
        f"Candles: {report['count']}\n"
        f"Change: {stats['change_pct']:.2f}%\n"
        f"Range: {stats['range_pct']:.2f}%"
    )

    # Unique temp file per request so two users can never overwrite each other.
    fd, path = tempfile.mkstemp(
        suffix=".csv", prefix=f"{report['symbol'].upper()}_{report['interval']}_"
    )
    os.close(fd)
    try:
        write_csv(rows, path)
        await update.message.reply_text(summary)
        with open(path, "rb") as f:
            await update.message.reply_document(
                document=f, filename=f"{report['symbol'].upper()}_{report['interval']}.csv"
            )
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    return ConversationHandler.END


@restricted
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Let the user stop the conversation."""
    await update.message.reply_text("Okay, cancelled. Send /start to begin again.")
    return ConversationHandler.END


def main():
    if not TELEGRAM_TOKEN:
        raise SystemExit("TELEGRAM_TOKEN is not set. Add it to your environment or a .env file.")

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    conversation = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            COIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_timeframe)],
            TIMEFRAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_count)],
            COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, make_report)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("myid", myid))
    application.add_handler(conversation)

    logger.info("Bot starting. Allowed users: %s", sorted(ALLOWED_USERS) or "(none yet)")
    application.run_polling()


if __name__ == "__main__":
    main()
