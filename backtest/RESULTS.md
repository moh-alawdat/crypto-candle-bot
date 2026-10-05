# Backtest results — BTCUSDT SAR / EMA200 / MACD strategy

This document is self-contained: it explains the strategy, how it was tested and
verified, every result we produced, the conclusions, and what is still worth
testing. Numbers below were produced on **2026-10-05**; the test windows are
anchored to "now", so re-running later rolls the windows forward and the exact
figures will drift slightly, but the method and conclusions stand.

All code lives under `backtest/`. Reproduction commands are at the end.

---

## 1. The strategy (plain English)

We trade **BTCUSDT on the 4-hour timeframe**. Three indicators drive it:

- **Parabolic SAR** (0.02 / 0.02 / 0.2) — a trailing dot that sits below price in
  an uptrend and above price in a downtrend. When price crosses it, the trend
  "flips".
- **EMA200** — the 200-period exponential moving average of the close (the
  long-term trend filter).
- **MACD histogram** (12 / 26 / 9) — momentum; positive means short-term momentum
  is above the longer average.

A signal fires on a **closed 4h candle** when all three agree:

- **Long**: SAR flips **down → up**, `close > EMA200`, histogram `> 0`.
- **Short**: SAR flips **up → down**, `close < EMA200`, histogram `< 0`.

**Entry** is at the **open of the next 4h candle** (never the signal candle's own
close — that would be lookahead). **Take-profit** and **stop-loss** are both
**±1%** from entry. Only **one trade at a time**: new signals are ignored while a
trade is open.

A separate **intrabar variant** (section 5d) instead enters *inside* the flip
candle, at the moment the entry conditions first line up minute-by-minute.

---

## 2. How we tested

**Data.** BTCUSDT klines from Binance's public market-data endpoint (no API key).
- 4h candles for the indicators, fetched with a long warm-up (≥500 candles, in
  practice 550–2950) before each test window so EMA200 and the SAR are fully
  settled before any trade is counted.
- 1-minute candles for the intrabar variant and for minute-accurate exits, cached
  under `data/` (git-ignored). `intrabar_data.py` pulls complete months from
  Binance's bulk dumps (data.binance.vision) and tops up the current month from
  the REST API. ~779,000 candles over 540 days, **zero minute gaps**.

**Warm-up.** Every window has at least 500 candles of history before it so the
indicators are stable from the first signal.

**Entry.** Original strategy: the open of the candle *after* the signal candle.
Intrabar variant: inside the flip candle (see 5d).

**Exits — two simulators.**
- **4h exit** (used by `backtest.py`, `runs/`, and the Excel exports): walk the 4h
  candles from entry; exit on the first candle whose high/low touches TP or SL.
- **1-minute exit** (used by `intrabar.py` for *both* strategies): walk the 1m
  candles from the entry minute; exit on the first minute that touches TP or SL.

**Same-candle rule.** If a single candle (4h) or minute (1m) touches **both** TP
and SL, we assume the **stop-loss hit first** (conservative) and count how often
this happens. Minute-level exits resolve most of these ambiguities that the 4h
simulator had to guess on.

**Fees.** 0.05% per side = 0.10% round-trip, deducted from each trade:
- Unleveraged: a win nets **+0.9%**, a loss nets **−1.1%**.
- With this asymmetry the **break-even win rate is 55%** (`1.1 / (0.9 + 1.1)`).

**Leverage model ("Gemini's plan").** $70,000 start, **10x**, full compounding.
Each trade multiplies the balance by `1 + 10 × (net % / 100)`:
- With fees: win **×1.09**, loss **×0.89**.
- Without fees: win **×1.10**, loss **×0.90**.
No single trade liquidates (max −11%), but compounded losing streaks are brutal.

---

## 3. Indicator verification (against TradingView and the `ta` library)

Nothing was trusted on reasoning — every indicator was checked against external
numbers.

- **MACD (12/26/9)** — matches the `ta` library **exactly** (max |Δ| = 0.000000
  for the MACD line, signal, and histogram over the last 500 candles).
