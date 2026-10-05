# crypto-candle-bot

A Python project that pulls Binance candle (kline) data from the public
market-data endpoint (no API key) and exposes it through a Telegram bot, plus a
staged backtester for a specific trading strategy.

## Layout

The **bot** lives at the repository root (its paths are hard-wired into a
systemd unit on the server — do not move these):

- `binance_logic.py` — fetches klines (`fetch_klines`), builds clean candle rows
  (`build_rows`), computes simple moving averages, assembles reports.
- `binance_report.py` — command-line front end for the report pipeline.
- `bot.py` — the Telegram bot (imports `binance_logic`).

The **backtester** lives under `backtest/`:

```
backtest/
  indicators.py        # EMA, MACD, Parabolic SAR (+ SAR trigger level)
  signals.py           # turn indicators into long/short signals
  backtest.py          # single-window trade simulation (fees, TP/SL)
  intrabar.py          # intrabar-entry variant + 1-minute exit comparison
  intrabar_data.py     # fetch/cache 1-minute candles (data/ at repo root)
  runs/                # analysis scripts (multi-period / multi-window studies)
    backtest_periods.py    # 3 x 200-day periods
    backtest_windows.py    # 4 x 180-day windows + 10x leverage column
    find_losses.py         # extract specific losing trades
  export/              # Excel exporters
    export_trades.py       # mutaz_backtest.xlsx (with fees)
    export_trades_simple.py# mutaz_backtest_simple.xlsx (no fees)
  checks/
    check_indicators.py    # print latest indicator values (sanity check)
  RESULTS.md           # full write-up of method + results + conclusions
```

Candle rows from `build_rows` are dicts with: `open_time` (UTC string), `open`,
`close`, `high`, `low`, `change`.

### Data & output folders (both git-ignored)

- `data/` — cached 1-minute candles, one normalized CSV per month. Downloaded
  once by `intrabar_data.py` (monthly dumps from data.binance.vision, current
  month via REST). Kept at the repo root.
- `output/` — every script writes its CSV/XLSX here (`backtest_trades.csv`,
  `losses_to_show.csv`, `mutaz_backtest.xlsx`, `mutaz_backtest_simple.xlsx`).

## Imports (flat layout preserved)

Scripts under `backtest/` import each other by bare module name
(`from signals import ...`, `from backtest import ...`, etc.) and import the
root-level `binance_logic`. To make this work regardless of how a script is
launched, each entry-point begins with a small bootstrap that locates the repo
root (the directory containing `binance_logic.py`) and prepends the repo root
plus the `backtest/` subfolders to `sys.path`. Keep that block when adding new
scripts.

## Strategy under test

Entry on a **closed** BTCUSDT 4h candle:

- **Long**: Parabolic SAR flips down→up, `close > EMA200`, MACD histogram > 0.
- **Short**: Parabolic SAR flips up→down, `close < EMA200`, MACD histogram < 0.

Entry at the next 4h candle's open; TP/SL ±1%; 0.05% fee per side. A variant
(`intrabar.py`) enters inside the flip candle instead. See `backtest/RESULTS.md`
for the exact rules and all results.

## Backtest plan (staged, each phase verified before the next)

- **Phase 1 — Indicators (done, verified).** `backtest/indicators.py`: `ema`
  (TradingView SMA-seeded), `macd` (12/26/9), `parabolic_sar` (0.02/0.02/0.2),
  and `parabolic_sar_trigger` (the per-bar level price must cross). The SAR is a
  line-for-line port of TradingView's `pine_sar`; it matches TradingView on-chart
  values to the cent, MACD matches the `ta` library exactly, EMA200 to <0.03%.
- **Phase 2 — Signals (done).** `backtest/signals.py`.
- **Phase 3 — Trade simulation (done).** `backtest/backtest.py`.
- **Phase 4 — Comparing variants (in progress).** `backtest/runs/` studies and
  the intrabar variant. Next candidates: ADX filter, daily candles, exit on SAR
  flip, ATR-based stops (see RESULTS.md).
- **Phase 5 — Out-of-sample testing.** Validate a chosen variant on data not used
  during development.

## Rules (must follow)

1. **Verify with numbers, not reasoning.** Every result must be verified with
   real numbers against an external reference (e.g. TradingView, the `ta`
   library). Never declare something correct based on reasoning alone.
2. **Never commit temporary or debugging scripts.** One-off comparison/diagnostic
   scripts stay out of version control — delete them or keep them untracked.

## Running

Scripts are run with `uv run` from the repo root. There is no `pyproject.toml`;
pass dependencies inline, e.g.:

```
uv run --with pandas --with requests backtest/backtest.py
uv run --with pandas --with requests backtest/runs/backtest_windows.py
uv run --with pandas --with requests --with openpyxl backtest/export/export_trades.py
uv run --with pandas --with requests --with numpy backtest/intrabar.py
```

Third-party libraries used only for verification (e.g. `ta`) must **not** be
added to `requirements.txt`.
