#!/usr/bin/env python3
"""
Run the Phase 3 backtest (unchanged rules) over four consecutive 180-day windows
(720 days total) and report per-window and combined stats, plus the result of
"Gemini's plan": start $70,000, 10x leverage, full compounding.

Leverage model: each trade multiplies the account by (1 + 10 * pct_after_fees/100),
i.e. +9% on a win (+0.9% * 10) and -11% on a loss (-1.1% * 10). Compounding is
CONTINUOUS across windows (oldest -> newest): a window's starting balance is the
previous window's ending balance. The whole plan starts at $70,000.

Each window has >= 500 warm-up candles before it. Strategy, entry/exit, fees and
signal logic are reused verbatim from signals.py / backtest.py.

Run with:  uv run --with pandas --with requests backtest_windows.py
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
from signals import build_frame, detect_signals, SYMBOL, INTERVAL, OPEN_TIME_FMT
from backtest import simulate, longest_losing_streak
from backtest_periods import window_indices

WINDOW_DAYS = 180
N_WINDOWS = 4
WARMUP = 550                 # >= 500 warm-up candles before the OLDEST window
CANDLES_PER_DAY = 6          # 24h / 4h
TOTAL = N_WINDOWS * WINDOW_DAYS * CANDLES_PER_DAY + WARMUP

LEVERAGE = 10
START_BALANCE = 70000.0


def stats_for(trades):
    n = len(trades)
    wins = sum(1 for t in trades if t["result"] == "TP")
    total = sum(t["pct_after_fees"] for t in trades)
    return {
        "trades": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate": (wins / n * 100) if n else 0.0,
        "total": total,
        "avg": (total / n) if n else 0.0,
        "streak": longest_losing_streak(trades),
    }


def apply_leverage(trades, start_balance):
    """Compound `start_balance` through the trades; return the ending balance."""
    bal = start_balance
    for t in trades:
        bal *= (1 + LEVERAGE * t["pct_after_fees"] / 100)
    return bal


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

    # Build windows oldest -> newest so the leveraged balance compounds forward.
    # k = N_WINDOWS-1 is the oldest; k = 0 is the last 180 days.
    window_trades = []   # list of (label, trades) oldest first
    for k in range(N_WINDOWS - 1, -1, -1):
        end_time = now - timedelta(days=WINDOW_DAYS * k)
        start_time = now - timedelta(days=WINDOW_DAYS * (k + 1))
        start_idx, end_idx = window_indices(open_times, start_time, end_time)

        signals, _ = detect_signals(df, start_idx, end_idx)
        for s in signals:
            s["_idx"] = time_to_idx[s["open_time (UTC)"]]
        trades, _, _ = simulate(rows, signals)

        tag = "last 180d" if k == 0 else f"{k+1}th-back"
        label = (f"{df['open_time'].iloc[start_idx][:10]} -> "
                 f"{df['open_time'].iloc[end_idx - 1][:10]}")
        window_trades.append((label, tag, trades, start_idx))

    # Assemble the per-window report, compounding the balance continuously.
    bal = START_BALANCE
    all_trades = []
    table = []
    for label, tag, trades, start_idx in window_trades:
        s = stats_for(trades)
        lev_start = bal
        lev_end = apply_leverage(trades, bal)
        bal = lev_end
        all_trades += trades
        table.append({
            "window": tag,
            "dates (UTC)": label,
            "warmup": start_idx,
            "trades": s["trades"],
            "W": s["wins"],
            "L": s["losses"],
            "win%": round(s["win_rate"], 1),
            "total%": round(s["total"], 2),
            "avg%": round(s["avg"], 3),
            "streak": s["streak"],
            "lev_start$": round(lev_start, 2),
            "lev_end$": round(lev_end, 2),
        })

    cs = stats_for(all_trades)
    combined = {
        "window": "COMBINED",
        "dates (UTC)": f"{window_trades[0][0][:10]} -> {window_trades[-1][0][-10:]}",
        "warmup": "",
        "trades": cs["trades"],
        "W": cs["wins"],
        "L": cs["losses"],
        "win%": round(cs["win_rate"], 1),
        "total%": round(cs["total"], 2),
        "avg%": round(cs["avg"], 3),
        "streak": cs["streak"],
        "lev_start$": round(START_BALANCE, 2),
        "lev_end$": round(bal, 2),
    }

    print(f"\n{SYMBOL} {INTERVAL} — {N_WINDOWS} x {WINDOW_DAYS}-day windows "
          f"(TP 1%, SL 1%, fees 0.1%/round-trip; leverage {LEVERAGE}x, "
          f"compounding continuous from ${START_BALANCE:,.0f})\n")
    df_out = pd.DataFrame(table + [combined])
    print(df_out.to_string(index=False))


if __name__ == "__main__":
    main()
