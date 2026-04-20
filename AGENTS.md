# AGENTS.md — Quant Trading System

## Project Overview

Full-stack quantitative trading system for US and HK equities. Python backend (Flask + event-driven engine), React frontend, DuckDB storage. Supports live/paper trading, backtesting, walk-forward validation, and LLM-based CIO market assessment.

## Repository Layout

```
D:\vk\quant\                    # Workspace root
├── api_server.py               # Flask API server — main web UI entry point
├── start_ui.bat / start_ui.sh  # Startup scripts (run api_server.py)
├── data/                       # RUNTIME data directory (CWD-relative)
│   ├── duckdb/quant.duckdb     # Active DuckDB database
│   └── strategy_state.json     # UI-level strategy state (selected strategy, running status)
├── frontend/                   # React 18 SPA (Create React App)
│   ├── src/                    # React source components
│   └── build/                  # Built frontend (served by api_server.py)
├── scripts/                    # Data pipeline, demos, profilers
├── docs/                       # Architecture docs
├── system/                     # Python package root (added to sys.path)
│   └── quant/                  # Python package "quant"
│       ├── quant_system.py     # CLI orchestrator for live/paper trading
│       ├── backtest_runner.py  # CLI backtest runner
│       ├── core/               # Engine, backtester, events, portfolio, risk, analytics, walkforward, scheduler
│       ├── data/               # Providers, DuckDB storage, SQLite storage, normalizer, symbol registry
│       ├── strategies/         # Strategy base, framework, factors, registry + per-strategy subdirs
│       ├── execution/          # Order manager, fill handler, portfolio coordinator, broker adapters
│       ├── models/             # Domain dataclasses: Order, Position, Fill, Trade, AccountInfo
│       ├── cio/                # CIO engine, market assessor, news analyzer, weight allocator, LLM adapters
│       ├── utils/              # Logger, datetime utils, config loader
│       ├── config/             # YAML configs: config.yaml, brokers.yaml, strategies.yaml
│       ├── tests/              # Pytest suite
│       └── migrations/         # (placeholder for future DB migrations)
```

## Key Conventions

### Python Package & sys.path

- Package name: `quant` — lives at `system/quant/`
- `system/` is added to `sys.path` so that `from quant.*` resolves to `system/quant/*`
- All imports use `from quant.<module>.<file> import <Class>`
- Never use relative imports; always use fully qualified `quant.*` paths

### sys.path Setup Pattern

```python
# api_server.py (workspace root): adds system/ to path
sys.path.insert(0, str(Path(__file__).parent / 'system'))

# quant_system.py / backtest_runner.py (inside system/quant/): adds system/ to path
sys.path.insert(0, str(Path(__file__).parent.parent))
```

### Strategy Architecture

- Each strategy lives in `quant/strategies/<name>/` with `strategy.py` + `config.yaml`
- Must use `@strategy("Name")` decorator for auto-registration
- Registry auto-discovers strategies by scanning subdirectories at import time
- Extend `Strategy` ABC from `quant.strategies.base` and implement lifecycle hooks: `on_start`, `on_before_trading`, `on_data`, `on_after_trading`, `on_fill`, `on_stop`
- Use `self.buy(symbol, qty)` / `self.sell(symbol, qty)` to submit orders
- Current strategies: `SimpleMomentum`, `VolatilityRegime`, `CrossSectionalMR`

### Domain Models

- All models are dataclasses in `quant/models/`
- Import from the package: `from quant.models import Order, Position, Fill, Trade, AccountInfo`
- Never duplicate model definitions elsewhere

### Data Layer

- Primary storage: `DuckDBStorage` (columnar, for backtesting + historical data)
- Legacy storage: `Storage` (SQLite + Parquet, still used for some persistence)
- Default DuckDB path is CWD-relative: `./data/duckdb/quant.duckdb`
  - When running `api_server.py` from workspace root → resolves to `D:\vk\quant\data\duckdb\quant.duckdb`
- All providers implement `DataProvider` ABC from `quant.data.providers.base`
- Symbol format: internal `AAPL` / `00700`, Futu `US.AAPL` / `HK.00700` — use `SymbolRegistry` for translation
- DuckDB tables: `{frequency}_{market}` — `daily_hk`, `daily_us`, `minute_hk`, `minute_us`
- Available providers: `YahooProvider`, `AlphaVantageProvider`, `FutuProvider`, `DuckDBProvider`, `YFinanceProvider`

