#!/usr/bin/env python3
"""
Command-line front end for the Binance candle report.

All the data logic lives in binance_logic.py; this file only parses arguments,
calls get_report + write_csv, and prints a summary.

Usage:
    python binance_report.py                          # BTCUSDT, daily, last 100 candles
    python binance_report.py --symbol ETHUSDT --interval 1h --limit 500
    python binance_report.py --symbol BTCUSDT --interval 1d --limit 20 --output btc.csv

Requires:  pip install requests
"""

import argparse
import sys

import requests

from binance_logic import VALID_INTERVALS, WARMUP, get_report, write_csv


def print_summary(report):
    rows = report["rows"]
    if not rows:
        print("No data returned.")
        return

    stats = report["stats"]
    print(f"\n{report['symbol'].upper()}  |  interval {report['interval']}  |  {report['count']} candles")
    print(f"Period: {rows[0]['open_time']}  ->  {rows[-1]['open_time']} (UTC)")
    print(f"{'Window net change':<17} : {stats['change_pct']:.2f}%")
    print(f"{'Window range':<17} : {stats['range_abs']:.2f} ({stats['range_pct']:.2f}%)")


def main():
    parser = argparse.ArgumentParser(description="Pull Binance candle data and build a CSV report.")
    parser.add_argument("--symbol",   default="BTCUSDT", help="Trading pair, e.g. BTCUSDT, ETHUSDT")
    parser.add_argument("--interval", default="1d",      help=f"Candle interval. One of: {sorted(VALID_INTERVALS)}")
    parser.add_argument("--limit",    type=int, default=100, help="Number of candles to report")
    parser.add_argument("--output",   default=None,      help="Output CSV path (default: <symbol>_<interval>.csv)")
    args = parser.parse_args()

    if args.interval not in VALID_INTERVALS:
        sys.exit(f"Invalid interval '{args.interval}'. Valid: {sorted(VALID_INTERVALS)}")

    output = args.output or f"{args.symbol.upper()}_{args.interval}.csv"

    print(f"Fetching {args.limit} '{args.interval}' candles for {args.symbol.upper()} "
          f"(+{WARMUP} warm-up) ...")
    try:
        report = get_report(args.symbol, args.interval, args.limit)
    except requests.HTTPError as e:
        sys.exit(f"Binance API error: {e}\nCheck the symbol exists (e.g. BTCUSDT) and try again.")
    except requests.RequestException as e:
        sys.exit(f"Network error: {e}\nIf api.binance.com is blocked in your region, "
                 f"switch BASE_URL to the data-api.binance.vision mirror near the top of binance_logic.py.")

    write_csv(report["rows"], output)
    print_summary(report)
    print(f"\nReport saved to: {output}")


if __name__ == "__main__":
    main()
