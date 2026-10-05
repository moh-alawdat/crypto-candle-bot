#!/usr/bin/env python3
"""
Phase 2: strategy signals (detection only — no trades are simulated).

A signal is read off a *closed* BTCUSDT 4h candle from the Phase 1 indicators:

    LONG  : SAR direction flips down -> up   AND close > EMA200  AND histogram > 0
    SHORT : SAR direction flips up   -> down AND close < EMA200  AND histogram < 0

We fetch enough history to cover the last 200 days plus a warm-up of at least 500
candles (so EMA200 and the SAR are fully settled), drop the still-forming candle,
compute indicators over the whole series, and only report signals whose candle
falls inside the 200-day window.

Run with:  uv run --with pandas --with requests signals.py
"""

import time
from datetime import datetime, timedelta, timezone

import pandas as pd

# --- make repo root + backtest/ subfolders importable (flat layout preserved) ---
import sys as _sys
import pathlib as _pathlib
_ROOT = next(p for p in _pathlib.Path(__file__).resolve().parents
             if (p / "binance_logic.py").exists())
for _d in (_ROOT, _ROOT / "backtest", _ROOT / "backtest" / "runs",
           _ROOT / "backtest" / "export", _ROOT / "backtest" / "checks"):
    if str(_d) not in _sys.path:
        _sys.path.insert(0, str(_d))

from binance_logic import fetch_klines, build_rows
from indicators import ema, macd, parabolic_sar

SYMBOL = "BTCUSDT"
INTERVAL = "4h"
WINDOW_DAYS = 200
CANDLES_PER_DAY = 6          # 24h / 4h
WARMUP = 550                 # >= 500 candles of warm-up before the window
TOTAL = WINDOW_DAYS * CANDLES_PER_DAY + WARMUP

OPEN_TIME_FMT = "%Y-%m-%d %H:%M:%S"


def build_frame(rows):
    """Return a DataFrame of candles with all indicators attached (index 0..n-1)."""
    closes = [r["close"] for r in rows]
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]

    ema200 = ema(closes, 200)
    _macd_line, _signal_line, histogram = macd(closes, 12, 26, 9)
    sar, sar_dir = parabolic_sar(highs, lows, closes, 0.02, 0.02, 0.2)

    return pd.DataFrame({
        "open_time": [r["open_time"] for r in rows],
        "close": closes,
        "ema200": ema200.values,
        "histogram": histogram.values,
        "sar": sar.values,
        "sar_dir": sar_dir.values,
    })


def detect_signals(df, window_start_idx, window_end_idx=None):
    """Scan for signals; return (signals, stats).

    A flip at candle i means sar_dir changed from the previous candle (both
    directions defined). Only candles in [window_start_idx, window_end_idx) are
    counted (window_end_idx defaults to the end of the series). Filters are
    applied in order so we can report how many survive each stage.
    """
    if window_end_idx is None:
        window_end_idx = len(df)

    signals = []
    n_flips = 0
    n_pass_ema = 0
    n_pass_both = 0

    for i in range(window_start_idx, window_end_idx):
        prev_dir = df["sar_dir"].iloc[i - 1]
        cur_dir = df["sar_dir"].iloc[i]

        # Need both directions defined and an actual change to have a flip.
        if prev_dir == 0 or cur_dir == 0 or cur_dir == prev_dir:
            continue
        n_flips += 1

        close = df["close"].iloc[i]
        ema200 = df["ema200"].iloc[i]
        hist = df["histogram"].iloc[i]

        if cur_dir == 1:   # flip down -> up  => candidate LONG
            ema_ok = close > ema200
            hist_ok = hist > 0
            direction = "LONG"
        else:              # flip up -> down  => candidate SHORT
            ema_ok = close < ema200
            hist_ok = hist < 0
            direction = "SHORT"

        if ema_ok:
            n_pass_ema += 1
        if ema_ok and hist_ok:
            n_pass_both += 1
            signals.append({
                "open_time (UTC)": df["open_time"].iloc[i],
                "direction": direction,
                "close": round(close, 2),
                "EMA200": round(ema200, 2),
                "histogram": round(hist, 2),
                "SAR": round(df["sar"].iloc[i], 2),
            })

    stats = {
        "flips": n_flips,
        "pass_ema": n_pass_ema,
        "pass_both": n_pass_both,
    }
    return signals, stats


def main():
    raw = fetch_klines(SYMBOL, INTERVAL, TOTAL)

    # Drop the current, still-forming candle (its close time is in the future).
    if raw and raw[-1][6] > int(time.time() * 1000):
        raw = raw[:-1]

    rows = build_rows(raw)
    df = build_frame(rows)

    # Window = candles whose open time is within the last WINDOW_DAYS.
    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    open_times = [datetime.strptime(t, OPEN_TIME_FMT).replace(tzinfo=timezone.utc)
                  for t in df["open_time"]]
    window_idx = [i for i, t in enumerate(open_times) if t >= cutoff]
    window_start_idx = window_idx[0]
    warmup_candles = window_start_idx  # candles before the window

    signals, stats = detect_signals(df, window_start_idx)

    print(f"\n{SYMBOL} {INTERVAL} — last {WINDOW_DAYS} days "
          f"({len(window_idx)} candles in window, {warmup_candles} warm-up candles)")
    print(f"Window starts: {df['open_time'].iloc[window_start_idx]} UTC")
    print(f"Window ends:   {df['open_time'].iloc[-1]} UTC\n")

    print(f"SAR flips in window:          {stats['flips']}")
    print(f"  ...passing EMA200 filter:   {stats['pass_ema']}")
    print(f"  ...passing both filters:    {stats['pass_both']}  (final signals)\n")

    if signals:
        print(pd.DataFrame(signals).to_string(index=False))
    else:
        print("No signals in window.")


if __name__ == "__main__":
    main()