- **EMA200** — matches `ta` to within **0.03%** (max |Δ| ≈ $21 on an ~$80,000
  price; 0 candles differ by more than 0.1%). We seed the EMA with the simple
  average of the first 200 closes (TradingView's convention); the tiny residual
  vs `ta` is only that seeding choice and decays to nothing over the warm-up.
- **Parabolic SAR** — matches TradingView **to the cent**.

### The SAR bug we found and fixed

Our first SAR was a "textbook Wilder" implementation. It disagreed with
TradingView — e.g. on 2026-09-25 it flipped to an uptrend at 08:00 because our
SAR there (85,199.62) sat too low, while TradingView stayed in a downtrend
(85,717.97 / 85,547.39 / 85,387.04 at 04:00 / 08:00 / 12:00). The `ta` library
agreed with TradingView, not us.

The fix was to port TradingView's documented `pine_sar` reference **line for
line**. The bugs in the naive version were:

1. It initialised the trend from the **highs** of the first two bars; TradingView
   initialises from **close vs close[1]**.
2. It **clamped** the SAR to the prior two bars' range **before** testing for a
   reversal; TradingView tests the reversal on the **raw projected** SAR first,
   then clamps.
3. On a reversal it set the new SAR to just the prior extreme point;
   TradingView uses `max(high, EP)` (mirror for the other direction).
4. It extended the extreme point / acceleration factor in the wrong order
   relative to the clamp.

After porting `pine_sar` exactly, our SAR reproduces TradingView's chart values
to the cent and agrees with `ta` on trend direction on **500/500** candles (mean
|Δ| ≈ $0.93). See `indicators.py` — each block is annotated with the Pine line it
mirrors.

### SAR trigger level

`parabolic_sar_trigger()` exposes, for each bar, the SAR level **before any
reversal** — the level price must cross to flip, known at the bar's open. It is
the raw projected value used in the reversal test (before the clamp). Verified on
6 flips: the trigger always lies between the previous bar's dot and the flip
candle's wick, and price always crossed it within the flip candle. This feeds the
intrabar variant.

---

## 4. Results — unleveraged, 4h exits

### 4a. Three consecutive 200-day periods (`runs/backtest_periods.py`)

| Period | Trades | W / L | Win rate | Total % | Avg/trade | Longest losing streak |
|---|---|---|---|---|---|---|
| Last 200d       | 25 | 15 / 10 | 60.0% | **+2.50%** | +0.100% | 3 |
| Prior 200d      | 20 | 11 / 9  | 55.0% | **−0.00%** | −0.000% | 3 |
| Prior-prior 200d| 30 | 12 / 18 | 40.0% | **−9.00%** | −0.300% | 7 |
| **Combined**    | 75 | 38 / 37 | 50.7% | **−6.50%** | −0.087% | 7 |

Only the most recent period clears the 55% break-even bar. Over 600 days the
strategy loses 6.5%.

### 4b. Four consecutive 180-day windows + 10x (`runs/backtest_windows.py`)

10x here compounds **continuously** across windows from a single $70,000 start
(with fees).

| Window | Dates | Trades | W / L | Win% | Total % | Streak | 10x balance (with fees) |
|---|---|---|---|---|---|---|---|
| 4th-back  | 2024-10 → 2025-04 | 22 | 12 / 10 | 54.5% | −0.2%  | 3 | 70,000 → 61,393 |
| 3rd-back  | 2025-04 → 2025-10 | 26 | 9 / 17  | 34.6% | −10.6% | 7 | 61,393 → 18,390 |
| 2nd-back  | 2025-10 → 2026-04 | 18 | 11 / 7  | 61.1% | +2.2%  | 2 | 18,390 → 20,990 |
| Last 180d | 2026-04 → 2026-10 | 23 | 14 / 9  | 60.9% | +2.7%  | 3 | 20,990 → 24,575 |
| **Combined** | 720 days | 89 | 46 / 43 | 51.7% | **−5.9%** | 7 | **70,000 → 24,575** |

Unleveraged the strategy is roughly flat (−5.9% over 720 days). At 10x with fees
the account loses **65%** — volatility drag plus the 3rd-back drawdown
(a 7-loss streak takes $61k → $18k and it never recovers).

---

## 5. Results — the three 180-day windows, leverage detail

These are the three most-recent 180-day windows (the Excel deliverables). Here the
10x balance **resets to $70,000 at the start of each window** (per-window view).

### 5a. With fees (`export/export_trades.py` → `output/mutaz_backtest.xlsx`)

| Window | Trades | W / L | Win% | 10x growth | Ending (from $70k) |
|---|---|---|---|---|---|
| Apr 2025 – Oct 2025 | 26 | 9 / 17  | 34.6% | ×0.29955 | **$20,968** |
| Oct 2025 – Apr 2026 | 18 | 11 / 7  | 61.1% | ×1.14136 | **$79,895** |
| Apr 2026 – Oct 2026 | 23 | 14 / 9  | 60.9% | ×1.17080 | **$81,956** |

### 5b. Without fees (`export/export_trades_simple.py` → `output/mutaz_backtest_simple.xlsx`)

| Window | W / L | $70,000 becomes | Result |
|---|---|---|---|
| Apr 2025 – Oct 2025 | 9 / 17 | **$27,527** | −60.7% |
| Oct 2025 – Apr 2026 | 11 / 7 | **$95,525** | +36.5% |
| Apr 2026 – Oct 2026 | 14 / 9 | **$102,986** | +47.1% |

### 5c. Gemini's claim vs. reality

Gemini claimed **$70,000 → $472,500 in 6 months (+575%)**.

- Best 180-day window, **no fees**, 10x: **$102,986 (+47%)** — $369,514 short.
- Best 180-day window, **with fees**, 10x: **$81,956 (+17%)**.
- Worst window, no fees: **$27,527 (−61%)**.

The claim is not reproducible on any window. Even with zero fees the best six
months is ~+47%, not +575%, and a bad six months loses more than half the account.

### 5d. Intrabar entry vs. original — both with 1-minute exits (`intrabar.py`)

The intrabar variant walks the flip candle's 1m candles: it finds the flip minute
(first minute whose wick reaches the SAR trigger level, or the open if the candle
opens beyond it), then from that minute enters at the first minute where
`close vs EMA200` and the MACD histogram sign agree — where EMA200 and MACD are
projected *as if the 4h candle closed at that minute's price* (this projection
reproduces the real 4h histogram exactly at the close: self-check max error
**0.00**). Entry price is the SAR level on the flip minute, else the minute close.
Both strategies are then exited with the **same 1-minute** TP/SL simulation.

