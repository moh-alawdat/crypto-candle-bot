#!/usr/bin/env python3
"""
One-off extraction (temporary; do not commit): list specific losing trades.

  * Period A (oldest 200-day window): the trades in its 7-loss streak.
  * Period C (most recent 200-day window): the 2 most recent losing trades.

Reuses the unchanged strategy from signals.py / backtest.py / backtest_periods.py.
Run with:  uv run --with pandas --with requests find_losses.py
"""

import csv
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
from signals import build_frame, detect_signals, SYMBOL, INTERVAL, OPEN_TIME_FMT
from backtest import simulate, TP_PCT, SL_PCT
from backtest_periods import window_indices, PERIOD_DAYS, N_PERIODS, TOTAL

_OUTPUT_DIR = _ROOT / "output"
_OUTPUT_DIR.mkdir(exist_ok=True)
CSV_PATH = str(_OUTPUT_DIR / "losses_to_show.csv")
OUT_COLS = ["group", "direction", "signal_time", "entry_time", "entry_price",
            "tp_price", "sl_price", "exit_time"]


def period_trades(rows, df, open_times, time_to_idx, now, k):
    """Run the backtest on the k-th period (k=0 newest) and return its trades."""
    end_time = now - timedelta(days=PERIOD_DAYS * k)
    start_time = now - timedelta(days=PERIOD_DAYS * (k + 1))
    start_idx, end_idx = window_indices(open_times, start_time, end_time)
    signals, _ = detect_signals(df, start_idx, end_idx)
    for s in signals:
        s["_idx"] = time_to_idx[s["open_time (UTC)"]]
    trades, _, _ = simulate(rows, signals)
    return trades


def longest_losing_run(trades):
    """Return the trades making up the longest run of consecutive SL results."""
    best, cur = [], []
    for t in trades:
        if t["result"] == "SL":
            cur.append(t)
            if len(cur) > len(best):
                best = cur[:]
        else:
            cur = []
    return best


def to_row(group, t):
    entry = t["entry_price"]
    if t["direction"] == "LONG":
        tp = entry * (1 + TP_PCT / 100)
        sl = entry * (1 - SL_PCT / 100)
    else:
        tp = entry * (1 - TP_PCT / 100)
        sl = entry * (1 + SL_PCT / 100)
    return {
        "group": group,
        "direction": t["direction"],
        "signal_time": t["signal_time"],
        "entry_time": t["entry_time"],
        "entry_price": round(entry, 2),
        "tp_price": round(tp, 2),
        "sl_price": round(sl, 2),
        "exit_time": t["exit_time"],
    }


def main():
    raw = fetch_klines(SYMBOL, INTERVAL, TOTAL)
    if raw and raw[-1][6] > int(time.time() * 1000):
        raw = raw[:-1]
    rows = build_rows(raw)
    df = build_frame(rows)
    open_times = [datetime.strptime(t, OPEN_TIME_FMT).replace(tzinfo=timezone.utc)
                  for t in df["open_time"]]
    time_to_idx = {t: i for i, t in enumerate(df["open_time"])}
    now = datetime.now(timezone.utc)

    # Period A = oldest window (k = N_PERIODS - 1); Period C = newest (k = 0).
    trades_a = period_trades(rows, df, open_times, time_to_idx, now, N_PERIODS - 1)
    trades_c = period_trades(rows, df, open_times, time_to_idx, now, 0)

    streak_a = longest_losing_run(trades_a)
    recent_losses_c = [t for t in trades_c if t["result"] == "SL"][-2:]

    out = [to_row("A streak (7 losses)", t) for t in streak_a]
    out += [to_row("C recent loss", t) for t in recent_losses_c]

    print(f"\nPeriod A losing streak: {len(streak_a)} trades   |   "
          f"Period C most-recent losses: {len(recent_losses_c)} trades\n")
    print(pd.DataFrame(out)[OUT_COLS].to_string(index=False))

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_COLS)
        writer.writeheader()
        writer.writerows(out)
    print(f"\nSaved {len(out)} rows to {CSV_PATH}")


if __name__ == "__main__":
    main()
