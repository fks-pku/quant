# Quant Trading System - Architecture Documentation

> Last updated: 2025-04-19

---

## 1. System Overview

This is a full-stack quantitative trading system supporting live trading, paper trading, and backtesting across **US and HK equities**. It includes a Python backend engine, a React frontend dashboard, and a CIO (Chief Investment Officer) module powered by LLM-based market analysis.

### Key Capabilities

| Feature | Description |
|---------|-------------|
| **Multi-market** | US equities + HK equities, with per-market commission models |
| **Multi-broker** | Paper (simulated), Futu OpenD (live HK/US), IBKR (planned) |
| **Strategy framework** | Event-driven lifecycle, factor library, auto-discovery registry |
| **Backtesting** | T+1 fills, lot-size enforcement, suspension handling, volume participation limits |
| **Walk-forward validation** | Rolling train/test with grid-search parameter optimization |
| **CIO module** | VIX-based regime detection, LLM news analysis, strategy weight allocation |
| **Web dashboard** | React SPA for backtest execution, live trading, strategy lifecycle management |

---

## 2. Project Structure

```
D:\vk\quant\
├── api_server.py              # Flask REST API server (port 5000)
├── start_ui.bat               # Windows launcher (production build)
├── start_ui.sh                # macOS/Linux launcher (dev mode)
│
├── system/quant/              # Core Python package
│   ├── quant_system.py        # CLI entry point & orchestrator
│   ├── backtest_runner.py     # Standalone backtest CLI
│   │
│   ├── core/                  # Trading engine core
│   │   ├── engine.py          # Central event-loop engine
│   │   ├── backtester.py      # Sophisticated backtest simulator
│   │   ├── events.py          # Thread-safe pub/sub EventBus
│   │   ├── portfolio.py       # Position & NAV tracking
│   │   ├── risk.py            # Pre-order risk engine
│   │   ├── analytics.py       # 15+ performance metrics
│   │   ├── walkforward.py     # Walk-forward validation framework
│   │   └── scheduler.py       # Time-based job scheduler
│   │
│   ├── data/                  # Data layer
│   │   ├── providers/         # Data source adapters
│   │   │   ├── base.py        # DataProvider ABC
│   │   │   ├── yfinance_provider.py  # yfinance + Parquet cache
│   │   │   ├── yahoo.py       # Simple yfinance (no cache)
│   │   │   ├── alpha_vantage.py # Alpha Vantage REST API
│   │   │   ├── futu.py        # Futu OpenD (streaming + historical)
│   │   │   └── duckdb_provider.py  # DuckDB backtest data
│   │   ├── storage.py         # SQLite + Parquet persistence (legacy)
│   │   ├── storage_duckdb.py  # DuckDB columnar storage (primary)
│   │   ├── normalizer.py      # Schema normalization layer
│   │   └── symbol_registry.py # Symbol format translation
│   │
│   ├── strategies/            # Strategy layer
│   │   ├── base.py            # Strategy ABC with lifecycle hooks
│   │   ├── framework.py       # Alpha pipeline (signal → portfolio → execution)
│   │   ├── factors.py         # 10-factor library + IC calculator
│   │   ├── registry.py        # Decorator-based auto-discovery registry
│   │   ├── simple_momentum/   # Cross-sectional momentum strategy
│   │   ├── cross_sectional_mr/ # Cross-sectional mean reversion
│   │   └── volatility_regime/  # VIX regime-switching strategy
│   │
│   ├── execution/             # Order execution layer
│   │   ├── order_manager.py   # Order lifecycle + broker routing
│   │   ├── fill_handler.py    # Fill reconciliation & portfolio update
│   │   ├── portfolio_coordinator.py # Multi-strategy risk budgeting
│   │   └── brokers/           # Broker adapters
│   │       ├── base.py        # BrokerAdapter ABC
│   │       ├── paper.py       # Paper trading simulator
│   │       ├── futu.py        # Futu OpenD live trading
│   │       └── ibkr.py        # IBKR stub (planned)
│   │
│   ├── models/                # Domain models (dataclasses)
│   │   ├── order.py           # Order + OrderStatus enum
│   │   ├── position.py        # Position
│   │   ├── fill.py            # Fill
│   │   ├── trade.py           # Trade (round-trip)
│   │   └── account.py         # AccountInfo
│   │
│   ├── cio/                   # CIO (Chief Investment Officer) module
│   │   ├── cio_engine.py      # CIO orchestrator
│   │   ├── market_assessor.py # VIX-based regime detection
│   │   ├── news_analyzer.py   # LLM news sentiment analysis
│   │   ├── weight_allocator.py # Regime-based strategy weight allocation
│   │   ├── llm_adapters/      # LLM provider adapters
│   │   │   ├── base.py        # LLMAdapter ABC
│   │   │   ├── openai_adapter.py
│   │   │   ├── ollama_adapter.py
│   │   │   └── claude_adapter.py
│   │   └── config/
│   │       └── cio_config.yaml
│   │
│   ├── utils/                 # Utilities
│   │   ├── logger.py          # Structured logging
│   │   ├── datetime_utils.py  # Timezone-aware market time utilities
│   │   └── config_loader.py   # YAML config loader with caching
│   │
│   ├── config/                # Configuration files
│   │   ├── config.yaml        # Master system config
│   │   ├── brokers.yaml       # Broker credentials (gitignored)
│   │   └── strategies.yaml    # Strategy parameters
│   │
│   ├── tests/                 # Unit & integration tests
│   └── migrations/            # Database migrations
│
├── frontend/                  # React web dashboard
│   ├── src/
│   │   ├── App.js             # Root component with tab navigation
│   │   ├── BacktestDashboard.js    # Backtest config + results
│   │   ├── LiveTradingPage.js      # Live trading container
│   │   ├── PositionOverview.js     # Portfolio holdings view
│   │   ├── StrategyManagement.js   # Strategy lifecycle management
│   │   ├── StrategyDetailModal.js  # Strategy detail popup
│   │   ├── CIOAssessmentPanel.js   # CIO market assessment
│   │   └── StrategyWeightBar.js    # Weight visualization
│   └── package.json
│
├── scripts/                   # Utility scripts
│   ├── prepare_data.py        # Futu → DuckDB data pipeline
│   ├── futu_backtest_demo.py  # End-to-end demo (Tencent SMA)
│   ├── run_backtest_test.py   # Quick integration test
│   ├── profile_backtest.py    # Backtest performance profiler
│   └── profile_vr.py          # VolatilityRegime profiler
│
└── data/
    ├── duckdb/                # DuckDB storage files
    └── strategy_state.json    # Persisted strategy states
```