| Window | Strategy | Trd | W / L | Win% | % (fees) | % (no fees) | $10x fees | $10x no-fees |
|---|---|---|---|---|---|---|---|---|
| Apr 2025–Oct 2025 | original | 26 | 9 / 17  | 34.6% | −10.6 | −8.0 | 20,968 | 27,527 |
| Apr 2025–Oct 2025 | intrabar | 31 | 12 / 19 | 38.7% | −10.1 | −7.0 | 21,509 | 29,677 |
| Oct 2025–Apr 2026 | original | 18 | 12 / 6  | 66.7% | +4.2  | +6.0 | 97,849 | 116,752 |
| Oct 2025–Apr 2026 | intrabar | 28 | 16 / 12 | 57.1% | +1.2  | +4.0 | 68,644 | 90,843 |
| Apr 2026–Oct 2026 | original | 23 | 15 / 8  | 65.2% | +4.7  | +7.0 | 100,373 | 125,872 |
| Apr 2026–Oct 2026 | intrabar | 31 | 19 / 12 | 61.3% | +3.9  | +7.0 | 88,896 | 120,912 |
| **OVERALL** | original | 67 | 36 / 31 | 53.7% | −1.7 | +5.0 | 42,028 | 82,557 |
| **OVERALL** | intrabar | 90 | 47 / 43 | 52.2% | −5.0 | +4.0 | 26,786 | 66,525 |

Shared trades (same flip candle) and entry-price differences:

| Window | Shared | orig / intra count | mean \|Δentry\| | signed Δ% | intrabar got better entry |
|---|---|---|---|---|---|
| Apr 2025–Oct 2025 | 26 | 26 / 31 | $392.96 (0.374%) | +0.024% | 15/26 |
| Oct 2025–Apr 2026 | 18 | 18 / 28 | $525.38 (0.665%) | +0.177% | 13/18 |
| Apr 2026–Oct 2026 | 23 | 23 / 31 | $600.18 (0.852%) | −0.542% | 15/23 |
| **OVERALL** | 67 | 67 / 90 | $499.67 (0.616%) | −0.129% | 43/67 |

**1-minute exits vs. 4h exits.** Trade *counts* are identical to the 4h runs, but
a few results flip from loss to win because the minute sim resolves same-candle
TP/SL ambiguity that the 4h sim had to call a stop — e.g. the original strategy's
Oct 2025–Apr 2026 window goes 11W/7L (4h) → **12W/6L** (1m). This is a genuine
refinement, not a rule change.

---

## 6. Conclusions

1. **No durable edge.** Unleveraged the strategy is flat-to-negative across every
   multi-period test (−6.5% over 3×200d, −5.9% over 4×180d). Only the most recent
   ~360 days are mildly positive; Apr–Oct 2025 is a −61% disaster (34.6% win rate,
   7-loss streak). Overall win rate (~51–54%) sits **below** the 55% break-even.
2. **Leverage makes it worse, not better.** 10x with fees and continuous
   compounding turns the 720-day run into **$70,000 → $24,575 (−65%)**. The
   asymmetry (−11% losses vs +9% wins) and volatility drag do the damage.
3. **Gemini's claim is fiction** for this strategy. The best six months even with
   **zero fees** is +47% ($103k), versus the claimed +575% ($472.5k). The worst
   six months loses 61%.
4. **Intrabar entry doesn't help.** It gets a slightly better entry price on
   shared trades (~64% of the time), but it also admits ~34% more trades —
   marginal signals that fail the 4h-close filter — and those extras are net
   losers that add fee drag. Overall it's modestly *worse* (10x with fees:
   $42,028 → $26,786).
5. **Exit granularity matters.** Minute-level exits change a handful of outcomes
   vs the 4h sim by resolving the "touched both TP and SL in one bar" cases.

Bottom line: as specified, this is not a profitable system, and it is certainly
not a 10x-leverage system. Any further work should focus on **filtering out the
chop** that produces the losing streaks, or on **changing the exit**, rather than
on entry timing.

---

## 7. What's left to test

- **ADX filter** — only take trades when trend strength (ADX) is above a
  threshold, to skip the ranging markets that produced the 7-loss streaks.
- **Daily candles** — test the same logic on the 1D timeframe (fewer, cleaner
  signals; less whipsaw) alongside or instead of 4h.
- **Exit on the opposite SAR flip** — instead of a fixed ±1% TP/SL, hold until the
  SAR flips back, to let winners run (the current ±1% caps every winner at +1%).
- **ATR-based stops/targets** — size TP/SL from recent volatility (ATR) instead of
  a fixed 1%, so stops aren't hit by normal noise in volatile regimes.

Each should be added as a *variant* (the original rules stay untouched) and
verified with numbers, per the project rules.

---

## 8. Reproducing these results

Run from the repo root with `uv` (dependencies passed inline; no `pyproject.toml`):

```bash
# indicators sanity check
uv run --with pandas --with requests backtest/checks/check_indicators.py

# single-window trade sim (25 trades / 60% / +2.50% for the last 200 days)
uv run --with pandas --with requests backtest/backtest.py

# multi-period / multi-window studies
uv run --with pandas --with requests backtest/runs/backtest_periods.py
uv run --with pandas --with requests backtest/runs/backtest_windows.py
uv run --with pandas --with requests backtest/runs/find_losses.py

# Excel exports (-> output/)
uv run --with pandas --with requests --with openpyxl backtest/export/export_trades.py
uv run --with pandas --with requests --with openpyxl backtest/export/export_trades_simple.py

# 1-minute cache (run once; downloads ~779k candles into data/)
uv run --with requests --with numpy backtest/intrabar_data.py

# intrabar vs original comparison (1-minute exits)
uv run --with pandas --with requests --with numpy backtest/intrabar.py
```

Outputs are written to `output/`; the 1-minute cache lives in `data/`. Both are
git-ignored.
