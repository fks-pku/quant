# Quant Trading System — Product Requirements Document

## 1. Concept & Vision

A **modular, extensible quantitative trading framework** designed for personal PC deployment. The system prioritizes stability and efficiency over complexity — a solid foundation where strategies, data sources, and execution venues can be plugged in or swapped out without touching the core engine. The user will start with basic market data and paper trading, then add strategies and broker integrations as needed.

**Design philosophy:** Convention over configuration. Sensible defaults with every knob exposed. Production-grade reliability at hobbyist scale.

---

## 2. System Architecture

### 2.1 High-Level Layout

```
┌─────────────────────────────────────────────────────────────────┐
│                        ENTRY POINT                               │
│                     quant_system.py                              │
│              (CLI + daemon mode, config driven)                  │
└──────────────────────┬──────────────────────────────────────────┘
                       │
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│    CORE     │ │   DATA      │ │  EXECUTION  │
│   ENGINE    │ │   LAYER     │ │   LAYER     │
│             │ │             │ │             │
│ - Scheduler │ │ - Providers │ │ - Brokers   │
│ - Portfolio │ │ - Storage   │ │ - Orders    │
│ - Risk      │ │ - Normalize │ │ - Fills     │
│ - Reporting │ │ - Cache     │ │ - Position  │
└─────────────┘ └─────────────┘ └─────────────┘
         │             │             │
         └─────────────┼─────────────┘
                       ▼
              ┌─────────────────┐
              │    STRATEGY    │
              │     LAYER      │
              │                │
              │ - Alpha Engine  │
              │ - Signal Gen    │
              │ - Portfolio     │
              │   Construction  │
              └─────────────────┘
```

### 2.2 Directory Structure

```
quant/
├── quant_system.py          # CLI entry point
├── config/
│   ├── config.yaml          # Main configuration
│   ├── brokers.yaml         # Broker credentials (gitignored)
│   └── strategies.yaml      # Strategy parameters
├── core/
│   ├── __init__.py
│   ├── engine.py            # Main event loop & orchestration
│   ├── scheduler.py          # Time-based scheduling
│   ├── portfolio.py          # Position & NAV tracking
│   ├── risk.py              # Risk checks & limits
│   └── events.py            # Internal event bus
├── data/
│   ├── __init__.py
│   ├── providers/           # Pluggable data providers
│   │   ├── base.py          # Abstract provider interface
│   │   ├── yahoo.py         # Yahoo Finance adapter
│   │   ├── alpha_vantage.py # Alpha Vantage adapter
│   │   └── futu.py          # Futu OpenAPI adapter
│   ├── storage.py           # SQLite/Parquet persistence
│   └── normalizer.py        # Unified data schemas
├── execution/
│   ├── __init__.py
│   ├── brokers/             # Pluggable broker adapters
│   │   ├── base.py          # Abstract broker interface
│   │   ├── ibkr.py          # Interactive Brokers adapter
│   │   └── futu.py          # Futu adapter
│   ├── order_manager.py     # Order lifecycle management
│   └── fill_handler.py      # Fill processing & reconciliation
├── strategies/
│   ├── __init__.py
│   ├── base.py              # Strategy abstract base class
│   ├── framework.py         # Alpha signal framework
│   └── examples/            # Example strategies
├── utils/
│   ├── __init__.py
│   ├── logger.py            # Structured logging
│   ├── config_loader.py     # YAML config loading
│   └── datetime_utils.py    # Timezone & market hours
├── prd/                     # (this document)
└── tests/
    ├── test_core.py
    ├── test_data.py
    └── test_execution.py
```

---

## 3. Core Engine

### 3.1 Event Loop

- **Mode A — Live:** Scheduled jobs trigger on real-time clock (configurable resolution: 1s / 1m / 5m / 1h / EOD).
- **Mode B — Backtest:** Historical data replay with configurable speed (1x / 10x / 100x / end-of-day).
- **Mode C — Paper:** Live data + simulated execution (no real orders).
- Events flow through an internal **EventBus** (pub/sub). All components communicate via events; no direct cross-module calls.

### 3.2 Scheduler

| Job Type       | Trigger                              |
|----------------|--------------------------------------|
| `market_open`  | Configurable offset from exchange open |
| `intraday`     | Fixed intervals (1m / 5m / 15m)      |
| `market_close` | Configurable offset before close      |
| `scheduled`    | Cron-like expressions (e.g. `0 9,15 * * 1-5`) |

