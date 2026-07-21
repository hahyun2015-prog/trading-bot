# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AMATS (AI-Managed Automated Trading System) — a Windows-only Python system that trades live on a real Korean brokerage account via 키움증권(Kiwoom) OpenAPI+ (a 32-bit COM/ActiveX API). It auto-trades KOSPI stocks (day + swing), KOSPI200 mini futures, and individual stock futures (ISF) on Samsung Electronics and SK Hynix, using AI news-sentiment scoring and volatility-breakout entries. It is remote-controlled entirely through a Telegram bot.

**This is not a toy repo.** `config/config.json` currently defaults to `"environment": "live"`, meaning code changes to `era/`, `sta/`, `tca/`, or `rsa/` can directly affect real order execution and real money. Treat changes to risk/exit logic (stop-loss, take-profit, kill switch, position sizing) with proportional caution, and call out anything that changes order-execution or risk behavior explicitly.

## Active system vs. legacy directories

The codebase went through several generations. Only one is current:

- **Current ("AMATS")**: `era/`, `sta/`, `tca/`, `rsa/`, `bqa/` — actively developed (most recent commits touch these). Start here for any real work.
- **Legacy/superseded**: `ai_trader/`, `ai_trader_v2_swing/`, `futures_trader/`, `unified_trader/`, `telegram_controller/` — earlier iterations of the same bot (stock-only, then swing, then futures, then a "unified" stock engine with a separate Telegram controller), last touched in June 2026, before being consolidated into `era`/`tca`. They're kept in the repo but are not what's running. Don't extend them; if similar functionality is needed, it belongs in the AMATS modules. Several `*.bak*` files throughout the repo are similarly stale copies left in place, not active code.
- `bqa/` and the root-level `scratch/` directory contain one-off analysis/backtest/diagnostic scripts (e.g. `check_*.py`, `search_*.py`, `sim_*.py`). These are throwaway investigation tools, not part of the production pipeline — don't treat them as APIs other code depends on.

## Module map (AMATS)

| Module | Role | Bitness | Runs |
|---|---|---|---|
| `era/era_order_manager.py` | Core order/risk engine: Kiwoom login, real-time tick handling, entry/exit logic for stocks + KOSPI200 futures + ISF, position tracking, kill switch | 32-bit only (Kiwoom OpenAPI requirement) | Continuously during market hours |
| `sta/` | Screening: theme/leader detection (`theme_tracker.py`), intraday/swing candidate scanning (`screener.py`, `swing_screener.py`) | 32-bit (Kiwoom for quotes) | Scheduled, pre-market + intraday |
| `tca/tca_controller.py` | Telegram command & control: process supervision, status reporting, manual order commands, triggers RSA/BQA | Either | Continuously |
| `rsa/rsa_coordinator.py` | AI stock research: orchestrates FAA (financial safety), IRA (industry cycle), NSAA (Gemini news sentiment) into a composite score written to the signals DB | Either | ~08:50 daily |
| `bqa/` | Backtesting & K-value/parameter optimization (`batch_optimizer.py`, `kalman_backtester.py`, etc.); writes results to `config/active_strategy.json` for hot-reload into `era` | Either, no Kiwoom needed | Weekend / on-demand via Telegram |

