#!/usr/bin/env python3
"""
Technical indicators computed from scratch (no TA library), using pandas.

These functions operate on plain sequences of numbers pulled out of the candle
rows that `binance_logic.build_rows` produces (each row is a dict with keys like
"close", "high", "low"). The caller is expected to extract the column it wants,
e.g. ``[r["close"] for r in rows]``, and hand it to these functions.

Every formula is implemented by hand and commented so the maths is auditable.
This is Phase 1: indicators only — no signal or trading logic lives here.
"""

import numpy as np
import pandas as pd


def ema(values, period):
    """Exponential Moving Average, seeded the TradingView way.

    The EMA gives more weight to recent values and less to older ones. Each new
    EMA value is:

        EMA_today = alpha * value_today + (1 - alpha) * EMA_yesterday

    where the smoothing factor is:

        alpha = 2 / (period + 1)

    The very first EMA value has no "yesterday" to lean on, so — exactly like
    TradingView — we seed it with the Simple Moving Average (plain mean) of the
    first `period` values. Everything before that seed is left empty (NaN).

    `values` may itself contain leading NaNs (the MACD signal line is an EMA of
    the MACD line, which starts out undefined); those are skipped so the seed is
    the mean of the first `period` *real* values.
    """
    s = pd.Series(values, dtype="float64").reset_index(drop=True)
    alpha = 2.0 / (period + 1)

    out = pd.Series(np.nan, index=s.index, dtype="float64")

    # Work only over the positions that actually have a number.
    valid = s.dropna()
    if len(valid) < period:
        return out  # not enough data to even seed one EMA value

    # Seed: SMA of the first `period` real values, placed at the position of the
    # period-th real value (so it lines up with the original series).
    seed_pos = valid.index[period - 1]
    prev = valid.iloc[:period].mean()
    out.loc[seed_pos] = prev

    # Roll the recursive formula forward over every real value after the seed.
    for idx in valid.index[period:]:
        prev = alpha * s.loc[idx] + (1 - alpha) * prev
        out.loc[idx] = prev

    return out


def macd(closes, fast=12, slow=26, signal=9):
    """Moving Average Convergence Divergence.

    Returns three aligned series (macd_line, signal_line, histogram):

        MACD line   = EMA(fast) - EMA(slow)
            A fast EMA reacts quicker than a slow one; their gap measures
            short-term momentum relative to the longer trend. Positive = the
            fast average is above the slow one (bullish momentum).

        signal line = EMA(signal) of the MACD line
            A smoothed version of the MACD line used as a trigger/baseline.

        histogram   = MACD line - signal line
            The gap between the two. Crossing zero means the MACD line crossed
            its signal line; growing bars mean momentum is accelerating.
    """
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)

    macd_line = ema_fast - ema_slow          # EMA(fast) - EMA(slow)
    signal_line = ema(macd_line, signal)     # EMA(signal) of the MACD line
    histogram = macd_line - signal_line      # MACD line - signal line

    return macd_line, signal_line, histogram


