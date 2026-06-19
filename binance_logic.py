#!/usr/bin/env python3
"""
Binance candle (kline) data pulling + report logic — importable functions only.

For a given crypto symbol it pulls each candle from Binance's PUBLIC market-data
endpoint (no API key required) and builds clean rows with, per candle:

    open_time   - candle open time (UTC)
    open        - سعر الفتح
    close       - سعر الاغلاق
    high        - اعلى سعر
    low         - اقل سعر
    change      - close - open
    MA7         - 7-period simple moving average of close
    MA21        - 21-period simple moving average of close
    MA50        - 50-period simple moving average of close
    MA200       - 200-period simple moving average of close

To keep every output row's moving averages filled, ~200 extra warm-up candles
are fetched before the requested window, the MAs are computed over the full
series, then only the requested N candles are kept.

This module does no argument parsing, printing, or program exit — see
binance_report.py for the command-line front end.

Requires:  pip install requests
"""

import csv
from datetime import datetime, timezone

import requests

# Public market-data endpoint. No API key needed.
# If api.binance.com is geo-blocked for you, swap in the line below it:
BASE_URL = "https://api.binance.com/api/v3/klines"
# BASE_URL = "https://data-api.binance.vision/api/v3/klines"  # public-data mirror, fewer geo blocks

VALID_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
}

# Moving-average periods to compute over the close price.
MA_PERIODS = [7, 21, 50, 200]

# Extra candles fetched before the requested window so a 200-period MA (which
# needs the 199 candles before it) has a value on every output row.
WARMUP = 200


def fetch_klines(symbol, interval, limit):
    """Fetch `limit` candles, paginating past the 1000-per-request cap if needed."""
    symbol = symbol.upper()
    all_klines = []
    end_time = None  # None => most recent candles

    while len(all_klines) < limit:
        batch_size = min(1000, limit - len(all_klines))
        params = {"symbol": symbol, "interval": interval, "limit": batch_size}
        if end_time is not None:
            params["endTime"] = end_time

        resp = requests.get(BASE_URL, params=params, timeout=15)
        resp.raise_for_status()
        batch = resp.json()

        if not batch:
            break  # no more history available

        all_klines = batch + all_klines           # prepend older candles
        end_time = batch[0][0] - 1                  # next request ends just before oldest we have

        if len(batch) < batch_size:
            break  # reached the start of available history

    # Keep only the requested count (oldest -> newest)
    return all_klines[-limit:]


def build_rows(raw_klines):
    """Turn raw Binance kline arrays into clean dict rows with computed fields."""
    rows = []
    for k in raw_klines:
        open_time = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc)
        o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])

        rows.append({
            "open_time": open_time.strftime("%Y-%m-%d %H:%M:%S"),
            "open": o,
            "close": c,
            "high": h,
            "low": l,
            "change": round(c - o, 8),
        })
    return rows


def add_moving_averages(rows):
    """Compute simple moving averages of close over the full series, in place.

    MAn at a given candle = mean of that candle's close and the n-1 closes
    before it. Rows without enough history get an empty value.
    """
    closes = [r["close"] for r in rows]
    for period in MA_PERIODS:
        col = f"MA{period}"
        for i, r in enumerate(rows):
            if i + 1 >= period:
                r[col] = round(sum(closes[i + 1 - period:i + 1]) / period, 8)
            else:
                r[col] = ""


def compute_window_stats(rows):
    """Window-level stats over the given rows. Returns them as a dict.

    Assumes `rows` is non-empty (the requested N candles, warm-up excluded).
    """
    # مستوى التغير — net change over the window: last close vs first open.
    change_pct = (rows[-1]["close"] - rows[0]["open"]) / rows[0]["open"] * 100

    # النطاق — range of the window: highest high - lowest low across all N candles.
    highest_high = max(r["high"] for r in rows)
    lowest_low = min(r["low"] for r in rows)
    range_abs = highest_high - lowest_low
    range_pct = range_abs / lowest_low * 100

    return {
        "change_pct": change_pct,
        "range_abs": range_abs,
        "range_pct": range_pct,
        "highest_high": highest_high,
        "lowest_low": lowest_low,
    }


def get_report(symbol, interval, limit):
    """Run the whole pipeline and return the report as data (no printing/writing).

    Fetches `limit` + WARMUP candles, builds rows, adds moving averages over the
    full series, trims to the last `limit` rows, then computes window stats.
    """
    total = limit + WARMUP
    raw = fetch_klines(symbol, interval, total)

    rows = build_rows(raw)
    add_moving_averages(rows)

    # Keep only the N candles asked for (MAs computed over the full series).
    rows = rows[-limit:]

    stats = compute_window_stats(rows) if rows else None

    return {
        "rows": rows,
        "stats": stats,
        "symbol": symbol,
        "interval": interval,
        "count": len(rows),
    }


def write_csv(rows, path):
    """Write rows to CSV and return the path written to."""
    fieldnames = ["open_time", "open", "close", "high", "low", "change",
                  "MA7", "MA21", "MA50", "MA200"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path