`era` is genuinely one large monolith (`era_order_manager.py`, ~4900 lines) — it is a single `QThread`/`QAxWidget`-driven event loop (Kiwoom's `OnReceiveTrData`, `OnReceiveRealData`, `OnReceiveChejanData` callbacks) that owns login, TR requests, real-time tick processing, order placement, and risk management for every instrument class (day stocks, swing stocks, KOSPI200 futures, ISF). When navigating it, anchor on the instrument/section you care about (`_process_day_tick`, `_process_night_tick`, `_process_isf_tick`, `_execute_isf_order`, `update_kalman_targets`, `_poll_stock_signals`/`_poll_futures_signals`) rather than reading top to bottom.

## Cross-process architecture: SQLite as the message bus

The modules are separate OS processes (started independently via `.bat` files, no shared memory), so **SQLite databases double as both market-data storage and an inter-process signal queue**:

- `sta/screener.py`, `sta/swing_screener.py`, and `rsa_coordinator.py` write candidate trades into a `signals` table.
- `era_order_manager.py` polls that table (`poll_signals` → `_poll_stock_signals` / `_poll_futures_signals`) and executes/rejects them against live risk rules — it is the only process allowed to place real orders.
- OHLCV data (`intraday_ohlcv`, `futures_ohlcv`, `isf_ohlcv`) and theme data (`top_volume_theme`, `theme_mapping`) are similarly shared through `unified_data.db` / `futures_data.db` rather than passed in-process.
- `tca_controller.py` reads JSON status snapshots (`tca/system_status*.json`, gitignored, regenerated at runtime) and sends manual commands back by writing rows/flags `era` polls for (e.g. `manual_signals`, `emergency_kill.flag`).
- `bqa` optimizers write tuned parameters to `config/active_strategy.json`; `era` hot-reloads from it on `!전략승인` (strategy-approve) without a restart.

When changing a DB schema (`CREATE TABLE IF NOT EXISTS ...`) in one module, check whether another module reads/writes the same table — schemas are duplicated ad hoc across files (e.g. `signals`, `futures_ohlcv` are each defined identically in 2-3 places) rather than shared from one source of truth.

All `*.db` files, runtime JSON status files, and PID/flag files are gitignored — they don't exist in a fresh checkout and are regenerated on first run.

## Configuration

- `config/config.json` is the **real** config (environment, Telegram bot token, Kiwoom account settings, feature flags) and is gitignored — it will not exist in a fresh clone and must be created from scratch (see `SETUP_GUIDE.md` / `AI_팩토리_운영가이드.md` for required keys). The root `config.json` checked into git is a stripped-down placeholder, not the live config.
- `config/config_local.json` (also gitignored) is an optional per-machine overlay, e.g. for a machine-specific Gemini API key or a `dev_bot_token` so a second PC can run a shadow Telegram bot without colliding with the live one. Look for `config_local` overlay logic before assuming a single global config.
- `"environment"`: `"live"` vs `"mock"` gates real trading vs paper trading throughout `era` and the legacy engines. Kiwoom server codes differ per mode (`kiwoom_server.live = 1`, `.mock = 2`).
- The 2-PC deployment model (see `AI_팩토리_운영가이드.md`): one Windows PC runs `live` and executes real trades (`era`+`tca`); a second PC runs `mock` and hosts overnight backtesting/optimization (`bqa`) plus Claude Code itself, exchanging only backtest results (not code) via a shared Google Drive folder. Application code is intentionally kept out of that sync path — only tuned parameters cross the boundary.

## Running the system

There is no cross-platform dev entry point — this only runs on Windows with Kiwoom OpenAPI+ and 영웅문4 installed and logged in. Everything is launched via the `.bat` files at the repo root or per-module (`run_era.bat`, `run_tca.bat`, `run_sta.bat`, `run_bqa.bat`, `startup.bat` for the full auto-start sequence).

```
setup_env.bat        # one-time: creates venv32 (32-bit Python 3.8-3.10 required for Kiwoom) and installs deps
run_tca.bat           # Telegram controller — start first
run_era.bat           # order/risk engine — everything routes through Telegram once this is up
run_sta.bat           # screener menu (theme tracker / swing screener)
run_bqa.bat           # backtest/optimization menu (no Kiwoom needed, 64-bit OK)
```

`era`/`sta` require the 32-bit `venv32` environment (PyQt5 + Kiwoom's ActiveX control only exists as 32-bit); `bqa`/`rsa`/`tca` don't touch Kiwoom directly and can run under 64-bit Python. Don't assume a single venv for the whole repo.

Almost all control after startup happens through Telegram commands to the bot (see `AMATS_자동매매시스템보고서.md` §9 for the full command list), not by re-running scripts by hand — e.g. `!시스템시작`/`!시스템종료` (start/stop ERA), `!긴급정지` (emergency: liquidate everything then kill ERA), `!백테스트시작`/`!전략승인` (run optimization / hot-apply its result), `!매도 <종목명>` (manually close one position).

## Testing

There is no automated test suite (no pytest/unittest anywhere in the repo). Files named `test_*.py` (`test_era.py`, `unified_trader/test_api.py`, `futures_trader/test_futures_balance.py`, etc.) are manual diagnostic scripts you run by hand against a live/mock Kiwoom login to sanity-check connectivity or a specific code path — they are not part of a CI-style suite and aren't runnable headlessly. Validating trading-logic changes means running the relevant `bqa` backtester against historical data in `futures_data.db`/`unified_data.db`, not writing unit tests.

## Conventions and gotchas worth knowing before editing

- **Korean-language codebase**: comments, log messages, commit messages, and Telegram command text are predominantly Korean. Match the existing language when adding comments/log strings/commands in these files rather than switching to English.
- **Console encoding**: `era`, `tca`, and `notifier.py` each define their own `SafeStreamWrapper` around `stdout`/`stderr` to avoid crashes when emoji/Korean characters hit a CP949 Windows console, plus mirror all output to a `.log` file. If adding a new long-running entry point, follow the same pattern rather than printing straight to `sys.stdout`.
- **IPv4-forced sockets**: `notifier.py` and the Telegram controllers monkeypatch `socket.getaddrinfo` to force IPv4, working around IPv6 DNS hangs on Windows for Telegram API calls. Preserve this if refactoring notification code.
- **`notifier.py`** (repo root) is the shared Telegram-notification module imported (via `sys.path.append`) by `era` and other engines — prefer extending it over adding a parallel notification path.
- **Kiwoom's API is entirely callback-driven and stateful**: TR requests are rate-limited and must be sequenced (`_on_receive_tr_data` dispatches by `rqname`); there's no synchronous "call and get a return value" for most operations. When touching `era_order_manager.py`, respect the existing screen-number/rqname bookkeping rather than adding new synchronous-looking calls.
- **Position/state persistence**: `era` persists in-memory position state to `era/era_positions.json` and futures exit state to `era/futures_exit_state.json` (both gitignored) so a restart doesn't lose track of open positions — if you change position-tracking fields, update both the in-memory struct and these persist/load methods together.
