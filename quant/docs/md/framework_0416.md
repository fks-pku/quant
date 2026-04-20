# Quant Trading System Framework Documentation

**Version:** 1.0  
**Date:** 2025-04-16  
**System:** Modular Event-Driven Quantitative Trading Platform

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [System Architecture](#system-architecture)
3. [Core Components Deep Dive](#core-components-deep-dive)
4. [Data Layer](#data-layer)
5. [Execution Layer](#execution-layer)
6. [Strategy Framework](#strategy-framework)
7. [Configuration System](#configuration-system)
8. [Frontend/UI Architecture](#frontendui-architecture)
9. [Workflow Examples](#workflow-examples)
10. [Directory Reference](#directory-reference)

---

## Executive Summary

This is a **production-grade quantitative trading framework** built with Python, featuring:

- **Event-driven architecture** for real-time processing
- **Pluggable design** for data providers and brokers
- **Three operating modes**: Live trading, Paper trading, Backtesting
- **Risk management** with configurable limits
- **Web dashboard** for monitoring and control
- **Strategy registry** with decorator-based registration

### Key Capabilities

| Feature | Description |
|---------|-------------|
| Multi-mode Operation | Live, Paper (simulated), Backtest |
| Data Providers | Yahoo Finance, Alpha Vantage, Futu, yfinance |
| Broker Support | Paper (simulator), Futu, Interactive Brokers |
| Strategy Types | Momentum, Mean Reversion, Regime Switching |
| Risk Controls | Position limits, sector exposure, daily loss limits |
| Scheduling | Market open/close triggers, intraday intervals |
| Analytics | Sharpe ratio, drawdown, win rate, Calmar ratio |

---

## System Architecture

### High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           QUANT TRADING SYSTEM                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │   FRONTEND   │  │   API SERVER │  │    CLI       │  │ BACKTEST     │    │
│  │   (React)    │  │   (Flask)    │  │  (quant_)    │  │   RUNNER     │    │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘    │
│         │                 │                  │                  │           │
│         └─────────────────┴──────────────────┴──────────────────┘           │
│                                   │                                         │
│                           ┌───────▼───────┐                                 │
│                           │    ENGINE     │                                 │
│                           │  (Event Loop) │                                 │
│                           └───────┬───────┘                                 │
│                                   │                                         │
│         ┌─────────────────────────┼─────────────────────────┐               │
│         │                         │                         │               │
│   ┌─────▼─────┐           ┌───────▼────────┐       ┌───────▼──────┐        │
│   │   DATA    │           │    CORE        │       │  EXECUTION   │        │
│   │  LAYER    │◄─────────►│   LAYER        │◄─────►│   LAYER      │        │
│   └───────────┘           └────────────────┘       └──────────────┘        │
│         │                         │                         │               │
│         │                  ┌──────▼──────┐                  │               │
│         │                  │  STRATEGY   │                  │               │
│         │                  │   LAYER     │                  │               │
│         │                  └─────────────┘                  │               │
│         │                                                   │               │
│   ┌─────▼───────────────────────────────────────────▼───────┐              │
│   │                  EXTERNAL SERVICES                       │              │
│   │  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │              │
│   │  │  Yahoo  │  │  Alpha   │  │   Futu   │  │  IBKR    │  │              │
│   │  │ Finance │  │ Vantage  │  │  OpenAPI │  │   API    │  │              │
│   │  └─────────┘  └──────────┘  └──────────┘  └──────────┘  │              │
│   └─────────────────────────────────────────────────────────┘              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Event-Driven Architecture

All components communicate through a centralized **EventBus** using publish/subscribe pattern:

**Event Types:**

| Event | Description |
|-------|-------------|
| BAR | New market data bar received |
| QUOTE | Real-time quote update |
| TRADE | Trade execution |
| ORDER_SUBMIT | Order submitted to broker |
| ORDER_FILL | Order filled |
| ORDER_CANCEL | Order cancelled |
| ORDER_REJECT | Order rejected |
| POSITION_UPDATE | Position changed |
| RISK_CHECK | Risk validation event |
| MARKET_OPEN | Market opened |
| MARKET_CLOSE | Market closed |
| STRATEGY_SIGNAL | Trading signal generated |
| SYSTEM_SHUTDOWN | System stopping |

### Data Flow

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Data        │────►│   EventBus   │────►│   Strategy   │────►│  Order       │
│ Provider    │     │              │     │   on_data()  │     │  Manager     │
└─────────────┘     └──────────────┘     └──────────────┘     └──────┬───────┘
                                                                     │
                              ┌──────────────────────────────────────┘
                              ▼
┌─────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Portfolio   │◄────│  Fill        │◄────│    Broker    │◄────│  Risk        │
│ Update      │     │  Handler     │     │   Adapter    │     │  Engine      │
└─────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
```

---

## Core Components Deep Dive

### 1. Engine (core/engine.py)

The **Engine** is the central orchestrator that manages the event loop, strategies, and component lifecycle.

**Key Classes:**

```python
class SystemMode(Enum):
    LIVE = "live"         # Real execution
    PAPER = "paper"       # Simulated execution
    BACKTEST = "backtest" # Historical replay

@dataclass
class Context:
    """Provides strategies access to all system components."""
    portfolio: Portfolio
    risk_engine: RiskEngine
    event_bus: EventBus
    order_manager: OrderManager
    data_provider: DataProvider
    broker: BrokerAdapter
```

**Engine Lifecycle:**

1. **INITIALIZATION**
   - Create EventBus
   - Create Portfolio
   - Create RiskEngine
   - Create Scheduler
   - Register Data Providers
   - Register Broker
   - Setup Order Manager
   - Register Strategies

2. **START**
   - Call strategy.on_start()
   - Start Scheduler
   - Launch event loop thread

3. **RUN LOOP (Live/Paper)**
   - Heartbeat checks
   - Market open/close detection
   - Sleep 60s

3. **RUN LOOP (Backtest)**
   - Iterate dates
   - Fetch historical bars
   - Publish BAR events

4. **STOP**
   - Call strategy.on_stop()
   - Stop Scheduler
   - Disconnect providers/brokers

**Event Dispatching:**

```python
# Bar data dispatch
def _dispatch_bar(self, strategy, event):
    if hasattr(strategy, "on_data") and strategy.context:
        strategy.on_data(strategy.context, event.data)

# Market open dispatch
def _dispatch_market_open(self, strategy, event):
    if hasattr(strategy, "on_before_trading") and strategy.context:
        strategy.on_before_trading(strategy.context, trading_date)
```

---

### 2. Scheduler (core/scheduler.py)

Time-based job scheduler supporting market events and cron expressions.

**Job Types:**

| Type | Description | Example |
|------|-------------|---------|
| market_open | Trigger X minutes after market opens | Rebalance at 9:35 AM |
| market_close | Trigger X minutes before market closes | Exit positions at 3:50 PM |
| intraday | Periodic during market hours | Check every 15 minutes |
| scheduled | Cron-based scheduling | Daily at 8:00 AM |

**Configuration Example:**

```yaml
scheduler:
  jobs:
    - name: us_open_rebalance
      trigger: market_open
      market: US
      offset_minutes: 5
    
    - name: intraday_check
      trigger: intraday
      market: US
      interval_minutes: 15
```

---

### 3. Portfolio (core/portfolio.py)

Tracks positions, calculates NAV, and manages unrealized/realized P&L.

**Key Metrics:**

```python
portfolio.to_dict()  # Returns:
{
    "nav": 105000.00,
    "cash": 50000.00,
    "total_unrealized_pnl": 5000.00,
    "total_realized_pnl": 2500.00,
    "positions": {
        "AAPL": {"quantity": 100, "avg_price": 150.00}
    }
}
```

---

### 4. Risk Engine (core/risk.py)

Validates orders against configured risk limits.

**Risk Checks:**

| Check | Default Limit | Description |
|-------|---------------|-------------|
| max_position_pct | 5% | Max position size per symbol |
| max_sector_pct | 25% | Max exposure per sector |
| max_daily_loss_pct | 2% | Max daily loss limit |
| max_leverage | 1.5x | Maximum portfolio leverage |
| max_orders_minute | 30 | Order rate limiting |

**Risk Flow:**

```
Strategy Submit Order
        │
        ▼
┌───────────────┐
│  Risk Engine  │
│  check_order()│
└───────┬───────┘
        │
   ┌────┴────┐
   ▼         ▼
APPROVED  REJECTED
   │         │
   ▼         ▼
Submit    Log reason
to Broker Publish event
          Return None
```

---

### 5. EventBus (core/events.py)

Thread-safe publish/subscribe system for component communication.

```python
event_bus = EventBus()

# Subscribe to events
event_bus.subscribe(EventType.BAR, on_bar_callback)
event_bus.subscribe(EventType.ORDER_FILL, on_fill_callback)

# Publish events
event_bus.publish_nowait(
    EventType.BAR,
    data={"symbol": "AAPL", "close": 150.00},
    source="yahoo"
)
```

---

## Data Layer

### Provider Pattern

All data providers implement the DataProvider interface:

```python
class DataProvider(ABC):
    @abstractmethod
    def connect(self) -> None: ...
    
    @abstractmethod
    def get_bars(self, symbol, start, end, timeframe) -> pd.DataFrame: ...
    
    @abstractmethod
    def subscribe(self, symbols, callback) -> None: ...
```

### Available Providers

| Provider | File | Data Source | Best For |
|----------|------|-------------|----------|
| YahooProvider | providers/yahoo.py | Yahoo Finance API | Free, US stocks |
| YfinanceProvider | providers/yfinance_provider.py | yfinance library | Backtesting |
| AlphaVantageProvider | providers/alpha_vantage.py | Alpha Vantage API | Real-time, fundamentals |
| FutuProvider | providers/futu.py | Futu OpenAPI | Asian markets |

### Data Normalization

All providers return unified schema:
```python
{
    "timestamp": datetime,
    "open": float,
    "high": float,
    "low": float,
    "close": float,
    "volume": int,
    "symbol": str
}
```

---

## Execution Layer

### Broker Adapter Pattern

```python
class BrokerAdapter(ABC):
    @abstractmethod
    def connect(self) -> None: ...
    
    @abstractmethod
    def submit_order(self, order: Order) -> str: ...
    
    @abstractmethod
    def cancel_order(self, order_id: str) -> bool: ...
```

### Available Brokers

| Broker | File | Type | Features |
|--------|------|------|----------|
| PaperBroker | brokers/paper.py | Simulator | Slippage, delay simulation |
| FutuBroker | brokers/futu.py | Real | HK/US stocks, futures |
| IBKRBroker | brokers/ibkr.py | Real | Global markets |

### Order Manager (execution/order_manager.py)

Manages order lifecycle with retry logic:

```python
order_manager.submit_order(
    symbol="AAPL",
    quantity=100,
    side="BUY",
    order_type="MARKET",
    price=None,
    strategy_name="Momentum"
)
```

**Order States:**

```
PENDING → SUBMITTED → PARTIAL → FILLED
                      ↘
                       REJECTED
                        ↓
                      CANCELLED
```

---

## Strategy Framework

### Base Strategy Class

```python
from quant.strategies.base import Strategy

class MyStrategy(Strategy):
    def on_start(self, context: Context) -> None:
        """Initialize strategy resources."""
        pass
    
    def on_before_trading(self, context: Context, trading_date: date) -> None:
        """Pre-market setup."""
        pass
    
    def on_data(self, context: Context, data: Any) -> None:
        """Process new market data."""
        if self.should_buy(data):
            self.buy("AAPL", quantity=100)
    
    def on_fill(self, context: Context, fill: Any) -> None:
        """Handle order fills."""
        pass
    
    def on_after_trading(self, context: Context, trading_date: date) -> None:
        """Post-market cleanup."""
        pass
    
    def on_stop(self, context: Context) -> None:
        """Strategy shutdown."""
        pass
```

### Strategy Registry

Decorator-based registration:

```python
from quant.strategies.registry import strategy

@strategy("MyStrategy")
class MyStrategy(Strategy):
    def __init__(self, symbols: list, lookback: int = 20):
        super().__init__("MyStrategy")
        self.symbols = symbols
        self.lookback = lookback

# Usage
from quant.strategies.registry import StrategyRegistry
strategy = StrategyRegistry.create("MyStrategy", symbols=["AAPL"])
```

### Available Strategies

**Production Strategies:**

| Strategy | Type | Description |
|----------|------|-------------|
| VolatilityRegime | Regime Switching | Switches between momentum/mean reversion based on VIX |
| SimpleMomentum | Cross-Sectional | Long top decile, short bottom decile |
| DualMomentum | Tactical Allocation | Absolute + relative momentum |
| CrossSectionalMeanReversion | Mean Reversion | Z-score based reversions |

**Example Strategies:**

| Strategy | Purpose |
|----------|---------|
| MomentumEOD | Intraday momentum |
| MeanReversion1m | RSI-based reversion |
| DualThrust | Breakout system |

---

## Configuration System

### Main Configuration (config/config.yaml)

```yaml
system:
  mode: paper               # live | paper | backtest
  log_level: INFO
  data_dir: ./data
  start_date: "2025-01-01"
  end_date: "2025-12-31"
  initial_cash: 100000
  currency: USD

markets:
  US:
    timezone: America/New_York
    open_hour: 9
    open_minute: 30
    close_hour: 16
    close_minute: 0

data:
  providers:
    - yahoo
    - alpha_vantage
  default_timeframe: 5m
  cache_enabled: true

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
  max_orders_minute: 30

strategies:
  - name: MomentumEOD
    enabled: true
    priority: 1
    max_risk_pct: 0.30
    symbols:
      - AAPL
      - GOOGL
```

### Credentials (config/brokers.yaml) - Gitignored

```yaml
alpha_vantage:
  api_key: "YOUR_API_KEY"

futu:
  host: "127.0.0.1"
  port: 11111
  password: "YOUR_PASSWORD"
  trade_mode: "SIMULATE"
```

### Strategy Parameters (config/strategies.yaml)

```yaml
strategies:
  SimpleMomentum:
    parameters:
      lookback_days: 90
      top_pct: 0.10
      rebalance_freq: "monthly"
```

---

## Frontend/UI Architecture

### React Application Structure

```
frontend/
├── src/
│   ├── App.js              # Main dashboard
│   ├── App.css             # Styles
│   ├── BacktestDashboard.js # Backtest UI
│   ├── index.js            # Entry point
│   └── index.css           # Global styles
└── package.json
```

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /api/status | System status, portfolio, positions |
| POST | /api/start | Start quant system |
| POST | /api/stop | Stop quant system |
| GET | /api/strategies | List all strategies |
| GET | /api/strategies/:id | Strategy details |
| POST | /api/strategies/select | Select active strategy |
| GET | /api/portfolio | Portfolio data |
| GET | /api/market | Market prices |
| POST | /api/orders | Create order |
| POST | /api/backtest/run | Run new backtest |
| GET | /api/backtest/result/:id | Get backtest result |

---

## Workflow Examples

### Example 1: Running a Backtest

```bash
# CLI approach
python system/quant/backtest_runner.py \
    --strategy SimpleMomentum \
    --symbols AAPL MSFT GOOGL \
    --start 2020-01-01 \
    --end 2024-12-31 \
    --initial-cash 100000
```

### Example 2: Creating a Custom Strategy

```python
from quant.strategies.base import Strategy
from quant.strategies.registry import strategy

@strategy("RSIMeanReversion")
class RSIMeanReversion(Strategy):
    def __init__(self, symbols: list, rsi_period: int = 14):
        super().__init__("RSIMeanReversion")
        self.symbols = symbols
        self.rsi_period = rsi_period
    
    def on_data(self, context, data):
        symbol = data.get("symbol")
        close = data.get("close")
        
        # Calculate RSI and trade
        rsi = self.calculate_rsi(symbol, close)
        
        if rsi < 30:
            self.buy(symbol, quantity=100)
        elif rsi > 70:
            self.sell(symbol, quantity=100)
```

---

## Directory Reference

### Complete File Tree

```
quant/
├── api_server.py                    # Flask API server
├── start_ui.sh                      # Linux/Mac launcher
├── START.bat                        # Windows launcher
├── framework_0416.md                # This documentation
│
├── frontend/                        # React dashboard
│   ├── src/
│   │   ├── App.js                   # Main dashboard
│   │   ├── BacktestDashboard.js     # Backtest UI
│   │   └── index.js                 # Entry point
│   └── package.json
│
├── system/                          # Core Python system
│   ├── quant/
│   │   ├── quant_system.py          # CLI main entry
│   │   ├── backtest_runner.py       # CLI backtest runner
│   │   │
│   │   ├── config/
│   │   │   ├── config.yaml          # Main configuration
│   │   │   ├── brokers.yaml         # Credentials (gitignored)
│   │   │   └── strategies.yaml      # Strategy parameters
│   │   │
│   │   ├── core/                    # Core engine components
│   │   │   ├── engine.py            # Main event loop
│   │   │   ├── events.py            # EventBus implementation
│   │   │   ├── scheduler.py         # Job scheduler
│   │   │   ├── portfolio.py         # Position & NAV tracking
│   │   │   ├── risk.py              # Risk engine
│   │   │   ├── backtester.py        # Historical replay
│   │   │   ├── walkforward.py       # Walk-forward analysis
│   │   │   └── analytics.py         # Performance metrics
│   │   │
│   │   ├── data/                    # Data layer
│   │   │   ├── providers/
│   │   │   │   ├── base.py          # Abstract interface
│   │   │   │   ├── yahoo.py         # Yahoo Finance
│   │   │   │   ├── yfinance_provider.py
│   │   │   │   ├── alpha_vantage.py
│   │   │   │   └── futu.py          # Futu OpenAPI
│   │   │   ├── storage.py           # SQLite/Parquet storage
│   │   │   ├── normalizer.py        # Data normalization
│   │   │   └── symbol_registry.py   # Symbol mapping
│   │   │
│   │   ├── execution/               # Execution layer
│   │   │   ├── brokers/
│   │   │   │   ├── base.py          # Abstract interface
│   │   │   │   ├── paper.py         # Paper trading
│   │   │   │   ├── futu.py          # Futu broker
│   │   │   │   └── ibkr.py          # Interactive Brokers
│   │   │   ├── order_manager.py     # Order lifecycle
│   │   │   ├── fill_handler.py      # Fill processing
│   │   │   └── portfolio_coordinator.py
│   │   │
│   │   ├── models/                  # Domain models
│   │   │   ├── order.py             # Order dataclass
│   │   │   ├── position.py          # Position model
│   │   │   ├── fill.py              # Fill confirmation
│   │   │   ├── trade.py             # Trade record
│   │   │   └── account.py           # Account info
│   │   │
│   │   ├── strategies/              # Strategy layer
│   │   │   ├── base.py              # Abstract base class
│   │   │   ├── registry.py          # Strategy registry
│   │   │   ├── framework.py         # Signal framework
│   │   │   ├── factors.py           # Alpha factors
│   │   │   ├── implementations/     # Production strategies
│   │   │   │   ├── volatility_regime.py
│   │   │   │   ├── simple_momentum.py
│   │   │   │   ├── dual_momentum.py
│   │   │   │   └── cross_sectional_mean_reversion.py
│   │   │   ├── examples/            # Educational examples
│   │   │   │   ├── momentum_eod.py
│   │   │   │   ├── mean_reversion.py
│   │   │   │   └── dual_thrust.py
│   │   │   └── docs/                # Strategy documentation
│   │   │
│   │   ├── utils/                   # Utilities
│   │   │   ├── logger.py            # Logging setup
│   │   │   ├── config_loader.py     # Config parsing
│   │   │   └── datetime_utils.py    # Market hours helpers
│   │   │
│   │   └── tests/                   # Test suite
│   │       ├── test_core.py
│   │       ├── test_strategies.py
│   │       └── test_backtester_fixed.py
│   │
│   └── requirements.txt             # Python dependencies
│
├── prd/                             # Product requirements
├── data/                            # Data storage
├── tmp/                             # Temporary files
└── .agents/                         # Agent skills
```

### Dependencies

**Python Core:**
```
pandas>=1.5.0
numpy>=1.23.0
pyyaml>=6.0
croniter>=1.3.0
requests>=2.28.0
```

**Data Providers:**
```
yfinance>=0.2.0
alpha-vantage>=2.3.0
futu-api>=8.0.0
```

**Web API:**
```
flask>=2.3.0
flask-cors>=4.0.0
```

---

## Summary

This framework provides a **complete quantitative trading infrastructure** with:

1. **Modular Design**: Easy to swap data providers, brokers, and strategies
2. **Event-Driven**: Real-time processing with pub/sub architecture
3. **Risk Management**: Comprehensive limit checks and enforcement
4. **Multi-Mode**: Live, paper, and backtest from same codebase
5. **Extensible**: Simple decorator-based strategy registration
6. **Production-Ready**: Web UI, logging, error handling, graceful shutdown

---

*Documentation generated: 2025-04-16*