### 3.3 Portfolio Tracker

- Tracks positions in-memory per session
- Persists to SQLite end-of-day (or on demand)
- Computes: unrealized P&L, realized P&L, margin used, NAV
- Supports multi-asset, multi-currency (with FX conversion)

### 3.4 Risk Engine

Risk checks run before every order submission:

| Check              | Default Limit                        |
|--------------------|--------------------------------------|
| Max position size  | 5% of NAV per symbol                 |
| Max sector exposure | 25% of NAV per sector              |
| Max daily loss     | 2% of starting NAV                   |
| Max leverage       | 1.5x                                 |
| Max orders/minute  | 30                                   |

Breaching a hard limit → order rejected. Soft limits → logged warning.

---

## 4. Data Layer

### 4.1 Provider Interface (Abstract)

```python
class DataProvider(ABC):
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def is_connected(self) -> bool: ...
    def get_bars(self, symbol: str, start: datetime, end: datetime,
                  timeframe: str) -> pd.DataFrame: ...
    def get_quote(self, symbol: str) -> dict: ...
    def subscribe(self, symbols: list[str], callback: Callable): ...
    def unsubscribe(self, symbols: list[str]): ...
```

### 4.2 Initial Providers

| Provider       | Data Type           | Notes                          |
|----------------|---------------------|--------------------------------|
| Yahoo Finance  | EOD, 1m, 5m, 15m, 1h | Free; rate-limited             |
| Alpha Vantage  | EOD, intraday        | Free tier: 25 req/day           |
| Futu           | Real-time           | Requires Futu OpenAPI account  |

### 4.3 Storage

- **SQLite** for daily bars, trade history, order log, portfolio snapshots
- **Parquet** for high-frequency intraday data (optional, for backtesting)
- Schema versioning via migrations directory (`migrations/`)

### 4.4 Normalizer

All provider data is normalized to a standard schema:

```python
Bar:   timestamp, symbol, open, high, low, close, volume
Quote: timestamp, symbol, bid, ask, bid_size, ask_size
Trade: timestamp, symbol, price, size, exchange
```

---

## 5. Execution Layer

### 5.1 Broker Interface (Abstract)

```python
class BrokerAdapter(ABC):
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def is_connected(self) -> bool: ...
    def submit_order(self, order: Order) -> str: ...  # returns order_id
    def cancel_order(self, order_id: str) -> bool: ...
    def get_positions(self) -> list[Position]: ...
    def get_account_info(self) -> AccountInfo: ...
```

### 5.2 Initial Adapters

| Broker  | Status       | Notes                        |
|---------|--------------|------------------------------|
| IBKR    | Planned      | Via `ib_insync` or `IBridgePy` |
| Futu    | Planned      | Via Futu OpenAPI             |
| Alpaca  | Planned      | Via REST + WebSocket         |
| Paper   | **Included** | Internal simulated broker    |

### 5.3 Order Manager

- Routes orders to the appropriate broker adapter based on symbol/market
- Maintains order state machine: `pending → submitted → filled / partial / cancelled / rejected`
- Retries on transient failures with exponential backoff (max 3 attempts)
- Full audit log to SQLite

### 5.4 Fill Handler

- Reconciles broker fills with local order state
- Updates portfolio positions
- Triggers strategy callbacks on fill events

---

## 6. Strategy Layer

### 6.1 Base Strategy Interface

```python
class Strategy(ABC, LoggerMixin):
    @property
    def name(self) -> str: ...

    def on_start(self, context: Context): ...
    def on_data(self, context: Context, data: Bar | Quote): ...
    def on_fill(self, context: Context, fill: Fill): ...
    def on_order_rejected(self, context: Context, order: Order, reason: str): ...
    def on_stop(self, context: Context): ...
```

### 6.2 Alpha Framework

Provides helper classes for common patterns:

- **SignalGenerator** — Combines multiple alpha factors into a signal score
- **PortfolioConstructor** — Converts signals to target weights (equal-weight, Kelly criterion, risk-parity)
- **ExecutionScheduler** — Splits large orders into child orders using TWAP/VWAP

### 6.3 Built-in Example Strategies

1. **MomentumEOD** — Buy top-5 S&P 500 gainers at open, sell at close (educational)
2. **MeanReversion1m** — RSI-based mean reversion on 1-minute data (educational)
3. **DualThrust** — Classic break-out system adapted for futures (reference implementation)

---

## 7. Configuration

