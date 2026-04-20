# Quant Trading System - Technical Manual

> **Version:** Latest (post-refactor)  
> **Date:** 2026-04-20  
> **Test Status:** 157 passed, 3 skipped, 0 failed

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture](#2-architecture)
3. [Backend API Reference](#3-backend-api-reference)
4. [Core Engine](#4-core-engine)
5. [Data Layer](#5-data-layer)
6. [Execution Layer](#6-execution-layer)
7. [Strategy Framework](#7-strategy-framework)
8. [CIO Module](#8-cio-module)
9. [Models](#9-models)
10. [Frontend](#10-frontend)
11. [Configuration](#11-configuration)
12. [Deployment](#12-deployment)

---

## 1. System Overview

### 1.1 What is This?

A full-stack quantitative trading system for US and HK equities supporting:

- **Live trading** via broker adapters (Futu, IBKR, Alpaca)
- **Paper trading** with simulated execution
- **Backtesting** with realistic fills (T+1, lot sizes, commissions, slippage)
- **Walk-forward analysis** with 6-month train / 1-month test windows
- **Multi-strategy coordination** with CIO market assessment

### 1.2 Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+, Flask |
| Frontend | React 18, custom SVG charts |
| Storage | DuckDB (primary), SQLite (legacy) |
| Data Providers | Futu OpenD, Yahoo Finance, Alpha Vantage |
| Brokers | Futu, Interactive Brokers, Alpaca, Paper |

### 1.3 Project Structure

```
quant/
├── api_server.py              # Flask orchestrator (44 lines)
├── quant/
│   ├── api/                   # Flask blueprints (new)
│   │   ├── state.py          # Shared mutable state
│   │   ├── system_bp.py      # /api/status, /api/start, /api/stop
│   │   ├── strategies_bp.py  # /api/strategies/*
│   │   ├── backtest_bp.py    # /api/backtest/*
│   │   ├── cio_bp.py         # /api/cio/*, /api/strategy-pool/*
│   │   ├── futu_bp.py        # /api/futu/*
│   │   └── positions_bp.py   # /api/portfolio, /api/orders, etc.
│   ├── core/                 # Engine, backtester, events, portfolio, risk
│   ├── data/                 # Providers, DuckDB storage, normalizer
│   ├── execution/            # Order management, fill handling, brokers
│   ├── strategies/           # Strategy implementations
│   ├── cio/                  # CIO market assessment
│   ├── models/               # Dataclasses (Order, Position, Trade, etc.)
│   └── utils/                # Logger, datetime, config loader
├── frontend/                 # React SPA
├── scripts/                  # Data pipelines, demos
└── docs/                     # Documentation
```

---

## 2. Architecture

### 2.1 High-Level Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Frontend  │────▶│   Flask API │────▶│    Core     │
│  (React 18) │◀────│   (7 BPs)   │◀────│   Engine    │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                                │
                       ┌────────────────────────┼────────────────────────┐
                       ▼                        ▼                        ▼
               ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
               │ Data Providers│      │   Brokers    │      │  Strategies  │
               │ (Futu, Yahoo) │      │ (Futu, Paper)│      │ (3 impl)     │
               └──────────────┘      └──────────────┘      └──────────────┘
```

### 2.2 Two Execution Paths

| Path | Entry Point | Use Case |
|------|-------------|----------|
| **Live/Paper** | `quant_system.py` → `Engine` | Real-time trading with event loop |
| **Backtest** | `backtest_runner.py` → `Backtester` | Historical simulation with realistic execution |

### 2.3 Event System

The system uses a pub/sub event bus with 17 event types:

```python
BAR, QUOTE, TRADE                    # Market data
ORDER_SUBMIT, ORDER_FILL, ORDER_CANCEL, ORDER_REJECT  # Order lifecycle
POSITION_UPDATE, RISK_CHECK          # Portfolio
MARKET_OPEN, MARKET_CLOSE            # Session
STRATEGY_SIGNAL, SYSTEM_SHUTDOWN     # System
RESEARCH_SEARCH_DONE, RESEARCH_IDEA_SCORED, RESEARCH_CODE_READY,  # Research
RESEARCH_REPORT_DONE, RESEARCH_ERROR
```

---

## 3. Backend API Reference

### 3.1 Flask Blueprint Structure

The API is organized into 6 blueprints, all registered in `api_server.py`:

```python
app.register_blueprint(system_bp)      # System control
app.register_blueprint(strategies_bp)  # Strategy management
app.register_blueprint(backtest_bp)    # Backtesting
app.register_blueprint(cio_bp)         # CIO assessment
app.register_blueprint(futu_bp)        # Futu broker
app.register_blueprint(positions_bp)   # Portfolio & orders
```

### 3.2 API Endpoints

#### System Control (`system_bp`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | System status, portfolio, strategies, positions |
| POST | `/api/start` | Start system (paper/live mode) |
| POST | `/api/stop` | Stop system |
| GET | `/api/logs` | Get logs (currently empty placeholder) |

**GET /api/status Response:**
```json
{
  "status": "running|stopped|starting|stopping",
  "portfolio": {"nav": 100000.0, "total_unrealized_pnl": 0.0, "total_realized_pnl": 0.0},
  "strategies": [...],
  "positions": [...],
  "selected_strategy": "VolatilityRegime"
}
```

#### Strategy Management (`strategies_bp`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/strategies` | List all strategies with metadata |
| GET | `/api/strategies/<id>` | Get single strategy |
| GET | `/api/strategies/docs/<id>` | Get strategy documentation (markdown) |
| GET | `/api/strategies/backtest/<id>` | Get backtest summary |
| GET | `/api/strategies/performance/<id>` | Get live performance |
| POST | `/api/strategies/select` | Set active strategy |
| POST | `/api/strategies/<id>/pause` | Pause strategy (optional flatten) |
| POST | `/api/strategies/<id>/resume` | Resume paused strategy |
| POST | `/api/strategies/<id>/retire` | Retire strategy |
| POST | `/api/strategies/<id>/restore` | Restore retired to paused |
| DELETE | `/api/strategies/<id>` | Permanently delete retired strategy |
| GET | `/api/strategies/<id>/parameters` | Get parameter definitions |
| GET | `/api/strategies/<id>/readme` | Get strategy README |

#### Backtest (`backtest_bp`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/backtest/run` | Start async backtest |
| GET | `/api/backtest/result/<id>` | Poll for result |
| GET | `/api/backtest/list` | List all backtests |

**POST /api/backtest/run Request:**
```json
{
  "strategy_id": "SimpleMomentum",
  "start_date": "2020-01-01",
  "end_date": "2024-12-31",
  "symbols": ["AAPL", "MSFT", "GOOGL"],
  "initial_cash": 100000,
  "slippage_bps": 5,
  "strategy_params": {"lookback_days": 20}
}
```

**Response:**
```json
{"backtest_id": "abc12345", "status": "running"}
```

#### CIO (`cio_bp`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/cio/assessment` | Get CIO market assessment |
| POST | `/api/cio/refresh` | Refresh with optional news text |
| GET | `/api/strategy-pool` | Get strategy pool with weights |
| POST | `/api/strategy-pool/weights` | Update manual weights |

**GET /api/cio/assessment Response:**
```json
{
  "environment": "Low Vol Bull|Medium Volatility|High Vol Bear",
  "score": 50,
  "sentiment": "neutral|bullish|bearish",
  "vix": 14.5,
  "trend_strength": 0.72,
  "weights": {"volatility_regime": 0.30, ...},
  "summary": "LLM-generated assessment text"
}
```

#### Futu Broker (`futu_bp`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/futu/connect` | Connect to Futu OpenD |
| POST | `/api/futu/disconnect` | Disconnect |
| POST | `/api/futu/unlock` | Unlock trading |
| GET | `/api/futu/status` | Connection & unlock status |
| GET | `/api/futu/account` | Account details |
| GET | `/api/futu/positions` | Positions with strategy breakdown |
| GET | `/api/futu/orders` | Orders + today's deals |

#### Portfolio & Orders (`positions_bp`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/portfolio` | Portfolio summary |
| POST | `/api/orders` | Create order (mock) |
| GET | `/api/orders` | Get recent orders |
| GET | `/api/market` | Mock market data |
| GET | `/api/data/symbols` | Available symbols in DuckDB |
| GET | `/api/strategy-positions` | Per-strategy position breakdown |
| GET | `/api/strategy/<name>/history` | Strategy NAV history |
| GET | `/api/strategy/all-history` | All strategies history |

---

## 4. Core Engine

### 4.1 Engine (`quant/core/engine.py`, 234 lines)

The main event loop for live/paper trading.

```python
class Engine:
    def __init__(self, config: Dict[str, Any])
    def set_data_provider(self, name: str, provider: Any) -> None
    def set_broker(self, broker: Any) -> None
    def set_order_manager(self, order_manager: Any) -> None
    def add_strategy(self, strategy: Any) -> None
    def subscribe(self, symbols: List[str]) -> None
    def start(self) -> None
    def stop(self) -> None
    def run_backtest(self, start_date: datetime, end_date: datetime, speed: str) -> None
    def get_portfolio_status(self) -> Dict[str, Any]
```

**Context** (passed to strategies):
```python
@dataclass
class Context:
    portfolio: Portfolio
    risk_engine: RiskEngine
    event_bus: EventBus
    order_manager: Any
    data_provider: Any
    broker: Any
```

### 4.2 Backtester (`quant/core/backtester.py`, 481 lines)

Sophisticated backtester with realistic execution:

```python
class Backtester:
    def __init__(self, config: Dict, event_bus: Optional[EventBus] = None, lot_sizes: Optional[Dict] = None)
    
    def run(self, start: datetime, end: datetime, strategies: List[Any], 
            initial_cash: float = 100000, data_provider: Any = None, 
            symbols: Optional[List[str]] = None) -> BacktestResult
```

**Realistic Execution Features:**
- **T+1 settlement** (orders deferred to next trading day)
- **Lot sizes** (HK stocks in board lots)
- **Suspension handling** (zero volume = no fill)
- **Volume participation limits** (max 5% of daily volume)
- **Commission models**:
  - US: $0.005/share, min $1
  - HK: Full breakdown (commission 0.03%, stamp duty 0.13%, SFC levy 0.00278%, clearing 0.002%, trading fee 0.005%)
- **Slippage** (configurable bps, directional: adds to BUY, subtracts from SELL)

**Diagnostics** (`BacktestDiagnostics`):
```python
suspended_days: int          # Days orders were suspended
volume_limited_trades: int   # Trades reduced due to volume limits
lot_adjusted_trades: int     # Trades adjusted to lot sizes
total_fill_delay_days: int   # Total deferral days
fill_count: int
total_commission: float
total_gross_pnl: float
avg_fill_delay_days: float
cost_drag_pct: float         # Commission as % of gross P&L
```

### 4.3 Scheduler (`quant/core/scheduler.py`, 217 lines)

Time-based job scheduler using cron expressions:

```python
class Scheduler:
    def add_job(self, name, trigger, callback, interval_minutes=None, 
                offset_minutes=0, cron_expression=None, market="all")
    
# JobType: MARKET_OPEN, MARKET_CLOSE, INTRADAY, SCHEDULED
```

### 4.4 Portfolio (`quant/core/portfolio.py`, 190 lines)

Position tracking with thread-safe updates:

```python
class Portfolio:
    def __init__(self, initial_cash: float = 100000.0, currency: str = "USD")
    
    @property
    def nav(self) -> float                    # Net Asset Value
    @property
    def starting_nav(self) -> float           # For daily loss tracking
    @property
    def total_unrealized_pnl(self) -> float
    @property
    def total_realized_pnl(self) -> float
    @property
    def margin_used(self) -> float            # 50% of position value
    
    def update_position(self, symbol: str, quantity: float, price: float, 
                        cost: float, sector: Optional[str] = None)
    def close_position(self, symbol: str, price: float) -> float  # Returns realized P&L
    def get_position(self, symbol: str) -> Optional[Position]
    def get_sector_exposure(self) -> Dict[str, float]
    def check_daily_loss(self, limit_pct: float) -> bool
    def reset_daily(self) -> None
```

### 4.5 Risk Engine (`quant/core/risk.py`, 184 lines)

Pre-order risk checks:

```python
class RiskEngine:
    def check_order(self, symbol: str, quantity: float, price: float, 
                    order_value: float, sector: Optional[str] = None) -> Tuple[bool, List[RiskCheckResult]]
    
# Risk Checks:
# - Position size (max 5% of NAV per symbol)
# - Sector exposure (max 25% of NAV per sector)
# - Daily loss (max 2% of starting NAV)
# - Leverage (max 1.5x)
# - Order rate (max 30/minute)
```

### 4.6 Analytics (`quant/core/analytics.py`, 370 lines)

Performance metrics calculation:

```python
# Core metrics
calculate_sharpe(returns, periods_per_year=252) -> float
calculate_sortino(returns, periods_per_year=252) -> float
calculate_max_drawdown(equity_curve) -> Tuple[float, float, datetime, datetime]

# Trade metrics
calculate_win_rate(trades) -> float
calculate_profit_factor(trades) -> float
calculate_payoff_ratio(trades) -> float
calculate_expectancy(trades) -> float

# Advanced
calculate_ulcer_index(equity_curve, periods=14) -> float
calculate_calmar(returns, max_dd, periods_per_year=252) -> float
calculate_tail_ratio(returns) -> float

# Aggregate
@dataclass
class PerformanceMetrics:
    total_return: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    avg_trade_duration: timedelta
    calmar_ratio: float
    payoff_ratio: float
    expectancy: float
    total_trades: int
    ulcer_index: float
    tail_ratio: float
    # ... plus 6 more fields
```

### 4.7 Walk-Forward Engine (`quant/core/walkforward.py`, 351 lines)

Out-of-sample validation:

```python
class WalkForwardEngine:
    def __init__(self, train_window_days=126, test_window_days=21, step_days=21)
    
    def run(self, strategy_factory, data: pd.DataFrame, param_grid: Dict, 
            initial_cash=100000, config=None) -> WFResult
```

**DataFrameProvider** (for backtesting):
```python
class DataFrameProvider:
    def __init__(self, data: pd.DataFrame)
    def get_bar_for_date(self, symbol: str, date) -> Optional[Dict]  # O(1) lookup
    def validate(self) -> List[str]  # Data quality checks
```

---

## 5. Data Layer

### 5.1 Data Provider Base (`quant/data/providers/base.py`, 63 lines)

```python
class DataProvider(ABC):
    @abstractmethod
    def connect(self) -> None
    @abstractmethod
    def disconnect(self) -> None
    @abstractmethod
    def is_connected(self) -> bool
    @abstractmethod
    def get_bars(self, symbol: str, start: datetime, end: datetime, timeframe: str) -> pd.DataFrame
    @abstractmethod
    def get_quote(self, symbol: str) -> dict
```

### 5.2 Providers

| Provider | File | Features |
|----------|------|----------|
| **FutuProvider** | `providers/futu.py` (620 lines) | Full Futu OpenAPI, subscriptions, market depth |
| **YfinanceProvider** | `providers/yfinance_provider.py` (150 lines) | Caching, rate limiting, parquet storage |
| **AlphaVantageProvider** | `providers/alpha_vantage.py` (160 lines) | REST API, 25 req/day limit |
| **DuckDBProvider** | `providers/duckdb_provider.py` (71 lines) | Primary backtesting source |
| **YahooProvider** | `providers/yahoo.py` (128 lines) | Simple yfinance wrapper (legacy) |

### 5.3 DuckDB Storage (`quant/data/storage_duckdb.py`, 370 lines)

**Table Schemas:**

```sql
-- Bar data (daily_hk, daily_us, minute_hk, minute_us)
CREATE TABLE {frequency}_{market} (
    timestamp TIMESTAMP,
    symbol VARCHAR,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume BIGINT,
    turnover DOUBLE
);
CREATE UNIQUE INDEX ON {table} (timestamp, symbol);

-- Orders
CREATE TABLE orders (
    order_id VARCHAR PRIMARY KEY,
    timestamp TIMESTAMP,
    symbol VARCHAR,
    quantity DOUBLE,
    side VARCHAR,
    order_type VARCHAR,
    price DOUBLE,
    status VARCHAR,
    filled_quantity DOUBLE DEFAULT 0,
    avg_fill_price DOUBLE,
    broker VARCHAR
);

-- Trades
CREATE TABLE trades (
    timestamp TIMESTAMP,
    symbol VARCHAR,
    price DOUBLE,
    size DOUBLE,
    side VARCHAR,
    order_id VARCHAR
);

-- Portfolio snapshots
CREATE TABLE portfolio_snapshots (
    timestamp TIMESTAMP,
    total_value DOUBLE,
    cash DOUBLE,
    positions_value DOUBLE,
    unrealized_pnl DOUBLE,
    realized_pnl DOUBLE,
    margin_used DOUBLE
);

-- Instrument metadata
CREATE TABLE instrument_meta (
    symbol VARCHAR PRIMARY KEY,
    lot_size INTEGER DEFAULT 100,
    market VARCHAR DEFAULT 'HK',
    name VARCHAR DEFAULT ''
);

-- Strategy snapshots
CREATE TABLE strategy_snapshots (
    date VARCHAR,
    strategy_name VARCHAR,
    nav DOUBLE,
    market_value DOUBLE,
    cash DOUBLE,
    unrealized_pnl DOUBLE,
    realized_pnl DOUBLE
);
```

### 5.4 Symbol Registry (`quant/data/symbol_registry.py`, 131 lines)

```python
class Market(Enum):
    US = "US"
    HK = "HK"

class SymbolRegistry:
    def format_for_provider(self, symbol: str, provider: str) -> str
    def detect_market(self, symbol: str) -> Market
    def normalize(self, external_symbol: str, source: str) -> str
    def is_tradable(self, symbol: str, market: Market) -> bool
```

**Symbol Formats:**
- Internal: `AAPL`, `00700` (no prefix)
- Futu: `US.AAPL`, `HK.00700`
- Yahoo/Alpha: Same as internal

### 5.5 Normalizer (`quant/data/normalizer.py`, 182 lines)

Standardizes data from different providers:

```python
@dataclass
class Bar:
    timestamp: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int

@dataclass
class Quote:
    timestamp: datetime
    symbol: str
    bid: float
    ask: float
    bid_size: int
    ask_size: int

@dataclass
class MarketTrade:  # Renamed from Trade to avoid conflict
    timestamp: datetime
    symbol: str
    price: float
    size: float
    exchange: str

class Normalizer:
    @staticmethod
    def normalize_bar(data: Union[Dict, pd.Series], symbol: Optional[str] = None) -> Bar
    @staticmethod
    def normalize_quote(data: Dict, symbol: Optional[str] = None) -> Quote
    @staticmethod
    def normalize_trade(data: Dict, symbol: Optional[str] = None) -> MarketTrade
    @staticmethod
    def normalize_dataframe(df: pd.DataFrame, data_type: str = "bar") -> pd.DataFrame
```

---

## 6. Execution Layer

### 6.1 Broker Adapter Base (`quant/execution/brokers/base.py`, 49 lines)

```python
class BrokerAdapter(ABC):
    @abstractmethod
    def connect(self) -> None
    @abstractmethod
    def disconnect(self) -> None
    @abstractmethod
    def is_connected(self) -> bool
    @abstractmethod
    def submit_order(self, order: Order) -> str  # Returns order_id
    @abstractmethod
    def cancel_order(self, order_id: str) -> bool
    @abstractmethod
    def get_positions(self) -> List[Position]
    @abstractmethod
    def get_account_info(self) -> AccountInfo
    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderStatus
```

### 6.2 Paper Broker (`quant/execution/brokers/paper.py`, 178 lines)

Simulated execution for paper trading:

```python
class PaperBroker(BrokerAdapter):
    def __init__(self, initial_cash=100000.0, slippage_bps=5, data_provider=None)
    def update_price(self, symbol: str, price: float) -> None
```

### 6.3 Futu Broker (Split into 3 mixins)

**futu_connection.py** (264 lines):
```python
class FutuConnectionMixin:
    def __init__(self, host="127.0.0.1", port=11111, acc_list=None, 
                 password="", trade_mode="SIMULATE")
    def connect(self) -> None
    def unlock_trade(self, password=None, trade_mode=None) -> bool
    def _get_acc_id(self, market: str) -> int
```

**futu_trade.py** (371 lines):
```python
class FutuTradeMixin:
    def submit_order(self, order: Order) -> str
    def cancel_order(self, order_id: str) -> bool
    def modify_order(self, order_id, price=None, quantity=None) -> bool
    def get_order_list(self) -> List[FutuOrderState]
    def get_today_deals(self) -> pd.DataFrame
```

**futu_position.py** (275 lines):
```python
class FutuPositionMixin:
    def get_positions(self) -> List[Position]
    def get_account_info(self) -> AccountInfo
    def get_positions_enriched(self) -> List[Dict]  # With PnL%, today buy/sell qty
    def get_account_detail(self) -> Dict  # HK + US aggregated
```

**futu.py** (17 lines) - Composition root:
```python
class FutuBroker(FutuConnectionMixin, FutuTradeMixin, FutuPositionMixin, BrokerAdapter):
    """Futu OpenAPI broker adapter."""
    pass
```

### 6.4 Order Manager (`quant/execution/order_manager.py`, 244 lines)

```python
class OrderManager:
    def __init__(self, portfolio, risk_engine, event_bus, config)
    def register_broker(self, name: str, broker: BrokerAdapter, symbols: List[str] = None)
    def submit_order(self, symbol: str, quantity: float, side: str, 
                     order_type: str = "MARKET", price: Optional[float] = None,
                     strategy_name: Optional[str] = None) -> Optional[str]
    def cancel_order(self, order_id: str) -> bool
    def get_order_status(self, order_id: str) -> Optional[OrderStatus]
    def get_open_orders(self) -> List[Order]
```

### 6.5 Fill Handler (`quant/execution/fill_handler.py`, 174 lines)

```python
class FillHandler:
    def __init__(self, portfolio, event_bus, config)
    def process_fill(self, order_id: str, symbol: str, side: str, 
                     quantity: float, price: float, commission: float = 0.0,
                     timestamp: Optional[datetime] = None,
                     strategy_name: Optional[str] = None) -> Fill
    def register_fill_callback(self, callback: Callable) -> None
    def get_fills(self, symbol=None, start=None, end=None) -> List[Fill]
    def get_fill_stats(self) -> Dict[str, Any]
```

### 6.6 Portfolio Coordinator (`quant/execution/portfolio_coordinator.py`, 192 lines)

Multi-strategy risk budget allocation:

```python
@dataclass
class StrategyAllocation:
    name: str
    priority: int
    max_risk_pct: float
    current_positions: Dict[str, Position]
    current_risk_used: float

class PortfolioCoordinator:
    def __init__(self, total_risk_budget: float = 1.0, max_portfolio_leverage: float = 1.5)
    def register_strategy(self, name: str, priority: int, max_risk_pct: float)
    def update_strategy_position(self, strategy_name: str, symbol: str, position: Position)
    def check_combined_risk(self, order: Order, strategy_name: str, nav: float) -> RiskCheckResult
```

### 6.7 Strategy Position Tracker (`quant/execution/strategy_position_tracker.py`, 242 lines)

Per-strategy position attribution:

```python
@dataclass
class StrategyPosition:
    symbol: str
    strategy_name: str
    qty: float = 0.0
    avg_cost: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0

class StrategyPositionTracker:
    def record_order(self, order_id: str, strategy_name: Optional[str])
    def get_strategy_for_order(self, order_id: str) -> str
    def update_from_fill(self, strategy_name, symbol, side, qty, price)
    def calibrate(self, broker_positions: List[Dict]) -> Dict  # Sync with broker
    def get_breakdown(self) -> Dict[str, Dict[str, Any]]
    def snapshot_all(self, total_nav: float) -> List[StrategySnapshot]
```

---

## 7. Strategy Framework

### 7.1 Base Strategy (`quant/strategies/base.py`, 92 lines)

```python
class Strategy(ABC):
    def __init__(self, name: str)
    
    # Lifecycle hooks
    def on_start(self, context: Context) -> None
    def on_before_trading(self, context: Context, trading_date: date) -> None
    def on_data(self, context: Context, data: Any) -> None
    def on_fill(self, context: Context, fill: Any) -> None
    def on_order_rejected(self, context: Context, order: Any, reason: str) -> None
    def on_after_trading(self, context: Context, trading_date: date) -> None
    def on_stop(self, context: Context) -> None
    
    # Order helpers
    def buy(self, symbol: str, quantity: float, order_type: str = "MARKET", 
            price: Optional[float] = None) -> Optional[str]
    def sell(self, symbol: str, quantity: float, order_type: str = "MARKET",
             price: Optional[float] = None) -> Optional[str]
    
    # Position tracking
    def get_position(self, symbol: str) -> float
    def get_all_positions(self) -> Dict[str, float]
```

### 7.2 Registry (`quant/strategies/registry.py`, 71 lines)

Decorator-based registration with auto-discovery:

```python
@strategy("SimpleMomentum")
class SimpleMomentum(Strategy):
    ...

# Auto-discovery scans quant/strategies/*/
class StrategyRegistry:
    @staticmethod
    def register(name: str, cls: Type) -> None
    @staticmethod
    def get(name: str) -> Optional[Type]
    @staticmethod
    def create(name: str, **kwargs) -> Strategy
    @staticmethod
    def list_strategies() -> List[str]
```

### 7.3 Framework Components (`quant/strategies/framework.py`, 411 lines)

```python
@dataclass
class Signal:
    symbol: str
    score: float
    timestamp: datetime
    factors: Dict[str, float]
    direction: str  # "long" | "short"

class SignalGenerator:
    def add_factor(self, name: str, func: Callable, weight: float = 1.0)
    def generate_signal(self, symbol: str, data: pd.DataFrame) -> Signal
    def generate_signals(self, symbols: List[str], data_dict: Dict) -> List[Signal]

class PortfolioConstructor:
    def __init__(self, method="equal_weight", kelly_params=None)
    def construct(self, signals, current_prices, nav, max_position_pct=0.05) -> Dict[str, Tuple[str, float]]
    # Methods: equal_weight, kelly, risk_parity

class ExecutionScheduler:
    def __init__(self, method="TWAP", duration_minutes=60)
    def schedule_order(self, symbol, quantity, side, start_time, interval_minutes=5) -> List[Tuple[datetime, float]]

class AlphaEngine:
    def generate_and_construct(self, symbols, data_dict, current_prices, nav) -> Dict[str, Tuple[str, float]]
```

### 7.4 Factor Library (`quant/strategies/factors.py`, 436 lines)

```python
class Factor(ABC):
    @abstractmethod
    def calculate(self, data: pd.DataFrame) -> float

class MomentumFactor(Factor)
class MeanReversionFactor(Factor)
class VolatilityFactor(Factor)
class VolumeFactor(Factor)
class RSIFactor(Factor)
class MACDFactor(Factor)
class BollingerBandFactor(Factor)
class ATRFactor(Factor)
class VolatilityRegimeFactor(Factor)
class QualityFactor(Factor)

class FactorLibrary:
    def get_factor(self, name, **kwargs) -> Factor
    def calculate_factor(self, name, data, **kwargs) -> float
```

### 7.5 Implemented Strategies

#### SimpleMomentum (`simple_momentum/strategy.py`, 218 lines)

```python
@strategy("SimpleMomentum")
class SimpleMomentum(Strategy):
    """Cross-sectional momentum strategy.
    
    Parameters:
        momentum_lookback: Days for momentum calculation (default: 20)
        holding_period: Days to hold positions (default: 21)
        top_pct: Top percentile to long (default: 0.1)
        bottom_pct: Bottom percentile to short (default: 0.1)
        max_position_pct: Max position size (default: 0.05)
    """
```

#### VolatilityRegime (`volatility_regime/strategy.py`, 323 lines)

```python
@strategy("VolatilityRegime")
class VolatilityRegime(Strategy):
    """VIX-based regime switching strategy.
    
    Bull regime (VIX < 15): Momentum strategy
    Chop regime (VIX 15-25): Balanced
    Bear regime (VIX > 25): Mean reversion with reduced exposure
    
    Parameters:
        vix_lookback: VIX SMA lookback (default: 20)
        vix_bull_threshold: Bull threshold (default: 15.0)
        vix_bear_threshold: Bear threshold (default: 25.0)
        momentum_top_n: Top N momentum stocks (default: 5)
        rsi_period: RSI calculation period (default: 14)
        reduce_exposure_bear: Exposure reduction in bear (default: 0.3)
    """
```

#### CrossSectionalMeanReversion (`cross_sectional_mr/strategy.py`, 197 lines)

```python
@strategy("CrossSectionalMeanReversion")
class CrossSectionalMeanReversion(Strategy):
    """Z-score based mean reversion strategy.
    
    Parameters:
        lookback_days: Return lookback (default: 5)
        holding_days: Holding period (default: 5)
        top_pct: Top percentile to short (default: 0.1)
        bottom_pct: Bottom percentile to long (default: 0.1)
    """
```

---

## 8. CIO Module

### 8.1 CIO Engine (`quant/cio/cio_engine.py`, 54 lines)

```python
class CIOEngine:
    """Chief Investment Officer - market regime assessment."""
    
    def __init__(self, assessor=None, news_analyzer=None, allocator=None)
    def assess(self, indicators=None, news_text=None, enabled_strategies=None) -> Dict
    def get_cached(self) -> Optional[Dict]
```

### 8.2 Market Assessor (`quant/cio/market_assessor.py`, 59 lines)

```python
class MarketAssessor:
    """VIX-based regime detection."""
    
    def __init__(self, vix_bull_threshold=15.0, vix_bear_threshold=25.0, vix_lookback=20)
    def assess(self, indicators=None) -> Dict  # Returns regime, score, sentiment
    
# Regimes: Low Vol Bull (VIX < 15), Medium Volatility (15-25), High Vol Bear (> 25)
```

### 8.3 News Analyzer (`quant/cio/news_analyzer.py`, 58 lines)

```python
class NewsAnalyzer:
    """LLM-based news sentiment analysis."""
    
    def __init__(self, provider="openai", model="gpt-4o-mini", api_key="",
                 base_url="http://localhost:11434", temperature=0.3)
    def analyze(self, news_text, market_context=None) -> Dict[str, Any]
    # Returns: sentiment_score (0-1), direction, summary, key_points, confidence
```

### 8.4 Weight Allocator (`quant/cio/weight_allocator.py`, 61 lines)

```python
class WeightAllocator:
    """Strategy weight allocation by regime."""
    
    REGIME_WEIGHTS = {
        "low_vol_bull": {
            "volatility_regime": 0.30,
            "simple_momentum": 0.20,
            "momentum_eod": 0.15,
            ...
        },
        "medium_vol_chop": {...},
        "high_vol_bear": {...}
    }
    
    def allocate(self, regime, enabled_strategies=None) -> Dict[str, float]
```

### 8.5 LLM Adapters (`quant/cio/llm_adapters/`)

All adapters have graceful degradation (return neutral on failure):

```python
# openai_adapter.py (35 lines)
class OpenAIAdapter(LLMAdapter):
    def __init__(self, model="gpt-4o-mini", api_key="", temperature=0.3)

# claude_adapter.py (35 lines)
class ClaudeAdapter(LLMAdapter):
    def __init__(self, model="claude-3-haiku-20240307", api_key="", temperature=0.3)

# ollama_adapter.py (38 lines)
class OllamaAdapter(LLMAdapter):
    def __init__(self, model="llama3.2", base_url="http://localhost:11434", temperature=0.3)
```

---

## 9. Models

### 9.1 Order (`quant/models/order.py`, 32 lines)

```python
class OrderStatus(Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"

@dataclass
class Order:
    symbol: str
    quantity: float
    side: str
    order_type: str
    order_id: Optional[str] = None
    status: OrderStatus = OrderStatus.PENDING
    price: Optional[float] = None
    filled_quantity: float = 0
    avg_fill_price: Optional[float] = None
    timestamp: Optional[datetime] = None
    strategy_name: Optional[str] = None
```

### 9.2 Position (`quant/models/position.py`, 17 lines)

```python
@dataclass
class Position:
    symbol: str
    quantity: float
    avg_cost: float
    market_value: float
    unrealized_pnl: float
    realized_pnl: float = 0.0
    sector: Optional[str] = None
```

### 9.3 Fill (`quant/models/fill.py`, 19 lines)

```python
@dataclass
class Fill:
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    commission: float
    timestamp: datetime
    strategy_name: Optional[str] = None
```

### 9.4 Trade (`quant/models/trade.py`, 25 lines)

```python
@dataclass
class Trade:
    entry_time: datetime
    exit_time: datetime
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    signal_date: Optional[datetime] = None
    fill_date: Optional[datetime] = None
    fill_price: float = 0.0
    intended_qty: float = 0.0
    cost_breakdown: Optional[Dict] = None
    strategy_name: Optional[str] = None
```

### 9.5 AccountInfo (`quant/models/account.py`, 14 lines)

```python
@dataclass
class AccountInfo:
    account_id: str
    cash: float
    buying_power: float
    equity: float
    margin_used: float
```

---

## 10. Frontend

### 10.1 Component Tree

```
App.js (180 lines)
├── BacktestDashboard.js (439 lines)
│   ├── EquityChart (SVG line chart)
│   └── DrawdownChart (SVG area chart)
└── LiveTradingPage.js (106 lines)
    ├── AccountOverview.js (133 lines)
    │   └── MarketPanel (HK/US panels)
    ├── StrategyPositionCards.js (173 lines)
    │   ├── StrategyCard
    │   └── Sparkline (SVG mini chart)
    ├── StrategyManagement.js (321 lines)
    │   ├── CIOAssessmentPanel.js (66 lines)
    │   ├── StrategyDetailModal.js (169 lines)
    │   │   └── ModalPnlChart (SVG)
    │   ├── PauseModal
    │   └── ConfirmAction
    └── PositionOverview.js (142 lines)
        └── StrategyWeightBar.js (34 lines)
```

### 10.2 State Management

Pure React hooks - no Redux:
- `useState` for component-local state
- `useEffect` for data fetching with polling
- `useCallback` for memoized handlers

**Polling Intervals:**
- System status: 3 seconds
- Portfolio/positions: 5 seconds
- CIO assessment: 60 seconds
- Backtest result: 2 seconds

### 10.3 Charts

All custom SVG implementations (no external chart library):
- **EquityChart**: Line chart with gradient fill
- **DrawdownChart**: Red area chart showing drawdown from peak
- **Sparkline**: Tiny line chart with trend coloring
- **ModalPnlChart**: P&L curve for strategy detail
- **StrategyWeightBar**: Horizontal stacked bar for allocation

---

## 11. Configuration

### 11.1 config.yaml

```yaml
system:
  mode: paper                    # paper|live|backtest
  log_level: INFO
  initial_cash: 100000
  currency: USD

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
  providers: [yahoo, alpha_vantage, futu]
  default_timeframe: 5m

execution:
  brokers: [paper]
  slippage_bps: 5
  commission:
    US: {type: per_share, per_share: 0.005, min_per_order: 1.0}
    HK: {type: percent, percent: 0.001, min_per_order: 2.0}

risk:
  max_position_pct: 0.05
  max_sector_pct: 0.25
  max_daily_loss_pct: 0.02
  max_leverage: 1.5
  max_orders_minute: 30

scheduler:
  jobs:
    - {name: us_open_rebalance, trigger: market_open, market: US}
    - {name: hk_open_rebalance, trigger: market_open, market: HK}
    - {name: intraday_check, trigger: intraday, interval_minutes: 30}

backtest:
  speed: 1x
  slippage_bps: 5

walkforward:
  enabled: true
  train_window_days: 126
  test_window_days: 21
  step_days: 21
```

### 11.2 strategies.yaml

```yaml
strategies:
  VolatilityRegime:
    enabled: true
    priority: 1
    parameters:
      symbols: [AAPL, GOOGL, MSFT, AMZN, TSLA, SPY, QQQ, VIX]
      vix_bull_threshold: 15.0
      vix_bear_threshold: 25.0
      momentum_lookback: 20
      # ... more params
```

### 11.3 brokers.yaml (credentials)

```yaml
alpha_vantage:
  api_key: "YOUR_API_KEY"

futu:
  host: "127.0.0.1"
  port: 11111
  acc_list: {}
  password: ""
  trade_mode: "SIMULATE"  # or "REAL"

ibkr:
  host: "localhost"
  port: 7497
  client_id: 1

alpaca:
  api_key: "YOUR_API_KEY"
  secret_key: "YOUR_SECRET"
  paper: true
```

---

## 12. Deployment

### 12.1 Local Development (macOS/Linux)

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Start UI (starts both API and React dev server)
./start_ui.sh

# Browser opens at http://localhost:3000
# API available at http://localhost:5000
```

### 12.2 Windows Production

```bash
start_ui.bat
```

This:
1. Starts `api_server.py` in a new window
2. Waits 3 seconds
3. Opens browser to `http://localhost:5000`

### 12.3 CLI Usage

```bash
# Live/paper trading
python quant/quant_system.py --mode paper

# Backtesting
python quant/backtest_runner.py \
    --strategy SimpleMomentum \
    --start 2024-01-01 \
    --end 2024-12-31 \
    --symbols "HK.00700,HK.09988"

# Data preparation
python scripts/prepare_data.py --market hk --start 2020-01-01
```

### 12.4 Testing

```bash
# Run all tests
python -m pytest quant/tests/ -q

# Current status: 157 passed, 3 skipped, 0 failed
```

---

## Appendix: Key Design Decisions

### A1. Why Two Backtest Paths?

- **Engine.run_backtest()**: Simple date iteration for quick testing
- **Backtester**: Full simulation with T+1, lot sizes, commissions, slippage for production research

### A2. Why Blueprint Split?

`api_server.py` was 1321 lines with 40 routes. Split into 7 blueprints (~50-250 lines each) for maintainability.

### A3. Why Futu Broker Mixins?

`futu.py` was 891 lines. Split into:
- Connection (264 lines) - lifecycle, auth
- Trade (371 lines) - orders, fills
- Position (275 lines) - portfolio, account

Enables independent testing and clearer separation.

### A4. Why DuckDB?

- Columnar storage for fast analytical queries
- Single file, no server required
- SQL interface with pandas integration
- Handles both time-series (bars) and relational (orders, positions) data

### A5. Thread Safety

All mutable shared state uses `threading.RLock()`:
- `EventBus._subscribers`
- `Portfolio._lock`
- `RiskEngine._lock`
- `FillHandler._lock`
- `StrategyPositionTracker._lock`

---

## Appendix: File Size Summary

| File/Module | Lines | Purpose |
|-------------|-------|---------|
| `api_server.py` | 44 | Thin Flask orchestrator |
| `quant/api/*` (8 files) | ~1,400 | API blueprints |
| `quant/core/*` (6 files) | ~1,900 | Engine, backtester, portfolio, risk |
| `quant/data/*` (12 files) | ~1,900 | Providers, DuckDB, normalizer |
| `quant/execution/*` (10 files) | ~2,100 | Order management, brokers |
| `quant/strategies/*` (8 files) | ~1,350 | Base, framework, 3 implementations |
| `quant/cio/*` (11 files) | ~400 | Market assessment, LLM adapters |
| `quant/models/*` (6 files) | ~120 | Dataclasses |
| `quant/utils/*` (4 files) | ~210 | Logger, datetime, config |
| `frontend/src/*` (13 files) | ~3,800 | React components |
| **Total Python** | ~10,000 | Production code |
| **Tests** (26 files) | ~1,500 | 157 tests |

---

*End of Technical Manual*
