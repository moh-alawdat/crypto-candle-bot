#!/usr/bin/env python3
"""
Strategy variant: intrabar entry, compared against the original (next-4h-open)
entry. Both are exited with the SAME minute-by-minute simulation on cached 1m
data. The original 4h strategy rules are untouched; this is an added option.

Original entry   : at the open of the 4h candle AFTER a confirmed signal candle
                   (SAR flip + close vs EMA200 + MACD histogram sign, all read at
                   the signal candle's 4h close).
Intrabar entry   : walk the flip candle's 1m candles. Find the flip minute (first
                   minute whose wick reaches the SAR trigger level; or the open if
                   the candle opens beyond it). From the flip minute onward, enter
                   at the first minute where close-vs-EMA200 and MACD-histogram
                   (computed as if the 4h candle closed at that minute's price)
                   both agree. Entry price = the SAR level on the flip minute, else
                   that minute's close. If they never agree, no trade.

Exits (both)     : TP/SL at +/-1% from entry, checked minute by minute from the
                   entry minute. A minute touching both counts as a stop.
Fees             : 0.05% per side. One trade at a time.

Run with:
  uv run --with pandas --with requests --with numpy intrabar.py
"""

import time
from datetime import datetime, timedelta, timezone

import numpy as np

# --- make repo root + backtest/ subfolders importable (flat layout preserved) ---
import sys as _sys
import pathlib as _pathlib
_ROOT = next(p for p in _pathlib.Path(__file__).resolve().parents
             if (p / "binance_logic.py").exists())
for _d in (_ROOT, _ROOT / "backtest", _ROOT / "backtest" / "runs",
           _ROOT / "backtest" / "export", _ROOT / "backtest" / "checks"):
    if str(_d) not in _sys.path:
        _sys.path.insert(0, str(_d))

from binance_logic import fetch_klines
from indicators import ema, macd, parabolic_sar, parabolic_sar_trigger
from intrabar_data import load_minutes
from backtest_windows import TOTAL  # same 4h fetch depth -> identical indicators

SYMBOL, INTERVAL = "BTCUSDT", "4h"
FOUR_H_MS = 4 * 60 * 60 * 1000
WINDOW_DAYS, N_WINDOWS = 180, 3
START_BALANCE = 70000.0

# EMA smoothing factors for the intrabar projection.
A200, A12, A26, A9 = 2 / 201, 2 / 13, 2 / 27, 2 / 10