def _sar_core(highs, lows, closes, start=0.02, increment=0.02, maximum=0.2):
    """Parabolic SAR, ported line-for-line from TradingView's `ta.sar` reference.

    TradingView documents `ta.sar(start, inc, max)` with an equivalent Pine
    implementation (`pine_sar`). This is a faithful port of that Pine code so our
    values match TradingView's chart exactly. Each block below is annotated with
    the Pine line(s) it mirrors. Note the Pine logic requires `close` (it seeds
    the initial trend from close vs close[1]), hence the extra `closes` argument.

    SAR prints a trailing dot: below price in an uptrend (`isBelow = True`), above
    price in a downtrend (`isBelow = False`). The key ordering that makes it match
    TradingView — and differs from a naive Wilder coding — is:
        1. advance the SAR:  result := result + acceleration * (maxMin - result)
        2. test for reversal on that *raw* result (before any clamping)
        3. only if no reversal this bar, extend the extreme point / accelerate
        4. finally clamp the result against the previous two bars' low/high

    `maxMin` is Pine's name for the extreme point (EP); `acceleration` is the AF.

    Returns parallel lists (sar, direction, trigger); the public wrappers
    `parabolic_sar` and `parabolic_sar_trigger` wrap these as pandas Series.
    direction is +1 uptrend / -1 downtrend; bar 0 has no SAR (NaN / 0). `trigger`
    is the pre-reversal projected level for each bar (see parabolic_sar_trigger).
    """
    highs = [float(h) for h in highs]
    lows = [float(l) for l in lows]
    closes = [float(c) for c in closes]
    n = len(highs)

    sar = [np.nan] * n
    direction = [0] * n
    # Pre-reversal SAR level for each bar (the level price must cross to flip),
    # captured right after the projection step, before the reversal test / clamp.
    trigger = [np.nan] * n

    # Pine `var` state — persists across bars; starts as na (None here).
    result = None        # var float result = na        (the SAR value)
    maxMin = None        # var float maxMin = na         (extreme point / EP)
    acceleration = None  # var float acceleration = na   (AF)
    isBelow = None       # var bool  isBelow = na         (True = uptrend)

    for i in range(n):
        # bool isFirstTrendBar = false  -> reset every bar (not a `var`)
        isFirstTrendBar = False

        # if bar_index == 1  -> initialise trend from close vs close[1]
        if i == 1:
            if closes[i] > closes[i - 1]:
                isBelow = True          # isBelow := true
                maxMin = highs[i]       # maxMin := high
                result = lows[i - 1]    # result := low[1]
            else:
                isBelow = False         # isBelow := false
                maxMin = lows[i]        # maxMin := low
                result = highs[i - 1]   # result := high[1]
            isFirstTrendBar = True      # isFirstTrendBar := true
            acceleration = start        # acceleration := start

        # result := result + acceleration * (maxMin - result)
        if result is not None:
            result = result + acceleration * (maxMin - result)
            # This projected value — before the reversal test and clamp — is the
            # level price must cross on this bar; it is known at the bar's open.
            trigger[i] = result

        # Reversal test on the raw result. In Pine `if isBelow` treats na as
        # false, so these only fire once isBelow has been initialised.
        if isBelow is True:
            # if result > low  -> uptrend flips to downtrend
            if result > lows[i]:
                isFirstTrendBar = True      # isFirstTrendBar := true
                isBelow = False             # isBelow := false
                result = max(highs[i], maxMin)  # result := math.max(high, maxMin)
                maxMin = lows[i]            # maxMin := low
                acceleration = start        # acceleration := start
        elif isBelow is False:
            # if result < high  -> downtrend flips to uptrend
            if result < highs[i]:
                isFirstTrendBar = True      # isFirstTrendBar := true
                isBelow = True              # isBelow := true
                result = min(lows[i], maxMin)   # result := math.min(low, maxMin)
                maxMin = highs[i]           # maxMin := high
                acceleration = start        # acceleration := start

        # if not isFirstTrendBar  -> extend EP and accelerate (no flip this bar)
        if not isFirstTrendBar:
            if isBelow is True:
                # if high > maxMin: maxMin := high; acc := min(acc+inc, max)
                if highs[i] > maxMin:
                    maxMin = highs[i]
                    acceleration = min(acceleration + increment, maximum)
            elif isBelow is False:
                # if low < maxMin: maxMin := low; acc := min(acc+inc, max)
                if lows[i] < maxMin:
                    maxMin = lows[i]
                    acceleration = min(acceleration + increment, maximum)

        # Clamp against the previous two bars' extremes (after EP/AF update).
        if isBelow is True:
            result = min(result, lows[i - 1])          # result := min(result, low[1])
            if i > 1:
                result = min(result, lows[i - 2])      # if bar_index>1: min(result, low[2])
        elif isBelow is False:
            result = max(result, highs[i - 1])         # result := max(result, high[1])
            if i > 1:
                result = max(result, highs[i - 2])     # if bar_index>1: max(result, high[2])

        # Record the bar's SAR + direction (bar 0 stays na / 0).
        if isBelow is not None and result is not None:
            sar[i] = result
            direction[i] = 1 if isBelow else -1

    return sar, direction, trigger


def parabolic_sar(highs, lows, closes, start=0.02, increment=0.02, maximum=0.2):
    """Parabolic SAR -> (sar, direction) pandas Series. See `_sar_core`.

    Output is unchanged from before this variant: +1 uptrend, -1 downtrend,
    NaN / 0 on bar 0.
    """
    sar, direction, _ = _sar_core(highs, lows, closes, start, increment, maximum)
    return pd.Series(sar, dtype="float64"), pd.Series(direction, dtype="int64")


def parabolic_sar_trigger(highs, lows, closes, start=0.02, increment=0.02, maximum=0.2):
    """Per-bar SAR trigger level: the SAR value *before any reversal* on that bar
    — the level price must cross to flip — which is known at the bar's open.

    This is the raw projected `result` from Pine's recursion, taken before the
    reversal test and the two-bar clamp, i.e. exactly the threshold the reversal
    test compares price against. Exposed for the intrabar-entry variant without
    altering `parabolic_sar`'s outputs.
    """
    _, _, trigger = _sar_core(highs, lows, closes, start, increment, maximum)
    return pd.Series(trigger, dtype="float64")