### 7.1 Main Config (`config/config.yaml`)

```yaml
system:
  mode: paper          # live | paper | backtest
  log_level: INFO
  data_dir: ./data
  start_date: "2025-01-01"
  end_date: "2025-12-31"

markets:
  US:
    timezone: America/New_York
    open_hour: 9
    open_minute: 30
    close_hour: 16
    close_minute: 0
  HK:
    timezone: Asia/Hong_Kong
    open_hour: 9
    open_minute: 30
    close_hour: 16
    close_minute: 0

data:
  providers:
    - yahoo
    - futu
  default_timeframe: 5m

execution:
  brokers:
    - paper
  default_order_type: MARKET
  slippage_bps: 5

risk:
  max_position_pct: 0.05
  max_sector_pct: 0.25
  max_daily_loss_pct: 0.02
  max_leverage: 1.5

scheduler:
  jobs:
    - name: open_rebalance
      trigger: market_open
      offset_minutes: 5
    - name: intraday_check
      trigger: intraday
      interval_minutes: 15
    - name: close_exit
      trigger: market_close
      offset_minutes: 10
```

### 7.2 Broker Config (`config/brokers.yaml`)

Stored separately, gitignored. Contains API keys and credentials per broker.

---

## 8. Error Handling & Resilience

| Scenario                    | Behavior                                              |
|-----------------------------|-------------------------------------------------------|
| Data provider disconnects   | Retry with backoff; use cached data if available      |
| Broker disconnects          | Queue orders locally; reconnect automatically         |
| Order rejected              | Log reason; notify via callback; continue strategy    |
| Daily loss limit breached   | Close all positions; halt intraday strategies         |
| System crash / restart      | Resume from last persisted state; reconcile positions |

---

## 9. Performance Targets (Personal PC)

| Metric                    | Target                              |
|---------------------------|-------------------------------------|
| Memory footprint (idle)   | < 200 MB                            |
| Memory (moderate load)    | < 1 GB                              |
| CPU (live, 100 symbols)   | < 15% on 4-core PC                  |
| Order latency (submit)    | < 50ms to broker API                |
| Data throughput           | 10,000 bars/sec ingestion           |
| Startup time              | < 5 seconds                         |

---

## 10. Testing Strategy

| Layer        | Test Type                | Coverage Target |
|--------------|--------------------------|-----------------|
| Core engine  | Unit tests (pytest)      | > 80%           |
| Data layer   | Provider mock + live test| Each provider   |
| Execution    | Paper trading regression | 100% order types|
| Strategies   | Backtest vs known signal | Historical data |
| Integration  | Full pipeline E2E        | Weekly run      |

---

## 11. Expandability Map

```
Phase 1 (This PRD)
  └─ Core engine, paper broker, Yahoo/Alpha Vantage, 1 example strategy

Phase 2 (Future)
  ├─ IBKR adapter + live trading
  ├─ Futu adapter (HK equities)
  ├─ SQLite → PostgreSQL migration path
  ├─ Parquet storage for HFT data
  └─ Strategy: Mean Reversion

Phase 3 (Future)
  ├─ Options module (Greeks, option chains)
  ├─ Multi-broker smart routing
  ├─ Real-time risk dashboard (Flask/Plotly)
  ├─ Strategy: Statistical arbitrage
  └─ Machine learning factor library

Phase 4 (Future)
  ├─ Cloud deployment option (Docker)
  ├─ Web UI for monitoring
  ├─ Strategy: Market making
  └─ GPU acceleration for factor computation
```

---

## 12. Non-Goals (Out of Scope for Phase 1)

- High-frequency trading (< 1 second latency)
- Direct market access (DMA) / co-location
- Multi-accounts / multi-strategy money management
- Options or derivatives pricing
- Regulatory compliance automation (KYC, reporting)
- Mobile UI

---

## 13. Definition of Done (Phase 1)

- [ ] System starts with `python quant_system.py --mode paper`
- [ ] Yahoo Finance data loads for US equities (EOD + intraday)
- [ ] Futu adapter connects and streams HK equity quotes
- [ ] Paper broker executes simulated orders with fills
- [ ] Portfolio tracker shows live P&L
- [ ] Risk engine rejects orders exceeding limits
- [ ] One example strategy runs end-to-end (signal → order → fill → P&L)
- [ ] All configuration loaded from YAML (no hardcoding)
- [ ] Unit tests pass for core modules
- [ ] Runs on Windows and Linux (WSL)