def _ms_to_str(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def load_frame():
    """Fetch 4h candles and attach all indicators + SAR trigger (arrays)."""
    raw = fetch_klines(SYMBOL, INTERVAL, TOTAL)
    if raw and raw[-1][6] > int(time.time() * 1000):
        raw = raw[:-1]  # drop still-forming candle

    open_ms = np.array([int(k[0]) for k in raw], dtype="int64")
    o = np.array([float(k[1]) for k in raw])
    h = np.array([float(k[2]) for k in raw])
    low = np.array([float(k[3]) for k in raw])
    c = np.array([float(k[4]) for k in raw])
    closes = c.tolist()

    macd_line, signal_line, hist = macd(closes, 12, 26, 9)
    sar, sar_dir = parabolic_sar(h.tolist(), low.tolist(), closes)
    trig = parabolic_sar_trigger(h.tolist(), low.tolist(), closes)

    return {
        "open_ms": open_ms, "open": o, "high": h, "low": low, "close": c,
        "ema200": ema(closes, 200).values,
        "ema12": ema(closes, 12).values,
        "ema26": ema(closes, 26).values,
        "signal": signal_line.values,
        "hist": hist.values,
        "sar_dir": sar_dir.values,
        "trigger": trig.values,
        "open_dt": [datetime.fromtimestamp(ms / 1000, tz=timezone.utc) for ms in open_ms],
    }


def window_bounds(frame, now):
    """Return [(label, start_idx, end_idx)] for the 3 consecutive 180-day windows."""
    out = []
    for k in range(N_WINDOWS):
        end_t = now - timedelta(days=WINDOW_DAYS * k)
        start_t = now - timedelta(days=WINDOW_DAYS * (k + 1))
        idx = [i for i, dt in enumerate(frame["open_dt"]) if start_t <= dt < end_t]
        s, e = idx[0], idx[-1] + 1
        label = (f'{frame["open_dt"][s].strftime("%b %Y")} - '
                 f'{frame["open_dt"][e - 1].strftime("%b %Y")}')
        out.append((label, s, e))
    return list(reversed(out))  # oldest first


def simulate_exit_1m(mins, entry_ms, direction, entry_price):
    """First +/-1% TP/SL touch from the entry minute onward (vectorized).

    Returns (exit_ms, exit_price, 'WIN'/'LOSS', both_hit) or None if never closed.
    A minute touching both TP and SL counts as a stop.
    """
    ot, H, L = mins["ot"], mins["high"], mins["low"]
    i0 = int(np.searchsorted(ot, entry_ms, "left"))
    if i0 >= len(ot):
        return None

    if direction == 1:
        tp, sl = entry_price * 1.01, entry_price * 0.99
        tp_hits = np.nonzero(H[i0:] >= tp)[0]
        sl_hits = np.nonzero(L[i0:] <= sl)[0]
    else:
        tp, sl = entry_price * 0.99, entry_price * 1.01
        tp_hits = np.nonzero(L[i0:] <= tp)[0]
        sl_hits = np.nonzero(H[i0:] >= sl)[0]

    ti = tp_hits[0] if len(tp_hits) else None
    si = sl_hits[0] if len(sl_hits) else None
    if ti is None and si is None:
        return None

    if si is not None and (ti is None or si <= ti):
        both = ti is not None and ti == si
        return int(ot[i0 + si]), sl, "LOSS", both
    return int(ot[i0 + ti]), tp, "WIN", False


def original_candidates(frame, start_idx, end_idx):
    """Yield (flip_i, direction, entry_ms, entry_price) for the original strategy."""
    f = frame
    n = len(f["open_ms"])
    for i in range(start_idx, end_idx):
        pd_, cd = f["sar_dir"][i - 1], f["sar_dir"][i]
        if pd_ == 0 or cd == 0 or cd == pd_:
            continue  # not a flip
        if cd == 1:
            ok = f["close"][i] > f["ema200"][i] and f["hist"][i] > 0
        else:
            ok = f["close"][i] < f["ema200"][i] and f["hist"][i] < 0
        if not ok or i + 1 >= n:
            continue
        yield i, int(cd), int(f["open_ms"][i + 1]), float(f["open"][i + 1])


def intrabar_candidate(frame, mins, i):
    """Resolve the intrabar entry for flip candle i, or None.

    Returns (flip_i, direction, entry_ms, entry_price).
    """
    f = frame
    cd, pdir = f["sar_dir"][i], f["sar_dir"][i - 1]
    if cd == 0 or pdir == 0 or cd == pdir:
        return None  # only real SAR flip candles have an intrabar entry
    new_dir = int(cd)
    level = f["trigger"][i]
    if not np.isfinite(level):
        return None

    start_ms = int(f["open_ms"][i])
    end_ms = start_ms + FOUR_H_MS
    lo = int(np.searchsorted(mins["ot"], start_ms, "left"))
    hi = int(np.searchsorted(mins["ot"], end_ms, "left"))
    if lo >= hi:
        return None

    # Prior-candle EMA/MACD state for the "as if the 4h closed now" projection.
    e200p, e12p, e26p, sigp = (f["ema200"][i - 1], f["ema12"][i - 1],
                               f["ema26"][i - 1], f["signal"][i - 1])

    flip_idx = None
    for j in range(lo, hi):
        if flip_idx is None:
            if new_dir == 1:  # downtrend -> up: price must rise to the level
                beyond_open = j == lo and mins["open"][j] >= level
                crossed = mins["high"][j] >= level
            else:             # uptrend -> down: price must fall to the level
                beyond_open = j == lo and mins["open"][j] <= level
                crossed = mins["low"][j] <= level
            if beyond_open or crossed:
                flip_idx = j
            else:
                continue

        # From the flip minute on, test the entry filter at this minute's close.
        mc = mins["close"][j]
        e200 = A200 * mc + (1 - A200) * e200p
        macd_h = (A12 * mc + (1 - A12) * e12p) - (A26 * mc + (1 - A26) * e26p)
        hist_h = macd_h - (A9 * macd_h + (1 - A9) * sigp)
        if new_dir == 1:
            ok = mc > e200 and hist_h > 0
        else:
            ok = mc < e200 and hist_h < 0
        if ok:
            is_flip_minute = j == flip_idx
            entry_price = float(level) if is_flip_minute else float(mc)
            return i, new_dir, int(mins["ot"][j]), entry_price
    return None


def run_strategy(candidates, mins):
    """Turn candidates into trades with 1m exits and one-trade-at-a-time gating."""
    trades = []
    last_exit_ms = -1
    for flip_i, direction, entry_ms, entry_price in candidates:
        if entry_ms <= last_exit_ms:
            continue  # a trade is still open -> ignore this signal
        res = simulate_exit_1m(mins, entry_ms, direction, entry_price)
        if res is None:
            continue  # never closed within available data
        exit_ms, exit_price, result, both = res
        trades.append({
            "flip_i": flip_i, "direction": "LONG" if direction == 1 else "SHORT",
            "entry_ms": entry_ms, "entry_price": entry_price,
            "exit_ms": exit_ms, "result": result, "both": both,
        })
        last_exit_ms = exit_ms
    return trades


def summarize(trades):
    n = len(trades)
    w = sum(t["result"] == "WIN" for t in trades)
    l = n - w
    bal_fee = bal_nofee = START_BALANCE
    for t in trades:
        if t["result"] == "WIN":
            bal_fee *= 1.09; bal_nofee *= 1.10
        else:
            bal_fee *= 0.89; bal_nofee *= 0.90
    return {
        "trades": n, "wins": w, "losses": l,
        "winrate": 100 * w / n if n else 0.0,
        "pct_fee": w * 0.9 - l * 1.1,
        "pct_nofee": float(w - l),
        "bal_fee": bal_fee, "bal_nofee": bal_nofee,
    }


def sharing(orig, intra):
    """Compare entry prices on trades both versions took from the same flip."""
    o_by = {t["flip_i"]: t for t in orig}
    i_by = {t["flip_i"]: t for t in intra}
    shared = sorted(set(o_by) & set(i_by))
    if not shared:
        return 0, None
    diffs = []
    better = 0
    for fi in shared:
        o, it = o_by[fi], i_by[fi]
        d = it["entry_price"] - o["entry_price"]
        diffs.append((d, d / o["entry_price"] * 100))
        # "better" = intrabar got a more favorable entry (lower for long, higher for short)
        if (o["direction"] == "LONG" and it["entry_price"] < o["entry_price"]) or \
           (o["direction"] == "SHORT" and it["entry_price"] > o["entry_price"]):
            better += 1
    abs_pct = np.mean([abs(p) for _, p in diffs])
    signed_pct = np.mean([p for _, p in diffs])
    abs_usd = np.mean([abs(d) for d, _ in diffs])
    return len(shared), {"abs_pct": abs_pct, "signed_pct": signed_pct,
                         "abs_usd": abs_usd, "better": better}


def main():
    frame = load_frame()
    now = datetime.now(timezone.utc)
    mins = load_minutes(now - timedelta(days=541), now)
    windows = window_bounds(frame, now)

    # --- self-check: intrabar projection at the 4h close must equal hist[i] ---
    max_err = 0.0
    for _, s, e in windows:
        for i in range(s, e):
            if frame["sar_dir"][i] and frame["sar_dir"][i - 1] and \
               frame["sar_dir"][i] != frame["sar_dir"][i - 1]:
                mc = frame["close"][i]
                macd_h = (A12 * mc + (1 - A12) * frame["ema12"][i - 1]) - \
                         (A26 * mc + (1 - A26) * frame["ema26"][i - 1])
                hist_h = macd_h - (A9 * macd_h + (1 - A9) * frame["signal"][i - 1])
                max_err = max(max_err, abs(hist_h - frame["hist"][i]))
    print(f"\nProjection self-check: max |hist_proj(close) - hist| over flips = "
          f"{max_err:.2e}  (should be ~0)\n")

    all_orig, all_intra = [], []
    rows = []
    share_rows = []
    for label, s, e in windows:
        orig = run_strategy(original_candidates(frame, s, e), mins)
        intra_cands = (c for i in range(s, e)
                       if (c := intrabar_candidate(frame, mins, i)) is not None)
        intra = run_strategy(intra_cands, mins)
        all_orig += orig
        all_intra += intra
        for name, tr in (("original", orig), ("intrabar", intra)):
            st = summarize(tr)
            rows.append((label, name, st))
        n_shared, sh = sharing(orig, intra)
        share_rows.append((label, n_shared, len(orig), len(intra), sh))

    # overall
    for name, tr in (("original", all_orig), ("intrabar", all_intra)):
        rows.append(("OVERALL", name, summarize(tr)))
    n_shared, sh = sharing(all_orig, all_intra)
    share_rows.append(("OVERALL", n_shared, len(all_orig), len(all_intra), sh))

    # --- results table ---
    print(f"{'window':<20}{'strat':<10}{'trd':>4}{'W':>4}{'L':>4}{'win%':>7}"
          f"{'%fee':>8}{'%nofee':>8}{'$10x fee':>12}{'$10x nofee':>13}")
    last_win = None
    for label, name, st in rows:
        if label != last_win and last_win is not None:
            print()
        last_win = label
        print(f"{label:<20}{name:<10}{st['trades']:>4}{st['wins']:>4}{st['losses']:>4}"
              f"{st['winrate']:>6.1f}%{st['pct_fee']:>+8.1f}{st['pct_nofee']:>+8.1f}"
              f"{st['bal_fee']:>12,.0f}{st['bal_nofee']:>13,.0f}")

    # --- shared-trade / entry-price comparison ---
    print(f"\n{'window':<20}{'shared':>8}{'orig#':>7}{'intra#':>8}"
          f"{'|Δentry|$':>12}{'|Δentry|%':>11}{'signedΔ%':>10}{'intra better':>14}")
    for label, n_shared, n_o, n_i, sh in share_rows:
        if sh is None:
            print(f"{label:<20}{n_shared:>8}{n_o:>7}{n_i:>8}{'-':>12}")
            continue
        better = f"{sh['better']}/{n_shared}"
        print(f"{label:<20}{n_shared:>8}{n_o:>7}{n_i:>8}"
              f"{sh['abs_usd']:>12,.2f}{sh['abs_pct']:>10.3f}%"
              f"{sh['signed_pct']:>+9.3f}%{better:>14}")


if __name__ == "__main__":
    main()
