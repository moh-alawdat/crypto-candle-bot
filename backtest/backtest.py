#!/usr/bin/env python3
"""
Phase 3: trade simulation.

Takes the Phase 2 signals (same data, same 200-day window) and simulates trades:

    Entry      : at the OPEN of the candle AFTER the signal candle.
    Take profit: 1% from entry (above for longs, below for shorts).
    Stop loss  : 1% from entry on the other side.
    Exit       : scan from the entry candle onward; exit at the first candle whose
                 high/low touches TP or SL. If a single candle touches BOTH, the
                 stop loss is assumed hit first (and such cases are counted).
    One at a time: any new signal is ignored while a trade is open.
    Fees       : 0.05% entry + 0.05% exit = 0.10% deducted from each trade's %.
    No leverage; results are in % of position.

Run with:  uv run --with pandas --with requests backtest.py
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
from signals import (build_frame, detect_signals,
                     SYMBOL, INTERVAL, WINDOW_DAYS, TOTAL, OPEN_TIME_FMT)

TP_PCT = 1.0            # take profit, percent from entry
SL_PCT = 1.0            # stop loss, percent from entry
FEE_PER_SIDE = 0.05     # percent, charged on entry and again on exit
TOTAL_FEE = FEE_PER_SIDE * 2

# Net result of a win/loss after fees (used for the break-even win rate).
WIN_NET = TP_PCT - TOTAL_FEE     # +0.90%
LOSS_NET = SL_PCT + TOTAL_FEE    # -1.10% (magnitude)

_OUTPUT_DIR = _ROOT / "output"
_OUTPUT_DIR.mkdir(exist_ok=True)
CSV_PATH = str(_OUTPUT_DIR / "backtest_trades.csv")

# Columns shared by the printed table and the CSV (no internal helper fields).
TRADE_COLS = ["signal_time", "direction", "entry_time", "entry_price",
              "exit_time", "exit_price", "result", "pct_after_fees", "candles"]


def load_rows_and_signals():
    """Fetch data once and return (rows, final signals tagged with their index)."""
    raw = fetch_klines(SYMBOL, INTERVAL, TOTAL)
    if raw and raw[-1][6] > int(time.time() * 1000):
        raw = raw[:-1]  # drop still-forming candle

    rows = build_rows(raw)
    df = build_frame(rows)

    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    open_times = [datetime.strptime(t, OPEN_TIME_FMT).replace(tzinfo=timezone.utc)
                  for t in df["open_time"]]
    window_start_idx = next(i for i, t in enumerate(open_times) if t >= cutoff)

    signals, _stats = detect_signals(df, window_start_idx)

    # Tag each signal with the index of its candle so we know where "next" is.
    time_to_idx = {t: i for i, t in enumerate(df["open_time"])}
    for s in signals:
        s["_idx"] = time_to_idx[s["open_time (UTC)"]]

    return rows, signals


def simulate(rows, signals):
    """Run the trade simulation. Returns (trades, same_candle_count, unclosed)."""
    n = len(rows)
    trades = []
    same_candle = 0
    unclosed = 0
    last_exit_idx = -1  # trades are blocked until after this candle

    for s in signals:
        sig_idx = s["_idx"]
        if sig_idx <= last_exit_idx:
            continue  # a trade was still open on/through this signal -> ignore it

        entry_idx = sig_idx + 1
        if entry_idx >= n:
            continue  # no candle after the signal to enter on

        direction = s["direction"]
        entry_price = rows[entry_idx]["open"]  # enter at next candle's OPEN

        if direction == "LONG":
            tp = entry_price * (1 + TP_PCT / 100)
            sl = entry_price * (1 - SL_PCT / 100)
        else:
            tp = entry_price * (1 - TP_PCT / 100)
            sl = entry_price * (1 + SL_PCT / 100)

        # Scan candles from the entry candle itself for the first TP/SL touch.
        exit_idx = exit_price = result = None
        both_hit = False
        for idx in range(entry_idx, n):
            hi, lo = rows[idx]["high"], rows[idx]["low"]
            if direction == "LONG":
                hit_tp, hit_sl = hi >= tp, lo <= sl
            else:
                hit_tp, hit_sl = lo <= tp, hi >= sl

            if hit_tp and hit_sl:
                both_hit = True
                same_candle += 1
                result, exit_price, exit_idx = "SL", sl, idx  # SL assumed first
                break
            if hit_sl:
                result, exit_price, exit_idx = "SL", sl, idx
                break
            if hit_tp:
                result, exit_price, exit_idx = "TP", tp, idx
                break

        if exit_idx is None:
            unclosed += 1  # trade never resolved inside the available data
            continue

        # Gross % move (longs profit when price rises, shorts when it falls).
        if direction == "LONG":
            gross = (exit_price - entry_price) / entry_price * 100
        else:
            gross = (entry_price - exit_price) / entry_price * 100
        net = gross - TOTAL_FEE

        trades.append({
            "signal_time": s["open_time (UTC)"],
            "direction": direction,
            "entry_time": rows[entry_idx]["open_time"],
            "entry_price": round(entry_price, 2),
            "exit_time": rows[exit_idx]["open_time"],
            "exit_price": round(exit_price, 2),
            "result": result,
            "pct_after_fees": round(net, 2),
            "candles": exit_idx - entry_idx + 1,
            "_entry_idx": entry_idx,
            "_exit_idx": exit_idx,
            "_both": both_hit,
        })
        last_exit_idx = exit_idx

    return trades, same_candle, unclosed


def longest_losing_streak(trades):
    """Longest run of consecutive SL results."""
    longest = run = 0
    for t in trades:
        if t["result"] == "SL":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return longest


def main():
    rows, signals = load_rows_and_signals()
    trades, same_candle, unclosed = simulate(rows, signals)

    # ---- trade table ----
    print(f"\n{SYMBOL} {INTERVAL} — trade simulation over the last {WINDOW_DAYS} "
          f"days (TP {TP_PCT}%, SL {SL_PCT}%, fees {TOTAL_FEE}%/round-trip)\n")
    if trades:
        table = pd.DataFrame(trades)[TRADE_COLS]
        print(table.to_string(index=False))
    else:
        print("No trades.")

    # ---- summary ----
    n_trades = len(trades)
    wins = sum(1 for t in trades if t["result"] == "TP")
    losses = n_trades - wins
    win_rate = wins / n_trades * 100 if n_trades else 0.0
    be_win_rate = LOSS_NET / (WIN_NET + LOSS_NET) * 100
    total_return = sum(t["pct_after_fees"] for t in trades)
    avg_per_trade = total_return / n_trades if n_trades else 0.0

    print("\n--- summary ---")
    print(f"trades:                 {n_trades}")
    print(f"wins / losses:          {wins} / {losses}")
    print(f"win rate:               {win_rate:.1f}%")
    print(f"break-even win rate:     {be_win_rate:.1f}%  (win +{WIN_NET}%, loss -{LOSS_NET}%)")
    print(f"total return:           {total_return:+.2f}%")
    print(f"avg per trade:          {avg_per_trade:+.3f}%")
    print(f"longest losing streak:  {longest_losing_streak(trades)}")
    print(f"same-candle TP/SL:      {same_candle}")
    if unclosed:
        print(f"unclosed (excluded):    {unclosed}")

    # ---- save CSV ----
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_COLS)
        writer.writeheader()
        for t in trades:
            writer.writerow({c: t[c] for c in TRADE_COLS})
    print(f"\nSaved {len(trades)} trades to {CSV_PATH}")

    # ---- one trade to hand-check on TradingView ----
    # Prefer a same-candle TP/SL case (most interesting to verify the rule); else
    # fall back to the first trade.
    pick = next((t for t in trades if t["_both"]), trades[0] if trades else None)
    if pick:
        print("\n--- hand-check this trade on TradingView ---")
        print(f"{pick['direction']} signal at {pick['signal_time']} UTC")
        print(f"entry @ open of {pick['entry_time']} = {pick['entry_price']}")
        tp_lvl = pick["entry_price"] * (1 + TP_PCT / 100) if pick["direction"] == "LONG" \
            else pick["entry_price"] * (1 - TP_PCT / 100)
        sl_lvl = pick["entry_price"] * (1 - SL_PCT / 100) if pick["direction"] == "LONG" \
            else pick["entry_price"] * (1 + SL_PCT / 100)
        print(f"TP level = {tp_lvl:.2f}   SL level = {sl_lvl:.2f}   "
              f"result = {pick['result']}  (both touched same candle: {pick['_both']})")

        candle_rows = []
        for label, idx in [("entry candle", pick["_entry_idx"]),
                           ("exit candle", pick["_exit_idx"])]:
            r = rows[idx]
            candle_rows.append({
                "which": label,
                "open_time (UTC)": r["open_time"],
                "open": r["open"], "high": r["high"],
                "low": r["low"], "close": r["close"],
            })
        print(pd.DataFrame(candle_rows).to_string(index=False))


if __name__ == "__main__":
    main()