### Configuration

- YAML configs in `quant/config/`, loaded by `ConfigLoader`
- CIO has its own config at `quant/cio/config/cio_config.yaml`
- `brokers.yaml` contains credentials — never commit real keys
- `ConfigLoader` resolves config dir relative to its own `__file__` location: `Path(__file__).parent.parent / "config"`

### Event System

- `EventBus` in `quant/core/events.py` — thread-safe pub/sub
- 17 event types:
  - Market data: `BAR`, `QUOTE`, `TRADE`
  - Orders: `ORDER_SUBMIT`, `ORDER_FILL`, `ORDER_CANCEL`, `ORDER_REJECT`
  - Portfolio: `POSITION_UPDATE`, `RISK_CHECK`
  - Session: `MARKET_OPEN`, `MARKET_CLOSE`, `STRATEGY_SIGNAL`, `SYSTEM_SHUTDOWN`
  - Research: `RESEARCH_SEARCH_DONE`, `RESEARCH_IDEA_SCORED`, `RESEARCH_CODE_READY`, `RESEARCH_REPORT_DONE`, `RESEARCH_ERROR`

### Frontend

- React 18 SPA in `frontend/`
- Talks to backend via REST API on `http://localhost:5000/api/*`
- API server serves the React build from `frontend/build/`
- After frontend changes, rebuild: `cd frontend && npm run build`
- Key components: `App.js`, `StrategyManagement.js`, `BacktestDashboard.js`, `LiveTradingPage.js`, `AccountOverview.js`, `CIOAssessmentPanel.js`, `StrategyPositionCards.js`

## Commands

### Start UI

```bash
# Production (Windows) — from workspace root:
start_ui.bat

# Development (macOS/Linux) — from workspace root:
./start_ui.sh
```

### Run Tests

```bash
# From workspace root:
python -m pytest system/quant/tests/ -q
```

### Run Backtest

```bash
# From workspace root:
python system/quant/backtest_runner.py --strategy SimpleMomentum --start 2024-01-01 --end 2024-12-31 --symbols HK.00700,HK.09988
```

### Prepare Data

```bash
python scripts/prepare_data.py --market hk --start 2020-01-01
```

## Code Style

- Python 3.10+ with type hints
- No comments unless explicitly requested
- Dataclasses for all data structures
- ABC + abstract methods for interface definitions
- Thread safety: use `threading.RLock()` for shared state
- Logging: `from quant.utils.logger import setup_logger`

## Architecture Decision Records

### Two Execution Paths

1. **Full system** (`quant_system.py` → `Engine`): for live/paper trading. Simple date-iteration replay for backtest mode.
2. **Standalone backtest** (`backtest_runner.py` → `Backtester`): sophisticated simulator with T+1 fills, lot sizes, suspension handling, volume limits, HK commission model. Also used by `WalkForwardEngine`.

### Strategy Framework

`quant/strategies/framework.py` provides reusable components:
- `SignalGenerator` — factor-based signal generation with configurable weights
- `PortfolioConstructor` — equal weight, Kelly, or risk parity allocation
- `AlphaEngine` — combines signal generation + portfolio construction
- `ExecutionScheduler` — TWAP-style order scheduling

### Multi-Strategy Coordination

`PortfolioCoordinator` manages shared risk budget across strategies with priority-based allocation. `StrategyPositionTracker` provides per-strategy position attribution with broker calibration support. Combined portfolio enforces leverage and concentration limits.

### CIO Module

Market regime detected by VIX SMA thresholds: bull (<15), chop (15-25), bear (>25). Each regime has predefined strategy weight allocations. LLM adapters (OpenAI/Claude/Ollama) provide news sentiment. All LLM calls degrade gracefully to neutral defaults on failure.

## File References

- Architecture doc: `docs/md/system_architecture_0419.md`
- Master config: `system/quant/config/config.yaml`
- Broker config: `system/quant/config/brokers.yaml` (gitignored)
- Strategy config: `system/quant/config/strategies.yaml`
- CIO config: `system/quant/cio/config/cio_config.yaml`
- DuckDB data: `data/duckdb/quant.duckdb` (gitignored, CWD-relative)
- Strategy state: `data/strategy_state.json` (gitignored, UI-level state)
- Strategy positions: `system/quant/data/strategy_positions.json` (per-strategy position tracking)
- Dependencies: `system/requirements.txt`
