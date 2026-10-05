#!/usr/bin/env python3
"""
BTCUSDT 1-minute candle fetching + caching for the intrabar-entry strategy.

Bulk history comes from Binance's public data dumps (data.binance.vision) as one
zip per complete month — far fewer requests than the REST API and no rate-limit
weight. The current (incomplete) month is topped up from the REST API, paginating
past the 1000-candle cap with a small sleep to respect rate limits.

Everything is cached under data/ (git-ignored) as normalized CSVs
(open_time_ms, open, high, low, close), so each month is downloaded only once.

Run directly to pre-populate the cache for the last 540 days:
  uv run --with requests intrabar_data.py
"""

import csv
import io
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYMBOL = "BTCUSDT"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data"  # repo-root/data
MONTHLY_URL = ("https://data.binance.vision/data/spot/monthly/klines/"
               "{sym}/1m/{sym}-1m-{y:04d}-{m:02d}.zip")
API_URL = "https://api.binance.com/api/v3/klines"
API_SLEEP = 0.25  # seconds between REST requests


def _normalize_ts(ts):
    """Binance switched some dumps to microseconds; normalize everything to ms."""
    ts = int(ts)
    return ts // 1000 if ts > 1e14 else ts


def _month_path(y, m):
    return CACHE_DIR / f"{SYMBOL}_1m_{y:04d}-{m:02d}.csv"


def _write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["open_time", "open", "high", "low", "close"])
        w.writerows(rows)


def _read_rows(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r, None)  # header
        for line in r:
            rows.append((int(line[0]), float(line[1]), float(line[2]),
                         float(line[3]), float(line[4])))
    return rows


def _rows_from_zip(content):
    """Extract (ot_ms, o, h, l, c) rows from a Binance kline monthly/daily zip."""
    zf = zipfile.ZipFile(io.BytesIO(content))
    out = []
    with zf.open(zf.namelist()[0]) as fh:
        for line in io.TextIOWrapper(fh, encoding="utf-8"):
            parts = line.strip().split(",")
            try:
                ot = _normalize_ts(parts[0])
            except (ValueError, IndexError):
                continue  # header row or blank
            out.append((ot, parts[1], parts[2], parts[3], parts[4]))
    return out


def _fetch_api(start_ms, end_ms):
    """Paginated 1m fetch over [start_ms, end_ms) from the REST API."""
    out = []
    cur = start_ms
    while cur < end_ms:
        params = {"symbol": SYMBOL, "interval": "1m",
                  "startTime": cur, "endTime": end_ms, "limit": 1000}
        resp = requests.get(API_URL, params=params, timeout=20)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        for k in batch:
            ot = _normalize_ts(k[0])
            if ot >= end_ms:
                break
            out.append((ot, k[1], k[2], k[3], k[4]))
        cur = _normalize_ts(batch[-1][0]) + 60_000
        if len(batch) < 1000:
            break
        time.sleep(API_SLEEP)
    return out


def _download_monthly(y, m):
    """Return extracted rows for a complete month, or None if the dump is 404."""
    url = MONTHLY_URL.format(sym=SYMBOL, y=y, m=m)
    resp = requests.get(url, timeout=60)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return _rows_from_zip(resp.content)


def _month_iter(start, end):
    """Yield (year, month) from start's month through end's month inclusive."""
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            m, y = 1, y + 1


def ensure_cache(start_dt, end_dt):
    """Make sure data/ holds 1m candles covering [start_dt, end_dt]."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    cur_ym = (now.year, now.month)

    for y, m in _month_iter(start_dt, end_dt):
        path = _month_path(y, m)
        if path.exists():
            continue

        if (y, m) == cur_ym:
            # Incomplete current month -> REST API from month start to now.
            month_start = datetime(y, m, 1, tzinfo=timezone.utc)
            rows = _fetch_api(int(month_start.timestamp() * 1000),
                              int(now.timestamp() * 1000))
            print(f"  {y}-{m:02d}: {len(rows):>6} rows via API (current month)")
            _write_rows(path, rows)
        else:
            rows = _download_monthly(y, m)
            if rows is None:
                # Monthly dump not published yet -> fall back to the API.
                ms = datetime(y, m, 1, tzinfo=timezone.utc)
                me = (datetime(y + (m == 12), (m % 12) + 1, 1, tzinfo=timezone.utc))
                rows = _fetch_api(int(ms.timestamp() * 1000),
                                  int(me.timestamp() * 1000))
                print(f"  {y}-{m:02d}: {len(rows):>6} rows via API (no monthly dump)")
            else:
                print(f"  {y}-{m:02d}: {len(rows):>6} rows via monthly dump")
            _write_rows(path, rows)


def load_minutes(start_dt, end_dt):
    """Load cached 1m candles for [start_dt, end_dt] as sorted numpy arrays.

    Returns a dict with 'ot' (int64 ms) and 'open'/'high'/'low'/'close' (float64).
    """
    import numpy as np

    ensure_cache(start_dt, end_dt)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    rows = []
    for y, m in _month_iter(start_dt, end_dt):
        path = _month_path(y, m)
        if path.exists():
            rows.extend(_read_rows(path))

    rows.sort(key=lambda r: r[0])
    # Dedupe by timestamp (month boundaries / API overlap) and clip to range.
    seen = set()
    ot, o, h, l, c = [], [], [], [], []
    for r in rows:
        if r[0] < start_ms or r[0] > end_ms or r[0] in seen:
            continue
        seen.add(r[0])
        ot.append(r[0]); o.append(r[1]); h.append(r[2]); l.append(r[3]); c.append(r[4])

    return {
        "ot": np.asarray(ot, dtype="int64"),
        "open": np.asarray(o, dtype="float64"),
        "high": np.asarray(h, dtype="float64"),
        "low": np.asarray(l, dtype="float64"),
        "close": np.asarray(c, dtype="float64"),
    }


def main():
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=540 + 1)  # 3 x 180-day windows + 1 day buffer
    print(f"Caching BTCUSDT 1m from {start.date()} to {now.date()} under {CACHE_DIR}/\n")
    ensure_cache(start, now)

    mins = load_minutes(start, now)
    n = len(mins["ot"])
    from datetime import datetime as dt
    first = dt.fromtimestamp(mins["ot"][0] / 1000, tz=timezone.utc)
    last = dt.fromtimestamp(mins["ot"][-1] / 1000, tz=timezone.utc)
    # Count gaps (missing minutes) as a data-quality check.
    import numpy as np
    diffs = np.diff(mins["ot"])
    gaps = int((diffs != 60_000).sum())
    print(f"\nLoaded {n:,} 1m candles  {first} -> {last} UTC")
    print(f"Minute gaps (non-60s steps): {gaps}")


if __name__ == "__main__":
    main()
