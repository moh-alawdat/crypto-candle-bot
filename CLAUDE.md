# crypto-candle-bot

A Python project that pulls Binance candle (kline) data from the public
market-data endpoint (no API key) and exposes it through a Telegram bot, plus a
staged backtester for a specific trading strategy.

## Existing pieces

- `binance_logic.py` — fetches klines (`fetch_klines`), builds clean candle rows
  (`build_rows`), computes simple moving averages, and assembles reports.
- `binance_report.py` — command-line front end for the report pipeline.
- `bot.py` — the Telegram bot.
- `indicators.py` — hand-rolled technical indicators (Phase 1).
- `check_indicators.py` — prints the latest indicator values for a sanity check.

Candle rows from `build_rows` are dicts with: `open_time` (UTC string), `open`,
`close`, `high`, `low`, `change`.

## Strategy under test

Entry on a **closed** BTCUSDT 4h candle:

- **Long**: Parabolic SAR flips down→up, `close > EMA200`, MACD histogram > 0.
- **Short**: Parabolic SAR flips up→down, `close < EMA200`, MACD histogram < 0.

## Backtest plan (staged, each phase verified before the next)

- **Phase 1 — Indicators (done, verified).** `indicators.py`: `ema` (TradingView
  SMA-seeded), `macd` (12/26/9), `parabolic_sar` (0.02/0.02/0.2). The SAR is a
  line-for-line port of TradingView's `pine_sar` reference and was verified to
  match TradingView's on-chart values to the cent; MACD matches the `ta` library
  exactly and EMA200 to <0.03%.
- **Phase 2 — Signals (current).** `signals.py`: detect long/short entry signals
  from the indicators. No trade simulation.
- **Phase 3 — Trade simulation.** Simulate entering on signals with realistic
  fees and take-profit / stop-loss exits. Produce per-trade and summary stats.
- **Phase 4 — Comparing variants.** Vary strategy parameters/filters and compare
  performance across variants on the same data.
- **Phase 5 — Out-of-sample testing.** Validate the chosen variant on a data
  window not used during development to check it generalizes.

## Rules (must follow)

1. **Verify with numbers, not reasoning.** Every result must be verified with
   real numbers against an external reference (e.g. TradingView, the `ta`
   library). Never declare something correct based on reasoning alone.
2. **Never commit temporary or debugging scripts.** One-off comparison/diagnostic
   scripts stay out of version control — delete them or keep them untracked.

## Running

Scripts are run with `uv run`. There is no `pyproject.toml`; pass dependencies
inline, e.g. `uv run --with pandas --with requests signals.py`. Third-party
libraries used only for verification (e.g. `ta`) must **not** be added to
`requirements.txt`.
