#!/usr/bin/env python3
"""
Phase 1 sanity check: fetch real BTCUSDT 4h candles and print the indicators
computed in indicators.py for the last few closed candles.

Run with:  uv run check_indicators.py
"""

import time

import pandas as pd

from binance_logic import fetch_klines, build_rows
from indicators import ema, macd, parabolic_sar

SYMBOL = "BTCUSDT"
INTERVAL = "4h"
LIMIT = 1000  # a long warm-up so EMA200 is fully settled by the last candles


def main():
    # Fetch raw klines so we can still see each candle's close time (index 6),
    # which build_rows drops. EMA200 needs ~200 candles before it to settle, so
    # pulling 1000 leaves the recent candles well warmed up.
    raw = fetch_klines(SYMBOL, INTERVAL, LIMIT)

    # Binance returns the current, still-forming candle as the last element; its
    # close time is in the future. Drop it so we only work with closed candles.
    now_ms = int(time.time() * 1000)
    if raw and raw[-1][6] > now_ms:
        raw = raw[:-1]

    rows = build_rows(raw)

    # Pull the columns the indicators need out of the dict rows.
    closes = [r["close"] for r in rows]
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]

    # Compute every indicator over the full series.
    ema200 = ema(closes, 200)
    macd_line, signal_line, histogram = macd(closes)
    sar, sar_dir = parabolic_sar(highs, lows, closes)

    # Assemble into one DataFrame aligned by position.
    df = pd.DataFrame({
        "open_time (UTC)": [r["open_time"] for r in rows],
        "close": closes,
        "EMA200": ema200.values,
        "SAR": sar.values,
        "SAR_dir": ["up" if d == 1 else "down" if d == -1 else "-" for d in sar_dir],
        "MACD": macd_line.values,
        "signal": signal_line.values,
        "histogram": histogram.values,
    })

    # Round the numeric columns to 2 decimals for a readable table.
    num_cols = ["close", "EMA200", "SAR", "MACD", "signal", "histogram"]
    df[num_cols] = df[num_cols].round(2)

    print(f"\nLast 5 closed {SYMBOL} {INTERVAL} candles:\n")
    print(df.tail(5).to_string(index=False))


if __name__ == "__main__":
    main()
