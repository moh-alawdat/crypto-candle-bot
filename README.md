# Crypto Candle Bot

A Telegram bot that downloads crypto candle (kline) data from Binance and sends
you a CSV report.

## What it does

Send `/start` and the bot asks one question at a time:

1. **Which coin?** (e.g. `ETHUSDT`) — typed in.
2. **Which timeframe?** — tap a button (`1m`, `5m`, `1h`, `4h`, `1d`, `1w`, …)
   or type any valid interval (e.g. `6h`).
3. **How many candles?** — tap a preset (`20`, `50`, `100`, `200`) or type a number.

It then downloads the data from Binance's public market-data endpoint and replies
with a short summary (coin, timeframe, number of candles, change %, range %) plus
a CSV file. Each row has the candle's **open, close, high, low**, the **change**
(close − open), and the moving averages **MA7, MA21, MA50, MA200**.

There is also a command-line version (`python binance_report.py`) if you prefer a
terminal report instead of the bot.

## Commands

- `/start` — begin a new report.
- `/help` — show what the bot does and the available commands.
- `/myid` — show your Telegram user ID.
- `/cancel` — stop the current report.

## Access

Only Telegram user IDs listed in `ALLOWED_USERS` can run reports. A user who is
not on the list gets a short message telling them to send `/myid` and share that
ID with the admin. `/help` and `/myid` are open to everyone so new users can
onboard themselves.

## Install

```bash
pip install -r requirements.txt
```

## Set up the .env

Copy the example file and fill in the two values:

```bash
cp .env.example .env
```

Then edit `.env`:

```
TELEGRAM_TOKEN=123456:your-bot-token
ALLOWED_USERS=11111111,22222222
```

- `TELEGRAM_TOKEN` — your bot token (see below).
- `ALLOWED_USERS` — a comma-separated list of Telegram user IDs allowed to use
  the bot. Tip: leave it empty at first, run the bot, send it `/myid` to get your
  ID, add it here, then restart.

The `.env` file holds secrets and is ignored by git.

## Get a token from @BotFather

1. Open Telegram and start a chat with [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts (choose a name and a username).
3. BotFather replies with a token like `123456:ABC-DEF...`.
4. Paste it into `.env` as `TELEGRAM_TOKEN`.

## Run

```bash
python bot.py
```

Then open your bot in Telegram and send `/start`.