---

## 3. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                     React Frontend (:3000/:5000)                 │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────────┐  │
│  │  Backtest     │  │ Live Trading  │  │ Strategy Management  │  │
│  │  Dashboard    │  │ Page          │  │ + CIO Panel          │  │
│  └──────┬───────┘  └──────┬────────┘  └──────────┬───────────┘  │
│         └──────────────────┼──────────────────────┘              │
└────────────────────────────┼────────────────────────────────────┘
                             │ axios REST API
                             ▼
┌────────────────────────────────────────────────────────────────┐
│                  Flask API Server (:5000)                       │
│  /api/backtest/*  /api/strategies/*  /api/cio/*  /api/orders   │
└────────────────────────────┬───────────────────────────────────┘
                             │
         ┌───────────────────┼──────────────────────┐
         ▼                   ▼                      ▼
┌─────────────┐   ┌──────────────────┐   ┌─────────────────┐
│  CIO Engine │   │  Trading Engine  │   │  Backtester     │
│             │   │  (core/engine)   │   │  (core/)        │
│ MarketAssess│   │                  │   │                 │
│ NewsAnalyze │   │  EventBus ◄─────┼──►│ Backtester      │
│ WeightAlloc │   │  Portfolio       │   │ WalkForward     │
│             │   │  RiskEngine      │   │ Analytics       │
│ LLM Adapters│   │  Scheduler       │   │                 │
│ (OpenAI/    │   │                  │   │                 │
│  Claude/    │   │  Strategies      │   │                 │
│  Ollama)    │   │  ◄── Registry    │   │                 │
└──────┬──────┘   └────────┬─────────┘   └────────┬────────┘
       │                   │                      │
       │          ┌────────┼──────────┐           │
       │          ▼        ▼          ▼           │
       │   ┌──────────┐ ┌────────┐ ┌───────────┐ │
       │   │ OrderMgr │ │ Fill   │ │ Portfolio  │ │
       │   │          │ │ Handler│ │ Coordinator│ │
       │   └────┬─────┘ └────────┘ └───────────┘ │
       │        │                                   │
       │   ┌────┼─────────────────┐                │
       │   ▼    ▼                 ▼                │
       │ Paper  Futu           IBKR               │
       │ Broker Broker         (stub)             │
       │        │                                 │
       └────────┼─────────────────────────────────┘
                ▼
┌──────────────────────────────────────────────────────────────┐
│                        Data Layer                             │
│                                                               │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐   │
│  │ DuckDB      │  │ Symbol       │  │ Normalizer         │   │
│  │ Storage     │  │ Registry     │  │ (schema unification)│   │
│  │ (primary)   │  │ (HK.00700 ↔  │  │                    │   │
│  │             │  │  00700 ↔     │  │                    │   │
│  │             │  │  AAPL)       │  │                    │   │
│  └──────┬──────┘  └──────────────┘  └────────────────────┘   │
│         │                                                     │
│  ┌──────┼──────────────────────────────────────────┐         │
│  ▼      ▼          ▼              ▼                 ▼         │
│ DuckDB  Futu     Yahoo/       Alpha           SQLite          │
│ Provider Provider yfinance    Vantage         Storage         │
│ (backtest) (live) (research)  (alt source)    (legacy)        │
└──────────────────────────────────────────────────────────────┘
```

---

## 4. Core Modules Detail

### 4.1 Engine (`core/engine.py`)

The `Engine` is the runtime hub for live/paper trading. It manages:
- **EventBus**: pub/sub backbone connecting all components
- **Portfolio**: real-time position and NAV tracking
- **RiskEngine**: pre-order risk checks (5 rules)
- **Scheduler**: market-aware time-based jobs (open/close/intraday/cron)
- **Strategies**: event-driven with `on_data`, `on_before_trading`, `on_after_trading` hooks

**Two execution paths exist:**

| Path | Entry | Use Case | Sophistication |
|------|-------|----------|----------------|
| Full system | `quant_system.py` → `Engine` | Live/paper trading, simple backtest | Basic date-iteration replay |
| Standalone backtest | `backtest_runner.py` → `Backtester` | Backtesting, walk-forward | T+1 fills, lot sizes, suspension, volume limits |

### 4.2 Backtester (`core/backtester.py`)

Production-grade backtest simulator with realistic execution:

- **T+1 fills**: orders submitted today are filled at tomorrow's open
- **Suspension handling**: zero-volume + unchanged close → skip trading
- **Lot-size enforcement**: rounds quantities to valid lot sizes per market
- **Volume participation**: max 5% of daily volume per order
- **Fill deferral**: suspended stocks deferred up to 5 days
- **Commission models**: US per-share ($0.005/share) and HK realistic (0.1% + fees)
- **Diagnostics**: tracks suspended days, volume-limited trades, lot adjustments, cost drag

### 4.3 Risk Engine (`core/risk.py`)

Pre-order risk checks with hard/soft limits:

| Check | Default Limit | Type |
|-------|---------------|------|
| Max position size | 5% NAV per symbol | Hard |
| Max sector exposure | 25% NAV per sector | Hard |
| Max daily loss | 2% of starting NAV | Hard |
| Max leverage | 1.5x | Hard |
| Order rate limit | 30 orders/minute | Soft |

### 4.4 Analytics (`core/analytics.py`)

Computes 15+ performance metrics:
- **Return metrics**: Total return, annualized return
- **Risk-adjusted**: Sharpe, Sortino, Calmar
- **Drawdown**: Max drawdown (value + %, peak/trough dates)
- **Trade metrics**: Win rate, profit factor, payoff ratio, expectancy
- **Advanced**: Ulcer Index, tail ratio, recovery factor, gain-to-pain ratio
- **Rolling**: Rolling Sharpe/Sortino (63-day default)
- **Statistical**: t-test with p-value, 95% confidence interval

### 4.5 Walk-Forward Engine (`core/walkforward.py`)

Rolling out-of-sample validation:
- **Default**: 6-month train, 1-month test, monthly step
- **Grid search**: exhaustive parameter optimization on training windows
- **Viability**: `avg_test_sharpe > 0.5` AND `sharpe_degradation < 0.5` AND `pct_profitable > 0.5`
- **Data quality**: validates prices, OHLC logic, same-close anomalies

### 4.6 Event System (`core/events.py`)

Thread-safe pub/sub with 12 event types:
`BAR`, `QUOTE`, `TRADE`, `ORDER_SUBMIT`, `ORDER_FILL`, `ORDER_CANCEL`, `ORDER_REJECT`, `POSITION_UPDATE`, `RISK_CHECK`, `MARKET_OPEN`, `MARKET_CLOSE`, `STRATEGY_SIGNAL`, `SYSTEM_SHUTDOWN`

---

## 5. Data Layer

### 5.1 Provider Architecture

All providers implement `DataProvider` ABC with: `connect()`, `disconnect()`, `is_connected()`, `get_bars()`, `get_quote()`.

| Provider | Purpose | Caching | Streaming |
|----------|---------|---------|-----------|
| `DuckDBProvider` | Backtesting (local DB) | N/A (direct read) | No |
| `FutuProvider` | HK/US live trading | No | Yes (quotes/klines/orderbook/trades) |
| `YfinanceProvider` | Research (with cache) | Parquet (24h TTL) | No |
| `YahooProvider` | Simple research | No | No |
| `AlphaVantageProvider` | Alternative source | No (25 req/day) | No |

### 5.2 Storage

| Backend | Use Case | Format |
|---------|----------|--------|
| `DuckDBStorage` | Primary (backtesting analytics) | Columnar DuckDB |
| `Storage` | Legacy (orders, trades, snapshots) | SQLite + Parquet |

**DuckDB table naming**: `{frequency}_{market}` → `daily_hk`, `daily_us`, `minute_hk`, `minute_us`

### 5.3 Symbol Convention

**Internal format**: `AAPL` (US), `00700` (HK stock), `HSI` (HK index)
**Futu format**: `US.AAPL`, `HK.00700`

The `SymbolRegistry` handles all translations. `DuckDBStorage` auto-routes based on prefix detection.

---

## 6. Strategy Layer

### 6.1 Strategy Lifecycle

```
on_start → on_before_trading → on_data → on_after_trading → on_stop
                ↑                                │
                └──── daily loop ────────────────┘
```

Each strategy receives a `Context` providing access to:
- `portfolio` — position and NAV queries
- `order_manager` — submit/cancel orders
- `risk_engine` — pre-flight risk checks
- `data_provider` — historical data access
- `event_bus` — event subscription

### 6.2 Alpha Framework (`strategies/framework.py`)

Signal-to-portfolio pipeline:

```
SignalGenerator (weighted factors) → PortfolioConstructor (sizing) → ExecutionScheduler (TWAP/VWAP)
```

- **Position sizing methods**: Equal weight, Kelly (quarter-Kelly default), Risk parity (inverse-volatility)
- **Execution**: TWAP order splitting (configurable interval/duration)

### 6.3 Factor Library (`strategies/factors.py`)

10 built-in factors with `FactorLibrary` factory and IC analysis:

| Factor | Purpose |
|--------|---------|
| `MomentumFactor` | N-day price momentum |
| `MeanReversionFactor` | Z-score deviation from SMA |
| `VolatilityFactor` | Annualized return volatility |
| `VolumeFactor` | Relative volume vs average |
| `RSIFactor` | Relative Strength Index |
| `MACDFactor` | MACD histogram |
| `BollingerBandFactor` | Band position [-1, +1] |
| `ATRFactor` | Average True Range |
| `VolatilityRegimeFactor` | VIX-based regime classification |
| `QualityFactor` | ROE + debt-to-equity composite |

### 6.4 Strategy Registry (`strategies/registry.py`)

- **Decorator**: `@strategy("name")` registers a class
- **Auto-discovery**: scans all subdirectories of `strategies/` for `strategy.py` files at import time
- **Usage**: `StrategyRegistry.create("SimpleMomentum", **params)`

### 6.5 Implemented Strategies

| Strategy | Registry Name | Priority | Rebalance | Core Logic |
|----------|---------------|----------|-----------|------------|
| SimpleMomentum | `"SimpleMomentum"` | 2 | Monthly (21d) | Cross-sectional: long top 10% momentum, short bottom 10% |
| CrossSectionalMeanReversion | `"CrossSectionalMeanReversion"` | 6 | Weekly (5d) | Excess returns vs SPY: long underperformers, short overperformers |
| VolatilityRegime | `"VolatilityRegime"` | 1 | Daily | VIX regime switching: momentum (bull), RSI mean-reversion (chop), defensive (bear) |

Each strategy resides in its own subdirectory with `strategy.py` + `config.yaml`.

---

## 7. Execution Layer

### 7.1 Order Flow

```
Strategy.buy()/sell()
    → OrderManager.submit_order()
        → RiskEngine.check_order() (5 checks)
        → BrokerAdapter.submit_order()
            → FillHandler.process_fill()
                → Portfolio.update_position()
                → EventBus.publish(ORDER_FILL)
                → Strategy.on_fill() callback
```

### 7.2 Portfolio Coordinator (`execution/portfolio_coordinator.py`)

Multi-strategy coordinator that:
- Maintains per-strategy position views and a combined portfolio view
- Implements priority-based risk budget allocation
- Enforces combined risk limits: strategy budget, portfolio leverage, symbol concentration (10% NAV max)

### 7.3 Broker Adapters

| Broker | Status | Markets | Features |
|--------|--------|---------|----------|
| `PaperBroker` | Production | All | Instant fill with slippage, cash ledger |
| `FutuBroker` | Production | HK, US | Live trading via OpenD, order modification, push notifications |
| `IBKRBroker` | Stub | - | Placeholder for Interactive Brokers |

---

## 8. CIO Module

The Chief Investment Officer module provides AI-driven market assessment and strategy weight allocation.

### 8.1 Pipeline

```
MarketIndicators → MarketAssessor (VIX regime) ─┐
                                                  ├→ CIOEngine → {regime, score, weights, sentiment}
News Text → NewsAnalyzer (LLM sentiment) ─────────┘
                                                  │
                                          WeightAllocator (regime → weights)
```

### 8.2 Regime Detection

| Regime | VIX SMA | Behavior |
|--------|---------|----------|
| Low Vol Bull | < 15 | Favor momentum strategies |
| Medium Vol Chop | 15–25 | Favor mean reversion |
| High Vol Bear | > 25 | Defensive, favor volatility regime strategy |

### 8.3 LLM Adapters

| Adapter | Backend | Default Model |
|---------|---------|---------------|
| `OpenAIAdapter` | OpenAI API | gpt-4o-mini |
| `ClaudeAdapter` | Anthropic API | claude-3-haiku |
| `OllamaAdapter` | Local Ollama | llama3.2 |

All adapters return `{sentiment, confidence, summary}` and fall back to neutral on failure.

---

## 9. Frontend Dashboard

### 9.1 Tech Stack

- **React 18** with Create React App
- **axios** for API calls
- **react-markdown** for strategy documentation rendering
- **SVG-based charts** (equity curve, drawdown, P&L)

### 9.2 Pages & Components

| Component | Purpose |
|-----------|---------|
| `App.js` | Root layout, broker selector, start/stop, tab navigation |
| `BacktestDashboard.js` | Strategy selection, parameter form, async backtest execution, equity/drawdown charts, trade table |
| `LiveTradingPage.js` | Live trading container: positions + strategies + orders |
| `PositionOverview.js` | Portfolio NAV cards, holdings table, strategy NAV distribution |
| `StrategyManagement.js` | Full lifecycle: Active → Pause → Retire → Delete, CIO panel |
| `StrategyDetailModal.js` | Strategy README + live performance metrics |
| `CIOAssessmentPanel.js` | Market regime, score, sentiment, LLM summary |
| `StrategyWeightBar.js` | Visual weight allocation bar |

### 9.3 API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/status` | System status, portfolio, strategies, positions |
| GET | `/api/strategies` | List all strategies with metrics |
| GET | `/api/strategies/<id>/parameters` | Strategy parameter definitions |
| POST | `/api/strategies/select` | Select active strategy |
| POST | `/api/strategies/<id>/pause` | Pause strategy (optional flatten) |
| POST | `/api/strategies/<id>/resume` | Resume strategy |
| POST | `/api/strategies/<id>/retire` | Retire strategy |
| DELETE | `/api/strategies/<id>` | Permanently delete (retired only) |
| POST | `/api/backtest/run` | Launch async backtest → `backtest_id` |
| GET | `/api/backtest/result/<id>` | Poll backtest result |
| GET | `/api/portfolio` | Portfolio summary |
| GET | `/api/orders` | Recent orders |
| GET | `/api/cio/assessment` | CIO market assessment |
| POST | `/api/cio/refresh` | Refresh CIO assessment |
| GET | `/api/strategy-pool` | Strategy weights + capital allocation |

---

## 10. Configuration

### 10.1 Config Files

| File | Purpose |
|------|---------|
| `config/config.yaml` | Master config: mode, markets, data, execution, risk, scheduler |
| `config/brokers.yaml` | Broker credentials and connection settings (gitignored) |
| `config/strategies.yaml` | Strategy parameters and enable/priority settings |
| `cio/config/cio_config.yaml` | CIO VIX thresholds, LLM settings, weight allocation |

### 10.2 Key Config Parameters

**Risk limits** (`config.yaml` → `risk`):
- `max_position_pct`: 5% per symbol
- `max_sector_pct`: 25% per sector
- `max_daily_loss_pct`: 2% of NAV
- `max_leverage`: 1.5x
- `max_orders_minute`: 30

**Markets** (`config.yaml` → `markets`):
- US: 9:30–16:00 ET (America/New_York)
- HK: 9:30–16:00 HKT (Asia/Hong_Kong)

**Execution** (`config.yaml` → `execution`):
- Default broker: paper
- Slippage: 5 bps
- US commission: $0.005/share
- HK commission: 0.1% + statutory fees

---

## 11. Scripts

| Script | Purpose |
|--------|---------|
| `scripts/prepare_data.py` | Futu OpenD → DuckDB data pipeline. Supports incremental updates, CLI args for market/symbols/date range |
| `scripts/futu_backtest_demo.py` | End-to-end demo: ensure data → run Tencent SMA crossover → export results |
| `scripts/run_backtest_test.py` | Quick integration test: SimpleMomentum on 5 HK stocks |
| `scripts/profile_backtest.py` | Performance profiler for backtest pipeline bottlenecks |
| `scripts/profile_vr.py` | VolatilityRegime-specific profiler for `get_bars()` call patterns |

---

## 12. Data Flow Summary

### 12.1 Live/Paper Trading

```
Scheduler fires market event
  → Engine dispatches BAR event via EventBus
    → Strategy.on_data() generates signals
      → Strategy.buy()/sell() → OrderManager
        → RiskEngine checks → Broker executes
          → FillHandler reconciles → Portfolio updates
            → EventBus broadcasts FILL event
```

### 12.2 Backtesting

```
backtest_runner.py CLI
  → DuckDBProvider loads historical data
    → _DataFrameProvider builds O(1) bar index
      → Backtester iterates trading days:
          on_before_trading → on_data → collect orders
          → T+1 fill at next open (with lot size, volume limits)
          → Portfolio mark-to-market
        → analytics.py computes PerformanceMetrics
          → Export CSV/JSON
```

### 12.3 Walk-Forward Validation

```
WalkForwardEngine
  → For each time window:
      Grid search params on training period
        → Select best Sharpe params
          → Evaluate on test period with best params
            → Collect per-window WFWindowResult
              → Aggregate: consistency, degradation, viability
```

---

## 13. Startup

### Production (Windows)
```bat
start_ui.bat
# Starts api_server.py → opens http://localhost:5000 (serves built frontend)
```

### Development (macOS/Linux)
```bash
./start_ui.sh
# Starts api_server.py (port 5000) + React dev server (port 3000) with hot-reload
```

### CLI Backtest
```bash
python system/quant/backtest_runner.py \
  --strategy SimpleMomentum \
  --start 2024-01-01 --end 2024-12-31 \
  --symbols HK.00700 HK.09988 \
  --initial-cash 1000000
```

### Data Preparation
```bash
python scripts/prepare_data.py --market hk --start 2020-01-01 --end 2025-01-01
```
