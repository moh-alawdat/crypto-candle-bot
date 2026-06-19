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
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
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

# Tap-to-select keyboards. Tapped buttons arrive as plain text messages, so the
# existing text handlers validate them exactly like typed input. Every label
# below is a member of binance_logic.VALID_INTERVALS; users can still type any
# other valid interval (e.g. 3m, 6h, 8h) by hand.
TIMEFRAME_KEYBOARD = ReplyKeyboardMarkup(
    [["1m", "5m", "15m", "30m"],
     ["1h", "2h", "4h", "12h"],
     ["1d", "3d", "1w", "1M"]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

# Common candle counts; a custom number can still be typed.
COUNT_KEYBOARD = ReplyKeyboardMarkup(
    [["20", "50", "100", "200"]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

HELP_TEXT = (
    "I download crypto candle data from Binance and send you a CSV report "
    "with moving averages (MA7, MA21, MA50, MA200).\n\n"
    "Commands:\n"
    "/start - begin a new report (coin, timeframe, candle count)\n"
    "/myid - show your Telegram user ID\n"
    "/help - show this message\n"
    "/cancel - stop the current report\n\n"
    "Access is limited to approved users. If the bot doesn't respond to "
    "/start, send /myid and share that number with the admin to be added."
)

NOT_AUTHORIZED_TEXT = (
    "You're not authorized to use this bot yet.\n\n"
    "Send /myid to get your Telegram user ID, then share it with the admin "
    "to be added to the allow-list."
)


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
            logger.info("Message from user not on allow-list: %s", user.id if user else None)
            if update.message:
                await update.message.reply_text(NOT_AUTHORIZED_TEXT)
            return ConversationHandler.END
        return await func(update, context, *args, **kwargs)
    return wrapped


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply with the sender's Telegram user ID (open to everyone)."""
    await update.message.reply_text(f"Your Telegram user ID is: {update.effective_user.id}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show what the bot does and how to use it (open to everyone)."""
    await update.message.reply_text(HELP_TEXT)


@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Greet the user and ask the first question."""
    await update.message.reply_text(
        "Hi! I download crypto candle data from Binance and send you a CSV report.\n\n"
        "Which coin? (e.g. ETHUSDT)",
        reply_markup=ReplyKeyboardRemove(),
    )
    return COIN


@restricted
async def ask_timeframe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Store the coin, then ask for the timeframe."""
    context.user_data["symbol"] = update.message.text.strip().upper()
    await update.message.reply_text(
        "Which timeframe? (tap one below, or type e.g. 6h)",
        reply_markup=TIMEFRAME_KEYBOARD,
    )
    return TIMEFRAME


@restricted
async def ask_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Validate the timeframe, then ask how many candles."""
    timeframe = update.message.text.strip()
    if timeframe not in VALID_INTERVALS:
        await update.message.reply_text(
            "That timeframe is not valid. Please pick one below, or type one "
            "like 1m, 5m, 1h, 4h, 1d, 1w.",
            reply_markup=TIMEFRAME_KEYBOARD,
        )
        return TIMEFRAME

    context.user_data["interval"] = timeframe
    await update.message.reply_text(
        "How many candles? (tap one below, or type a number)",
        reply_markup=COUNT_KEYBOARD,
    )
    return COUNT


@restricted
async def make_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Validate the count, run the report, reply with a summary + CSV file."""
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text(
            "Please send a positive whole number.\n\n"
            "How many candles? (tap one below, or type a number)",
            reply_markup=COUNT_KEYBOARD,
        )
        return COUNT

    symbol = context.user_data["symbol"]
    interval = context.user_data["interval"]
    limit = int(text)

    await update.message.reply_text("Downloading...", reply_markup=ReplyKeyboardRemove())

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
    await update.message.reply_text(
        "Okay, cancelled. Send /start to begin again.",
        reply_markup=ReplyKeyboardRemove(),
    )
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
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(conversation)

    logger.info("Bot starting. Allowed users: %s", sorted(ALLOWED_USERS) or "(none yet)")
    application.run_polling()


if __name__ == "__main__":
    main()
