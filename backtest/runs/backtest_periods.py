#!/usr/bin/env python3
"""
Run the Phase 3 backtest (unchanged rules) over three consecutive 200-day
periods and report per-period and combined stats:

    period 0: the last 200 days
    period 1: the 200 days before that
    period 2: the 200 days before that

Each period gets at least 500 warm-up candles before its window so EMA200 and
the SAR are settled. Strategy, entry/exit, fees and signal logic are reused
verbatim from signals.py / backtest.py.

Run with:  uv run --with pandas --with requests backtest_periods.py
"""

import time
from datetime import datetime, timedelta, timezone

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
from signals import build_frame, detect_signals, SYMBOL, INTERVAL, OPEN_TIME_FMT
from backtest import simulate, longest_losing_streak

PERIOD_DAYS = 200
N_PERIODS = 3
WARMUP = 550                 # >= 500 warm-up candles before the OLDEST window
CANDLES_PER_DAY = 6          # 24h / 4h
# Enough history: all three windows (600 days) plus warm-up before the oldest.
TOTAL = N_PERIODS * PERIOD_DAYS * CANDLES_PER_DAY + WARMUP


def window_indices(open_times, start_time, end_time):
    """Indices of candles with start_time <= open_time < end_time."""
    idx = [i for i, t in enumerate(open_times) if start_time <= t < end_time]
    return idx[0], idx[-1] + 1  # (start_idx, end_idx_exclusive)


def stats_for(trades):
    n = len(trades)
    wins = sum(1 for t in trades if t["result"] == "TP")
    losses = n - wins
    total = sum(t["pct_after_fees"] for t in trades)
    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / n * 100) if n else 0.0,
        "total": total,
        "avg": (total / n) if n else 0.0,
        "streak": longest_losing_streak(trades),
    }


def print_stats(label, s):
    print(f"{label}")
    print(f"  trades:                {s['trades']}")
    print(f"  wins / losses:         {s['wins']} / {s['losses']}")
    print(f"  win rate:              {s['win_rate']:.1f}%")
    print(f"  total return:          {s['total']:+.2f}%")
    print(f"  avg per trade:         {s['avg']:+.3f}%")
    print(f"  longest losing streak: {s['streak']}\n")


def main():
    raw = fetch_klines(SYMBOL, INTERVAL, TOTAL)
    if raw and raw[-1][6] > int(time.time() * 1000):
        raw = raw[:-1]  # drop still-forming candle

    rows = build_rows(raw)
    df = build_frame(rows)
    open_times = [datetime.strptime(t, OPEN_TIME_FMT).replace(tzinfo=timezone.utc)
                  for t in df["open_time"]]
    time_to_idx = {t: i for i, t in enumerate(df["open_time"])}

    now = datetime.now(timezone.utc)

    print(f"\n{SYMBOL} {INTERVAL} — backtest over {N_PERIODS} × {PERIOD_DAYS}-day "
          f"periods (same rules: TP 1%, SL 1%, fees 0.1%/round-trip)\n")

    all_trades_chrono = []  # oldest period first, for a meaningful combined streak

    for k in range(N_PERIODS):
        end_time = now - timedelta(days=PERIOD_DAYS * k)
        start_time = now - timedelta(days=PERIOD_DAYS * (k + 1))
        start_idx, end_idx = window_indices(open_times, start_time, end_time)
        warmup = start_idx  # candles available before this window

        signals, _ = detect_signals(df, start_idx, end_idx)
        for s in signals:
            s["_idx"] = time_to_idx[s["open_time (UTC)"]]
        trades, _same, _unclosed = simulate(rows, signals)

        label = (f"Period {k}: {df['open_time'].iloc[start_idx]} -> "
                 f"{df['open_time'].iloc[end_idx - 1]} UTC  "
                 f"({warmup} warm-up candles)")
        print_stats(label, stats_for(trades))

        all_trades_chrono = trades + all_trades_chrono  # prepend (older first)

    print_stats("COMBINED (all three periods)", stats_for(all_trades_chrono))


if __name__ == "__main__":
    main()
