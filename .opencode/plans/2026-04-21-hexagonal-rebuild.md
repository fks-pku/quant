# Hexagonal + Event-Driven Architecture Rebuild Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the quant trading system using Hexagonal Architecture (Ports & Adapters) with Event-Driven communication, maintaining the same functionality while achieving clean separation of concerns.

**Architecture:** 
- **Domain Layer** (center): Pure business logic with zero external dependencies
- **Ports** (interfaces): Abstract contracts defined by domain, implemented by infrastructure
- **Adapters** (infrastructure): Concrete implementations of ports (brokers, data feeds, storage)
- **Application Layer** (features): Orchestrate use cases by coordinating domain and adapters
- **Plugins** (strategies): Swappable business logic implementations

**Tech Stack:** Python 3.10+, dataclasses, ABC, threading.RLock, pandas, numpy

---

## Architecture Mapping

```
┌─────────────────────────────────────────────────────────────┐
│                         API Layer                           │
│                    (Thin HTTP routes)                       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      Features Layer                         │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐   │
│  │  Backtest   │ │   Trading   │ │     Portfolio       │   │
│  │   Engine    │ │   Engine    │ │   Coordinator       │   │
│  └─────────────┘ └─────────────┘ └─────────────────────┘   │
│  ┌─────────────────────┐ ┌─────────────────────────────┐   │
│  │      CIO Engine     │ │     Strategy Registry       │   │
│  └─────────────────────┘ └─────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Domain Layer (Pure, No Deps)                   │
│  ┌─────────────────────┐ ┌─────────────────────────────┐   │
│  │   Domain Models     │ │       Domain Events         │   │
│  │ Order, Position,    │ │ TradeEvent, FillEvent,      │   │
│  │ Trade, Fill, Bar    │ │ OrderEvent, MarketEvent     │   │
│  └─────────────────────┘ └─────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Domain Ports (Interfaces)              │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │   │
│  │  │  Data    │ │  Broker  │ │ Strategy │ │Storage │ │   │
│  │  │  Feed    │ │ Adapter  │ │   Port   │ │  Port  │ │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └────────┘ │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Infrastructure Layer (Adapters)                │
│  ┌─────────────────┐ ┌─────────────────────────────────┐   │
│  │  Data Feeds     │ │         Brokers                 │   │
│  │ ┌─────┬───────┐ │ │ ┌────────┬───────┬──────────┐   │   │
│  │ │Yahoo│DuckDB │ │ │ │ Paper  │ Futu  │  IBKR    │   │   │
│  │ └─────┴───────┘ │ │ └────────┴───────┴──────────┘   │   │
│  └─────────────────┘ └─────────────────────────────────┘   │
│  ┌─────────────────┐ ┌─────────────────────────────────┐   │
│  │     Storage     │ │         Event Bus               │   │
│  │   (DuckDB)      │ │      (Pub/Sub)                  │   │
│  └─────────────────┘ └─────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Strategies Layer (Plugins)                     │
│  ┌─────────────────┐ ┌─────────────────────────────────┐   │
│  │  SimpleMomentum │ │     VolatilityRegime            │   │
│  └─────────────────┘ └─────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │           CrossSectionalMeanReversion               │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## Dependency Rules (Strict)

1. **Domain** has ZERO dependencies on any other layer
2. **Features** can depend on Domain only (no direct infrastructure access)
3. **Infrastructure** implements Domain Ports (depends on Domain)
4. **Strategies** implement Domain Strategy Port (depends on Domain only)
5. **API** depends on Features only
6. **Communication between layers** happens through:
   - Direct method calls (Domain ↔ Features)
   - Event Bus (async, decoupled)
   - Dependency Injection (ports injected into features)

---

## File Structure

```
quant/
├── domain/                          # NEW: Pure domain layer
│   ├── __init__.py
│   ├── models/                      # Domain entities (value objects)
│   │   ├── __init__.py
│   │   ├── order.py                 # Order entity
│   │   ├── position.py              # Position entity
│   │   ├── trade.py                 # Trade entity
│   │   ├── fill.py                  # Fill entity
│   │   ├── bar.py                   # Market data bar
│   │   └── account.py               # Account info
│   ├── events/                      # Domain events
│   │   ├── __init__.py
│   │   ├── base.py                  # Event base class
│   │   ├── trade_events.py          # Trade-related events
│   │   ├── order_events.py          # Order-related events
│   │   ├── market_events.py         # Market data events
│   │   └── system_events.py         # System lifecycle events
│   └── ports/                       # Abstract interfaces (ports)
│       ├── __init__.py
│       ├── data_feed.py             # DataFeed port
│       ├── broker.py                # BrokerAdapter port
│       ├── strategy.py              # Strategy port
│       ├── storage.py               # Storage port
│       └── event_publisher.py       # EventPublisher port
│
├── infrastructure/                  # Adapters implementation
│   ├── __init__.py
│   ├── events/
│   │   ├── __init__.py
│   │   └── event_bus.py             # Implements EventPublisher port
│   ├── feeds/                       # Data feed adapters
│   │   ├── __init__.py
│   │   ├── base.py                  # (remove, use domain port)
│   │   ├── duckdb.py                # Implements DataFeed port
│   │   ├── yahoo.py                 # Implements DataFeed port
│   │   └── futu.py                  # Implements DataFeed port
│   ├── brokers/                     # Broker adapters
│   │   ├── __init__.py
│   │   ├── paper.py                 # Implements Broker port
│   │   ├── futu.py                  # Implements Broker port
│   │   └── ibkr.py                  # Implements Broker port
│   ├── storage/
│   │   ├── __init__.py
│   │   └── duckdb.py                # Implements Storage port
│   └── data/                        # DEPRECATED: migrate to feeds/
│       ├── providers/               # Migrate to ../feeds/
│       ├── storage.py               # Migrate to ../storage/
│       └── ...
│
├── features/                        # Application layer (orchestrators)
│   ├── __init__.py
│   ├── backtest/
│   │   ├── __init__.py
│   │   ├── engine.py                # Depends on Domain + injected ports
│   │   ├── analytics.py             # Pure functions (no deps)
│   │   └── results.py               # Backtest result types
│   ├── trading/
│   │   ├── __init__.py
│   │   ├── engine.py                # Depends on Domain + injected ports
│   │   ├── risk.py                  # Risk engine (pure logic)
│   │   ├── scheduler.py             # Market time scheduler
│   │   └── context.py               # Strategy context (DI container)
│   ├── portfolio/
│   │   ├── __init__.py
│   │   ├── coordinator.py           # Multi-strategy coordinator
│   │   └── tracker.py               # Position tracking
│   ├── cio/
│   │   ├── __init__.py
│   │   ├── engine.py                # CIO orchestration
│   │   ├── market_assessor.py       # Market assessment logic
│   │   └── weight_allocator.py      # Portfolio weight allocation
│   └── strategy_registry/           # Strategy management
│       ├── __init__.py
│       ├── registry.py              # Strategy discovery & registration
│       └── loader.py                # Dynamic strategy loading
│
├── strategies/                      # Plugin layer
│   ├── __init__.py
│   ├── simple_momentum/
│   │   ├── __init__.py
│   │   └── strategy.py              # Implements Strategy port
│   ├── volatility_regime/
│   │   ├── __init__.py
│   │   └── strategy.py              # Implements Strategy port
│   └── cross_sectional_mr/
│       ├── __init__.py
│       └── strategy.py              # Implements Strategy port
│
├── api/                             # Interface layer
│   ├── __init__.py
│   ├── server.py                    # Flask/FastAPI application
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── backtest.py              # Backtest endpoints
│   │   ├── trading.py               # Trading endpoints
│   │   └── portfolio.py             # Portfolio endpoints
│   └── state/                       # Runtime state
│       └── __init__.py
│
├── shared/                          # Utilities (no business logic)
│   ├── __init__.py
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── logger.py                # Logging utilities
│   │   ├── datetime.py              # Date/time helpers
│   │   └── config.py                # Configuration loading
│   └── config/                      # Configuration files
│       ├── config.yaml
│       ├── brokers.yaml
│       └── strategies.yaml
│
└── tests/                           # Test suite
    ├── __init__.py
    ├── domain/                      # Domain unit tests
    ├── features/                    # Feature integration tests
    ├── infrastructure/              # Adapter tests
    └── strategies/                  # Strategy tests
```

---

## Phase 1: Domain Layer - Models

**Files to Create:**
- `quant/domain/__init__.py`
- `quant/domain/models/__init__.py`
- `quant/domain/models/order.py`
- `quant/domain/models/position.py`
- `quant/domain/models/trade.py`
- `quant/domain/models/fill.py`
- `quant/domain/models/bar.py`
- `quant/domain/models/account.py`

### Task 1.1: Create Domain Package Structure

**Files:**
- Create: `quant/domain/__init__.py`

- [ ] **Step 1: Write the domain package init**

```python
"""Domain layer - Pure business logic with zero external dependencies."""

from quant.domain.models.order import Order, OrderSide, OrderType, OrderStatus
from quant.domain.models.position import Position
from quant.domain.models.trade import Trade
from quant.domain.models.fill import Fill
from quant.domain.models.bar import Bar
from quant.domain.models.account import AccountInfo

__all__ = [
    # Models
    "Order",
    "OrderSide",
    "OrderType",
    "OrderStatus",
    "Position",
    "Trade",
    "Fill",
    "Bar",
    "AccountInfo",
]
```

- [ ] **Step 2: Create models directory init**

**Files:**
- Create: `quant/domain/models/__init__.py`

```python
"""Domain models - Core business entities."""

from quant.domain.models.order import Order, OrderSide, OrderType, OrderStatus
from quant.domain.models.position import Position
from quant.domain.models.trade import Trade
from quant.domain.models.fill import Fill
from quant.domain.models.bar import Bar
from quant.domain.models.account import AccountInfo

__all__ = [
    "Order",
    "OrderSide",
    "OrderType",
    "OrderStatus",
    "Position",
    "Trade",
    "Fill",
    "Bar",
    "AccountInfo",
]
```

- [ ] **Step 3: Commit**

```bash
git add quant/domain/
git commit -m "feat(domain): create domain package structure"
```

### Task 1.2: Create Order Model

**Files:**
- Create: `quant/domain/models/order.py`

- [ ] **Step 1: Write the Order model**

```python
"""Order domain model."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional


class OrderSide(Enum):
    """Order side enumeration."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    """Order type enumeration."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(Enum):
    """Order status enumeration."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Order:
    """Domain Order entity.
    
    Represents a trading order with all necessary information.
    This is a value object - immutable once created.
    """
    symbol: str
    quantity: float
    side: OrderSide
    order_type: OrderType
    order_id: Optional[str] = None
    price: Optional[float] = None
    stop_price: Optional[float] = None
    status: OrderStatus = field(default=OrderStatus.PENDING)
    filled_quantity: float = field(default=0.0)
    avg_fill_price: Optional[float] = None
    timestamp: Optional[datetime] = None
    strategy_name: Optional[str] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            object.__setattr__(self, 'timestamp', datetime.now())
    
    @property
    def is_buy(self) -> bool:
        """Check if this is a buy order."""
        return self.side == OrderSide.BUY
    
    @property
    def is_sell(self) -> bool:
        """Check if this is a sell order."""
        return self.side == OrderSide.SELL
    
    @property
    def remaining_quantity(self) -> float:
        """Get remaining quantity to fill."""
        return self.quantity - self.filled_quantity
    
    @property
    def is_filled(self) -> bool:
        """Check if order is completely filled."""
        return self.status == OrderStatus.FILLED
    
    @property
    def is_active(self) -> bool:
        """Check if order is still active (not finalized)."""
        return self.status in (OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIAL)
    
    def with_fill(self, fill_quantity: float, fill_price: float) -> "Order":
        """Create new Order with updated fill information."""
        new_filled = self.filled_quantity + fill_quantity
        
        # Calculate new average fill price
        if self.avg_fill_price is None:
            new_avg = fill_price
        else:
            total_value = (self.filled_quantity * self.avg_fill_price) + (fill_quantity * fill_price)
            new_avg = total_value / new_filled if new_filled > 0 else fill_price
        
        # Determine new status
        if new_filled >= self.quantity:
            new_status = OrderStatus.FILLED
        elif new_filled > 0:
            new_status = OrderStatus.PARTIAL
        else:
            new_status = self.status
        
        return Order(
            symbol=self.symbol,
            quantity=self.quantity,
            side=self.side,
            order_type=self.order_type,
            order_id=self.order_id,
            price=self.price,
            stop_price=self.stop_price,
            status=new_status,
            filled_quantity=new_filled,
            avg_fill_price=new_avg,
            timestamp=self.timestamp,
            strategy_name=self.strategy_name,
        )
    
    def with_status(self, status: OrderStatus) -> "Order":
        """Create new Order with updated status."""
        return Order(
            symbol=self.symbol,
            quantity=self.quantity,
            side=self.side,
            order_type=self.order_type,
            order_id=self.order_id,
            price=self.price,
            stop_price=self.stop_price,
            status=status,
            filled_quantity=self.filled_quantity,
            avg_fill_price=self.avg_fill_price,
            timestamp=self.timestamp,
            strategy_name=self.strategy_name,
        )
```

- [ ] **Step 2: Run basic import test**

Run: `python -c "from quant.domain.models.order import Order, OrderSide; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add quant/domain/models/order.py
git commit -m "feat(domain): add Order model with value object semantics"
```

### Task 1.3: Create Position Model

**Files:**
- Create: `quant/domain/models/position.py`

- [ ] **Step 1: Write the Position model**

```python
"""Position domain model."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Position:
    """Domain Position entity.
    
    Represents a holding in a specific symbol.
    Tracks quantity, cost basis, and P&L.
    """
    symbol: str
    quantity: float = field(default=0.0)
    avg_cost: float = field(default=0.0)
    market_value: float = field(default=0.0)
    unrealized_pnl: float = field(default=0.0)
    realized_pnl: float = field(default=0.0)
    sector: Optional[str] = None
    
    @property
    def is_long(self) -> bool:
        """Check if this is a long position."""
        return self.quantity > 0
    
    @property
    def is_short(self) -> bool:
        """Check if this is a short position."""
        return self.quantity < 0
    
    @property
    def is_flat(self) -> bool:
        """Check if position is flat (no holding)."""
        return self.quantity == 0
    
    @property
    def cost_basis(self) -> float:
        """Get total cost basis."""
        return abs(self.quantity * self.avg_cost)
    
    def update_from_fill(self, fill_quantity: float, fill_price: float) -> None:
        """Update position based on a fill.
        
        Args:
            fill_quantity: Positive for buy, negative for sell
            fill_price: Execution price
        """
        if fill_quantity == 0:
            return
        
        old_quantity = self.quantity
        new_quantity = old_quantity + fill_quantity
        
        if old_quantity == 0:
            # New position
            self.avg_cost = fill_price
        elif (old_quantity > 0 and fill_quantity > 0) or (old_quantity < 0 and fill_quantity < 0):
            # Adding to position - update average cost
            old_cost = abs(old_quantity * self.avg_cost)
            new_cost = abs(fill_quantity * fill_price)
            self.avg_cost = (old_cost + new_cost) / abs(new_quantity)
        else:
            # Reducing or flipping position
            if abs(fill_quantity) < abs(old_quantity):
                # Partial close - calculate realized P&L
                realized = (fill_price - self.avg_cost) * fill_quantity * (-1 if old_quantity > 0 else 1)
                self.realized_pnl += realized
            else:
                # Full close or flip
                realized = (fill_price - self.avg_cost) * old_quantity * (-1 if old_quantity > 0 else 1)
                self.realized_pnl += realized
                if new_quantity != 0:
                    # Flipped - new position at fill price
                    self.avg_cost = fill_price
        
        self.quantity = new_quantity
        
        if self.quantity == 0:
            self.avg_cost = 0.0
            self.market_value = 0.0
            self.unrealized_pnl = 0.0
    
    def update_market_price(self, price: float) -> None:
        """Update position based on current market price."""
        self.market_value = self.quantity * price
        if self.quantity != 0:
            self.unrealized_pnl = (price - self.avg_cost) * self.quantity
        else:
            self.unrealized_pnl = 0.0
```

- [ ] **Step 2: Test Position model**

Run: `python -c "from quant.domain.models.position import Position; p = Position('AAPL'); p.update_from_fill(100, 150.0); print(f'Qty: {p.quantity}, Avg: {p.avg_cost}')"`
Expected: `Qty: 100, Avg: 150.0`

- [ ] **Step 3: Commit**

```bash
git add quant/domain/models/position.py
git commit -m "feat(domain): add Position model with P&L tracking"
```

### Task 1.4: Create Trade Model

**Files:**
- Create: `quant/domain/models/trade.py`

- [ ] **Step 1: Write the Trade model**

```python
"""Trade domain model - represents completed round-trip."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Any


@dataclass(frozen=True)
class Trade:
    """Domain Trade entity.
    
    Represents a completed trade with entry and exit.
    This is a value object - immutable once created.
    """
    symbol: str
    quantity: float
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    side: str  # "LONG" or "SHORT"
    pnl: float
    commission: float = field(default=0.0)
    realized_pnl: float = field(default=0.0)
    signal_date: Optional[datetime] = None
    fill_date: Optional[datetime] = None
    fill_price: float = field(default=0.0)
    intended_qty: float = field(default=0.0)
    cost_breakdown: Optional[Dict[str, float]] = field(default=None)
    strategy_name: Optional[str] = None
    
    def __post_init__(self):
        pass  # frozen dataclass, validation at creation
    
    @property
    def is_win(self) -> bool:
        """Check if trade was profitable."""
        return self.pnl > 0
    
    @property
    def is_loss(self) -> bool:
        """Check if trade was a loss."""
        return self.pnl < 0
    
    @property
    def return_pct(self) -> float:
        """Calculate return percentage."""
        if self.entry_price == 0:
            return 0.0
        return ((self.exit_price - self.entry_price) / self.entry_price) * 100
    
    @property
    def duration(self) -> float:
        """Get trade duration in days."""
        return (self.exit_time - self.entry_time).total_seconds() / 86400
    
    @classmethod
    def from_entry_exit(
        cls,
        symbol: str,
        quantity: float,
        entry_price: float,
        exit_price: float,
        entry_time: datetime,
        exit_time: datetime,
        side: str = "LONG",
        commission: float = 0.0,
        **kwargs
    ) -> "Trade":
        """Factory method to create Trade from entry/exit."""
        # Calculate P&L
        if side == "LONG":
            pnl = (exit_price - entry_price) * quantity - commission
        else:
            pnl = (entry_price - exit_price) * quantity - commission
        
        return cls(
            symbol=symbol,
            quantity=quantity,
            entry_price=entry_price,
            exit_price=exit_price,
            entry_time=entry_time,
            exit_time=exit_time,
            side=side,
            pnl=pnl,
            commission=commission,
            realized_pnl=pnl,
            **kwargs
        )
```

- [ ] **Step 2: Test Trade model**

Run: `python -c "from quant.domain.models.trade import Trade; from datetime import datetime; t = Trade.from_entry_exit('AAPL', 100, 150.0, 160.0, datetime.now(), datetime.now()); print(f'PnL: {t.pnl}, Return: {t.return_pct:.2f}%')"`
Expected: `PnL: 1000.0, Return: 6.67%`

- [ ] **Step 3: Commit**

```bash
git add quant/domain/models/trade.py
git commit -m "feat(domain): add Trade model for completed round-trips"
```

### Task 1.5: Create Fill Model

**Files:**
- Create: `quant/domain/models/fill.py`

- [ ] **Step 1: Write the Fill model**

```python
"""Fill domain model."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Fill:
    """Domain Fill entity.
    
    Represents an order fill (partial or complete).
    This is a value object - immutable once created.
    """
    order_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: float
    price: float
    commission: float
    timestamp: datetime
    fill_id: Optional[str] = None
    strategy_name: Optional[str] = None
    
    @property
    def value(self) -> float:
        """Get fill value (quantity * price)."""
        return self.quantity * self.price
    
    @property
    def net_value(self) -> float:
        """Get fill value minus commission."""
        return self.value - self.commission
```

- [ ] **Step 2: Test Fill model**

Run: `python -c "from quant.domain.models.fill import Fill; from datetime import datetime; f = Fill('ord123', 'AAPL', 'BUY', 100, 150.0, 1.0, datetime.now()); print(f'Value: {f.value}, Net: {f.net_value}')"`
Expected: `Value: 15000.0, Net: 14999.0`

- [ ] **Step 3: Commit**

```bash
git add quant/domain/models/fill.py
git commit -m "feat(domain): add Fill model for order execution"
```

### Task 1.6: Create Bar Model

**Files:**
- Create: `quant/domain/models/bar.py`

- [ ] **Step 1: Write the Bar model**

```python
"""Bar (OHLCV) domain model."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Bar:
    """Domain Bar entity (OHLCV data).
    
    Represents a single bar of market data.
    This is a value object - immutable once created.
    """
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    timeframe: str = "1d"  # "1m", "5m", "1h", "1d", etc.
    source: Optional[str] = None  # Data provider name
    adjusted: bool = False  # Whether prices are split/dividend adjusted
    
    def __post_init__(self):
        # Validate OHLC relationships
        if self.high < max(self.open, self.close, self.low):
            raise ValueError(f"High {self.high} is less than max of O/C/L")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError(f"Low {self.low} is greater than min of O/C/H")
    
    @property
    def range(self) -> float:
        """Get price range (high - low)."""
        return self.high - self.low
    
    @property
    def body(self) -> float:
        """Get body size (abs(close - open))."""
        return abs(self.close - self.open)
    
    @property
    def is_bullish(self) -> bool:
        """Check if bar is bullish (close > open)."""
        return self.close > self.open
    
    @property
    def is_bearish(self) -> bool:
        """Check if bar is bearish (close < open)."""
        return self.close < self.open
    
    @property
    def typical_price(self) -> float:
        """Get typical price (H + L + C) / 3."""
        return (self.high + self.low + self.close) / 3
    
    @property
    def weighted_price(self) -> float:
        """Get weighted price (O + H + L + C) / 4."""
        return (self.open + self.high + self.low + self.close) / 4
    
    @property
    def value(self) -> float:
        """Get dollar volume (close * volume)."""
        return self.close * self.volume
```

- [ ] **Step 2: Test Bar model**

Run: `python -c "from quant.domain.models.bar import Bar; from datetime import datetime; b = Bar('AAPL', datetime.now(), 150.0, 155.0, 149.0, 152.0, 1000000); print(f'Range: {b.range}, Bullish: {b.is_bullish}')"`
Expected: `Range: 6.0, Bullish: True`

- [ ] **Step 3: Commit**

```bash
git add quant/domain/models/bar.py
git commit -m "feat(domain): add Bar model for OHLCV data"
```

### Task 1.7: Create Account Model

**Files:**
- Create: `quant/domain/models/account.py`

- [ ] **Step 1: Write the AccountInfo model**

```python
"""Account domain model."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class AccountInfo:
    """Domain AccountInfo entity.
    
    Represents account balance and margin information.
    This is a value object - immutable once created.
    """
    account_id: str
    cash: float
    buying_power: float
    equity: float
    currency: str = "USD"
    margin_used: float = field(default=0.0)
    margin_available: float = field(default=0.0)
    day_trading_buying_power: Optional[float] = None
    maintenance_margin: Optional[float] = None
    
    @property
    def total_value(self) -> float:
        """Get total account value."""
        return self.equity
    
    @property
    def leverage(self) -> float:
        """Calculate current leverage."""
        if self.equity == 0:
            return 0.0
        return self.margin_used / self.equity
```

- [ ] **Step 2: Test AccountInfo model**

Run: `python -c "from quant.domain.models.account import AccountInfo; a = AccountInfo('ACC001', 100000.0, 200000.0, 150000.0); print(f'Cash: {a.cash}, Leverage: {a.leverage}')"`
Expected: `Cash: 100000.0, Leverage: 0.0`

- [ ] **Step 3: Commit**

```bash
git add quant/domain/models/account.py
git commit -m "feat(domain): add AccountInfo model"
```

---

## Phase 2: Domain Layer - Events

**Files to Create:**
- `quant/domain/events/__init__.py`
- `quant/domain/events/base.py`
- `quant/domain/events/order_events.py`
- `quant/domain/events/trade_events.py`
- `quant/domain/events/market_events.py`
- `quant/domain/events/system_events.py`

### Task 2.1: Create Events Package Structure

**Files:**
- Create: `quant/domain/events/__init__.py`

- [ ] **Step 1: Write events package init**

```python
"""Domain events - Immutable event objects for event-driven communication."""

from quant.domain.events.base import Event, EventType
from quant.domain.events.order_events import (
    OrderSubmittedEvent,
    OrderFilledEvent,
    OrderCancelledEvent,
    OrderRejectedEvent,
)
from quant.domain.events.trade_events import (
    TradeOpenedEvent,
    TradeClosedEvent,
    PositionUpdateEvent,
)
from quant.domain.events.market_events import (
    BarEvent,
    QuoteEvent,
    MarketOpenEvent,
    MarketCloseEvent,
)
from quant.domain.events.system_events import (
    StrategySignalEvent,
    SystemStartEvent,
    SystemStopEvent,
)

__all__ = [
    # Base
    "Event",
    "EventType",
    # Order events
    "OrderSubmittedEvent",
    "OrderFilledEvent",
    "OrderCancelledEvent",
    "OrderRejectedEvent",
    # Trade events
    "TradeOpenedEvent",
    "TradeClosedEvent",
    "PositionUpdateEvent",
    # Market events
    "BarEvent",
    "QuoteEvent",
    "MarketOpenEvent",
    "MarketCloseEvent",
    # System events
    "StrategySignalEvent",
    "SystemStartEvent",
    "SystemStopEvent",
]
```

- [ ] **Step 2: Commit**

```bash
git add quant/domain/events/__init__.py
git commit -m "feat(domain): create events package structure"
```

### Task 2.2: Create Event Base Class

**Files:**
- Create: `quant/domain/events/base.py`

- [ ] **Step 1: Write the base Event class**

```python
"""Base event classes for domain events."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Optional
import uuid


class EventType(Enum):
    """Enumeration of all domain event types."""
    # Order events
    ORDER_SUBMITTED = auto()
    ORDER_FILLED = auto()
    ORDER_CANCELLED = auto()
    ORDER_REJECTED = auto()
    
    # Trade events
    TRADE_OPENED = auto()
    TRADE_CLOSED = auto()
    POSITION_UPDATE = auto()
    
    # Market events
    BAR = auto()
    QUOTE = auto()
    MARKET_OPEN = auto()
    MARKET_CLOSE = auto()
    
    # Strategy events
    STRATEGY_SIGNAL = auto()
    
    # System events
    SYSTEM_START = auto()
    SYSTEM_STOP = auto()
    RISK_CHECK = auto()


@dataclass(frozen=True)
class Event:
    """Base class for all domain events.
    
    All events are immutable value objects with a unique ID,
    timestamp, and event type.
    """
    event_type: EventType
    timestamp: datetime = field(default_factory=datetime.now)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source: Optional[str] = None
    
    def __post_init__(self):
        pass  # frozen dataclass
    
    def to_dict(self) -> dict:
        """Convert event to dictionary."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.name,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
        }
```

- [ ] **Step 2: Test Event base**

Run: `python -c "from quant.domain.events.base import Event, EventType; e = Event(EventType.BAR); print(f'Type: {e.event_type.name}, ID exists: {len(e.event_id) > 0}')"`
Expected: `Type: BAR, ID exists: True`

- [ ] **Step 3: Commit**

```bash
git add quant/domain/events/base.py
git commit -m "feat(domain): add Event base class with EventType enum"
```

### Task 2.3: Create Order Events

**Files:**
- Create: `quant/domain/events/order_events.py`

- [ ] **Step 1: Write order events**

```python
"""Order-related domain events."""

from dataclasses import dataclass
from typing import Optional

from quant.domain.events.base import Event, EventType
from quant.domain.models.order import Order, OrderStatus


@dataclass(frozen=True)
class OrderSubmittedEvent(Event):
    """Event emitted when an order is submitted."""
    order: Order
    broker_order_id: Optional[str] = None
    
    def __init__(self, order: Order, broker_order_id: Optional[str] = None, source: Optional[str] = None):
        super().__init__(
            event_type=EventType.ORDER_SUBMITTED,
            source=source
        )
        object.__setattr__(self, 'order', order)
        object.__setattr__(self, 'broker_order_id', broker_order_id)


@dataclass(frozen=True)
class OrderFilledEvent(Event):
    """Event emitted when an order is filled (partial or complete)."""
    order: Order
    fill_quantity: float
    fill_price: float
    commission: float
    
    def __init__(
        self,
        order: Order,
        fill_quantity: float,
        fill_price: float,
        commission: float = 0.0,
        source: Optional[str] = None
    ):
        super().__init__(
            event_type=EventType.ORDER_FILLED,
            source=source
        )
        object.__setattr__(self, 'order', order)
        object.__setattr__(self, 'fill_quantity', fill_quantity)
        object.__setattr__(self, 'fill_price', fill_price)
        object.__setattr__(self, 'commission', commission)


@dataclass(frozen=True)
class OrderCancelledEvent(Event):
    """Event emitted when an order is cancelled."""
    order: Order
    reason: Optional[str] = None
    
    def __init__(self, order: Order, reason: Optional[str] = None, source: Optional[str] = None):
        super().__init__(
            event_type=EventType.ORDER_CANCELLED,
            source=source
        )
        object.__setattr__(self, 'order', order)
        object.__setattr__(self, 'reason', reason)


@dataclass(frozen=True)
class OrderRejectedEvent(Event):
    """Event emitted when an order is rejected."""
    order: Order
    reason: str
    
    def __init__(self, order: Order, reason: str, source: Optional[str] = None):
        super().__init__(
            event_type=EventType.ORDER_REJECTED,
            source=source
        )
        object.__setattr__(self, 'order', order)
        object.__setattr__(self, 'reason', reason)
```

- [ ] **Step 2: Test order events**

Run: `python -c "from quant.domain.events.order_events import OrderSubmittedEvent; from quant.domain.models.order import Order, OrderSide, OrderType; o = Order('AAPL', 100, OrderSide.BUY, OrderType.MARKET); e = OrderSubmittedEvent(o, 'brk-123'); print(f'Event type: {e.event_type.name}, Symbol: {e.order.symbol}')"`
Expected: `Event type: ORDER_SUBMITTED, Symbol: AAPL`

- [ ] **Step 3: Commit**

```bash
git add quant/domain/events/order_events.py
git commit -m "feat(domain): add order domain events"
```

### Task 2.4: Create Trade Events

**Files:**
- Create: `quant/domain/events/trade_events.py`

- [ ] **Step 1: Write trade events**

```python
"""Trade-related domain events."""

from dataclasses import dataclass
from typing import Optional

from quant.domain.events.base import Event, EventType
from quant.domain.models.trade import Trade
from quant.domain.models.position import Position


@dataclass(frozen=True)
class TradeOpenedEvent(Event):
    """Event emitted when a new trade is opened."""
    trade: Trade
    
    def __init__(self, trade: Trade, source: Optional[str] = None):
        super().__init__(
            event_type=EventType.TRADE_OPENED,
            source=source
        )
        object.__setattr__(self, 'trade', trade)


@dataclass(frozen=True)
class TradeClosedEvent(Event):
    """Event emitted when a trade is closed."""
    trade: Trade
    
    def __init__(self, trade: Trade, source: Optional[str] = None):
        super().__init__(
            event_type=EventType.TRADE_CLOSED,
            source=source
        )
        object.__setattr__(self, 'trade', trade)


@dataclass(frozen=True)
class PositionUpdateEvent(Event):
    """Event emitted when a position is updated."""
    position: Position
    symbol: str
    
    def __init__(self, position: Position, source: Optional[str] = None):
        super().__init__(
            event_type=EventType.POSITION_UPDATE,
            source=source
        )
        object.__setattr__(self, 'position', position)
        object.__setattr__(self, 'symbol', position.symbol)
```

- [ ] **Step 2: Commit**

```bash
git add quant/domain/events/trade_events.py
git commit -m "feat(domain): add trade domain events"
```

### Task 2.5: Create Market Events

**Files:**
- Create: `quant/domain/events/market_events.py`

- [ ] **Step 1: Write market events**

```python
"""Market data domain events."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from quant.domain.events.base import Event, EventType
from quant.domain.models.bar import Bar


@dataclass(frozen=True)
class BarEvent(Event):
    """Event emitted when new bar data arrives."""
    bar: Bar
    
    def __init__(self, bar: Bar, source: Optional[str] = None):
        super().__init__(
            event_type=EventType.BAR,
            source=source
        )
        object.__setattr__(self, 'bar', bar)
    
    @property
    def symbol(self) -> str:
        return self.bar.symbol


@dataclass(frozen=True)
class QuoteEvent(Event):
    """Event emitted when new quote data arrives."""
    symbol: str
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    timestamp: datetime
    
    def __init__(
        self,
        symbol: str,
        bid: float,
        ask: float,
        bid_size: float = 0,
        ask_size: float = 0,
        timestamp: Optional[datetime] = None,
        source: Optional[str] = None
    ):
        super().__init__(
            event_type=EventType.QUOTE,
            source=source
        )
        object.__setattr__(self, 'symbol', symbol)
        object.__setattr__(self, 'bid', bid)
        object.__setattr__(self, 'ask', ask)
        object.__setattr__(self, 'bid_size', bid_size)
        object.__setattr__(self, 'ask_size', ask_size)
        object.__setattr__(self, 'timestamp', timestamp or datetime.now())
    
    @property
    def mid(self) -> float:
        """Get mid price."""
        return (self.bid + self.ask) / 2
    
    @property
    def spread(self) -> float:
        """Get bid-ask spread."""
        return self.ask - self.bid


@dataclass(frozen=True)
class MarketOpenEvent(Event):
    """Event emitted when market opens."""
    market: str  # "US", "HK", "CN", etc.
    open_time: datetime
    
    def __init__(self, market: str, open_time: Optional[datetime] = None, source: Optional[str] = None):
        super().__init__(
            event_type=EventType.MARKET_OPEN,
            source=source
        )
        object.__setattr__(self, 'market', market)
        object.__setattr__(self, 'open_time', open_time or datetime.now())


@dataclass(frozen=True)
class MarketCloseEvent(Event):
    """Event emitted when market closes."""
    market: str
    close_time: datetime
    
    def __init__(self, market: str, close_time: Optional[datetime] = None, source: Optional[str] = None):
        super().__init__(
            event_type=EventType.MARKET_CLOSE,
            source=source
        )
        object.__setattr__(self, 'market', market)
        object.__setattr__(self, 'close_time', close_time or datetime.now())
```

- [ ] **Step 2: Commit**

```bash
git add quant/domain/events/market_events.py
git commit -m "feat(domain): add market data domain events"
```

### Task 2.6: Create System Events

**Files:**
- Create: `quant/domain/events/system_events.py`

- [ ] **Step 1: Write system events**

```python
"""System-level domain events."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from quant.domain.events.base import Event, EventType


@dataclass(frozen=True)
class StrategySignalEvent(Event):
    """Event emitted when a strategy generates a signal."""
    strategy_name: str
    symbol: str
    signal: str  # "BUY", "SELL", "HOLD"
    confidence: float  # 0.0 to 1.0
    metadata: Dict[str, Any]
    
    def __init__(
        self,
        strategy_name: str,
        symbol: str,
        signal: str,
        confidence: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None
    ):
        super().__init__(
            event_type=EventType.STRATEGY_SIGNAL,
            source=source
        )
        object.__setattr__(self, 'strategy_name', strategy_name)
        object.__setattr__(self, 'symbol', symbol)
        object.__setattr__(self, 'signal', signal)
        object.__setattr__(self, 'confidence', confidence)
        object.__setattr__(self, 'metadata', metadata or {})


@dataclass(frozen=True)
class SystemStartEvent(Event):
    """Event emitted when system starts."""
    mode: str  # "live", "paper", "backtest"
    start_time: datetime
    config: Dict[str, Any]
    
    def __init__(
        self,
        mode: str,
        config: Optional[Dict[str, Any]] = None,
        start_time: Optional[datetime] = None,
        source: Optional[str] = None
    ):
        super().__init__(
            event_type=EventType.SYSTEM_START,
            source=source
        )
        object.__setattr__(self, 'mode', mode)
        object.__setattr__(self, 'config', config or {})
        object.__setattr__(self, 'start_time', start_time or datetime.now())


@dataclass(frozen=True)
class SystemStopEvent(Event):
    """Event emitted when system stops."""
    reason: str
    stop_time: datetime
    
    def __init__(self, reason: str, stop_time: Optional[datetime] = None, source: Optional[str] = None):
        super().__init__(
            event_type=EventType.SYSTEM_STOP,
            source=source
        )
        object.__setattr__(self, 'reason', reason)
        object.__setattr__(self, 'stop_time', stop_time or datetime.now())
```

- [ ] **Step 2: Commit**

```bash
git add quant/domain/events/system_events.py
git commit -m "feat(domain): add system domain events"
```

---

## Phase 3: Domain Layer - Ports (Interfaces)

**Files to Create:**
- `quant/domain/ports/__init__.py`
- `quant/domain/ports/data_feed.py`
- `quant/domain/ports/broker.py`
- `quant/domain/ports/strategy.py`
- `quant/domain/ports/storage.py`
- `quant/domain/ports/event_publisher.py`

### Task 3.1: Create Ports Package

**Files:**
- Create: `quant/domain/ports/__init__.py`

- [ ] **Step 1: Write ports package init**

```python
"""Domain ports - Abstract interfaces defined by the domain.

These are the contracts that infrastructure must implement.
The domain depends on these abstractions, not concrete implementations.
"""

from quant.domain.ports.data_feed import DataFeed, DataFeedCallback
from quant.domain.ports.broker import BrokerAdapter
from quant.domain.ports.strategy import Strategy, StrategyContext
from quant.domain.ports.storage import Storage
from quant.domain.ports.event_publisher import EventPublisher, EventSubscriber

__all__ = [
    "DataFeed",
    "DataFeedCallback",
    "BrokerAdapter",
    "Strategy",
    "StrategyContext",
    "Storage",
    "EventPublisher",
    "EventSubscriber",
]
```

- [ ] **Step 2: Commit**

```bash
git add quant/domain/ports/__init__.py
git commit -m "feat(domain): create ports package structure"
```

### Task 3.2: Create DataFeed Port

**Files:**
- Create: `quant/domain/ports/data_feed.py`

- [ ] **Step 1: Write DataFeed port**

```python
"""DataFeed port - Abstract interface for market data providers."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Callable, List, Optional
import pandas as pd

from quant.domain.models.bar import Bar


# Type alias for data callbacks
DataFeedCallback = Callable[[Bar], None]


class DataFeed(ABC):
    """Abstract port for market data feeds.
    
    Infrastructure adapters implement this interface to provide
    market data from various sources (Yahoo, Futu, DuckDB, etc.)
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Get provider name."""
        pass
    
    @abstractmethod
    def connect(self) -> None:
        """Connect to the data source."""
        pass
    
    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from the data source."""
        pass
    
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected to the data source."""
        pass
    
    @abstractmethod
    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1d",
    ) -> pd.DataFrame:
        """Get historical bar data.
        
        Args:
            symbol: Trading symbol
            start: Start datetime
            end: End datetime
            timeframe: Bar timeframe ("1m", "5m", "1h", "1d", etc.)
        
        Returns:
            DataFrame with OHLCV columns
        """
        pass
    
    @abstractmethod
    def subscribe(self, symbols: List[str], callback: DataFeedCallback) -> None:
        """Subscribe to real-time data for symbols.
        
        Args:
            symbols: List of symbols to subscribe to
            callback: Function called when new data arrives
        """
        pass
    
    @abstractmethod
    def unsubscribe(self, symbols: List[str]) -> None:
        """Unsubscribe from real-time data.
        
        Args:
            symbols: List of symbols to unsubscribe from
        """
        pass
```

- [ ] **Step 2: Test DataFeed port**

Run: `python -c "from quant.domain.ports.data_feed import DataFeed; print(f'DataFeed is abstract: {getattr(DataFeed, \"__abstractmethods__\", None) is not None}')"`  
Expected: `DataFeed is abstract: True`

- [ ] **Step 3: Commit**

```bash
git add quant/domain/ports/data_feed.py
git commit -m "feat(domain): add DataFeed port"
```

### Task 3.3: Create Broker Port

**Files:**
- Create: `quant/domain/ports/broker.py`

- [ ] **Step 1: Write Broker port**

```python
"""Broker port - Abstract interface for broker adapters."""

from abc import ABC, abstractmethod
from typing import List, Optional

from quant.domain.models.order import Order, OrderStatus
from quant.domain.models.position import Position
from quant.domain.models.account import AccountInfo
from quant.domain.models.fill import Fill


class BrokerAdapter(ABC):
    """Abstract port for broker adapters.
    
    Infrastructure adapters implement this interface to connect
    to various brokers (Paper, Futu, IBKR, etc.)
    """
    
    def __init__(self, name: str):
        self._name = name
        self._connected = False
    
    @property
    def name(self) -> str:
        """Get broker name."""
        return self._name
    
    @abstractmethod
    def connect(self) -> None:
        """Connect to the broker."""
        pass
    
    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from the broker."""
        pass
    
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected to broker."""
        pass
    
    @abstractmethod
    def submit_order(self, order: Order) -> str:
        """Submit an order to the broker.
        
        Args:
            order: Order to submit
        
        Returns:
            Broker order ID
        """
        pass
    
    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an existing order.
        
        Args:
            order_id: Order ID to cancel
        
        Returns:
            True if cancellation was successful
        """
        pass
    
    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderStatus:
        """Get the status of an order.
        
        Args:
            order_id: Order ID to check
        
        Returns:
            Current order status
        """
        pass
    
    @abstractmethod
    def get_positions(self) -> List[Position]:
        """Get all current positions.
        
        Returns:
            List of positions
        """
        pass
    
    @abstractmethod
    def get_account_info(self) -> AccountInfo:
        """Get account information.
        
        Returns:
            Account information
        """
        pass
    
    def update_price(self, symbol: str, price: float) -> None:
        """Update price for paper trading (optional).
        
        Brokers that don't support this do nothing.
        
        Args:
            symbol: Symbol to update
            price: Current price
        """
        pass
```

- [ ] **Step 2: Commit**

```bash
git add quant/domain/ports/broker.py
git commit -m "feat(domain): add BrokerAdapter port"
```

### Task 3.4: Create Strategy Port

**Files:**
- Create: `quant/domain/ports/strategy.py`

- [ ] **Step 1: Write Strategy port**

```python
"""Strategy port - Abstract interface for trading strategies."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from quant.domain.models.bar import Bar
from quant.domain.models.fill import Fill
from quant.domain.models.order import Order, OrderSide, OrderType

if TYPE_CHECKING:
    from quant.domain.ports.event_publisher import EventPublisher


@dataclass
class StrategyContext:
    """Context passed to strategies.
    
    This is a data container that provides strategies with
    access to necessary services without direct dependencies.
    """
    strategy_name: str
    event_publisher: "EventPublisher"
    data_provider: Any  # DataFeed - avoid circular import
    portfolio_accessor: Any  # Portfolio - avoid circular import
    
    # Configuration
    symbols: List[str] = None
    parameters: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.symbols is None:
            object.__setattr__(self, 'symbols', [])
        if self.parameters is None:
            object.__setattr__(self, 'parameters', {})


class Strategy(ABC):
    """Abstract port for trading strategies.
    
    Strategy implementations inherit from this class and
    override the lifecycle methods to define trading logic.
    
    The domain defines this interface - strategies in the
    strategies/ directory implement it.
    """
    
    def __init__(self, name: str):
        self._name = name
        self._context: Optional[StrategyContext] = None
        self._positions: Dict[str, float] = {}
        self._data: Dict[str, Any] = {}
    
    @property
    def name(self) -> str:
        """Get strategy name."""
        return self._name
    
    @property
    def context(self) -> Optional[StrategyContext]:
        """Get strategy context."""
        return self._context
    
    @property
    def symbols(self) -> List[str]:
        """Get symbols this strategy trades."""
        if self._context:
            return self._context.symbols
        return []
    
    # Lifecycle methods - override these in implementations
    
    def on_start(self, context: StrategyContext) -> None:
        """Called when strategy starts.
        
        Args:
            context: Strategy context with services
        """
        self._context = context
        self._load_data()
    
    def on_stop(self, context: StrategyContext) -> None:
        """Called when strategy stops.
        
        Args:
            context: Strategy context
        """
        self._positions.clear()
    
    def on_before_trading(self, context: StrategyContext, trading_date: date) -> None:
        """Called before market opens.
        
        Args:
            context: Strategy context
            trading_date: Current trading date
        """
        pass
    
    def on_after_trading(self, context: StrategyContext, trading_date: date) -> None:
        """Called after market closes.
        
        Args:
            context: Strategy context
            trading_date: Current trading date
        """
        pass
    
    @abstractmethod
    def on_bar(self, context: StrategyContext, bar: Bar) -> None:
        """Called on each bar of data.
        
        This is the main strategy logic entry point.
        
        Args:
            context: Strategy context
            bar: Bar data
        """
        pass
    
    def on_fill(self, context: StrategyContext, fill: Fill) -> None:
        """Called when an order is filled.
        
        Args:
            context: Strategy context
            fill: Fill information
        """
        # Update internal position tracking
        qty = fill.quantity if fill.side == "BUY" else -fill.quantity
        self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty
    
    def on_order_rejected(self, context: StrategyContext, order: Order, reason: str) -> None:
        """Called when an order is rejected.
        
        Args:
            context: Strategy context
            order: Rejected order
            reason: Rejection reason
        """
        pass
    
    # Helper methods for strategy implementations
    
    def get_position(self, symbol: str) -> float:
        """Get current position for a symbol."""
        return self._positions.get(symbol, 0)
    
    def get_all_positions(self) -> Dict[str, float]:
        """Get all current positions."""
        return self._positions.copy()
    
    def buy(self, symbol: str, quantity: float, order_type: OrderType = OrderType.MARKET, price: Optional[float] = None) -> Optional[str]:
        """Submit a buy order.
        
        Args:
            symbol: Symbol to buy
            quantity: Quantity to buy
            order_type: Order type
            price: Limit price (for limit orders)
        
        Returns:
            Order ID if submitted, None otherwise
        """
        return self._submit_order(symbol, quantity, OrderSide.BUY, order_type, price)
    
    def sell(self, symbol: str, quantity: float, order_type: OrderType = OrderType.MARKET, price: Optional[float] = None) -> Optional[str]:
        """Submit a sell order.
        
        Args:
            symbol: Symbol to sell
            quantity: Quantity to sell
            order_type: Order type
            price: Limit price (for limit orders)
        
        Returns:
            Order ID if submitted, None otherwise
        """
        return self._submit_order(symbol, quantity, OrderSide.SELL, order_type, price)
    
    def _submit_order(
        self,
        symbol: str,
        quantity: float,
        side: OrderSide,
        order_type: OrderType,
        price: Optional[float]
    ) -> Optional[str]:
        """Internal order submission helper."""
        if not self._context:
            return None
        
        # Create order
        order = Order(
            symbol=symbol,
            quantity=quantity,
            side=side,
            order_type=order_type,
            price=price,
            strategy_name=self._name,
        )
        
        # Submit via portfolio accessor (injected dependency)
        if hasattr(self._context.portfolio_accessor, 'submit_order'):
            return self._context.portfolio_accessor.submit_order(order)
        
        return None
    
    def _load_data(self) -> None:
        """Load historical data for strategy initialization.
        Override in subclasses to load required data.
        """
        pass
    
    def _store_data(self, key: str, value: Any) -> None:
        """Store strategy-specific data."""
        self._data[key] = value
    
    def _get_data(self, key: str, default: Any = None) -> Any:
        """Retrieve strategy-specific data."""
        return self._data.get(key, default)
```

- [ ] **Step 2: Commit**

```bash
git add quant/domain/ports/strategy.py
git commit -m "feat(domain): add Strategy port with lifecycle methods"
```

### Task 3.5: Create Storage Port

**Files:**
- Create: `quant/domain/ports/storage.py`

- [ ] **Step 1: Write Storage port**

```python
"""Storage port - Abstract interface for data persistence."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional
import pandas as pd


class Storage(ABC):
    """Abstract port for data storage.
    
    Infrastructure adapters implement this to provide
    persistence via SQLite, DuckDB, Parquet, etc.
    """
    
    @abstractmethod
    def save_bars(
        self,
        df: pd.DataFrame,
        timeframe: str,
        provider: str,
    ) -> None:
        """Save bars to storage.
        
        Args:
            df: DataFrame with OHLCV data
            timeframe: Bar timeframe
            provider: Data provider name
        """
        pass
    
    @abstractmethod
    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str,
        provider: str,
    ) -> pd.DataFrame:
        """Get bars from storage.
        
        Args:
            symbol: Trading symbol
            start: Start datetime
            end: End datetime
            timeframe: Bar timeframe
            provider: Data provider name
        
        Returns:
            DataFrame with OHLCV columns
        """
        pass
    
    @abstractmethod
    def save_order(self, order: Dict[str, Any]) -> None:
        """Save order to storage.
        
        Args:
            order: Order data dictionary
        """
        pass
    
    @abstractmethod
    def get_orders(
        self,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
    ) -> pd.DataFrame:
        """Get orders from storage.
        
        Args:
            symbol: Filter by symbol
            status: Filter by status
        
        Returns:
            DataFrame with order data
        """
        pass
    
    @abstractmethod
    def save_portfolio_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """Save portfolio snapshot.
        
        Args:
            snapshot: Portfolio snapshot data
        """
        pass
    
    @abstractmethod
    def get_portfolio_snapshots(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """Get portfolio snapshots.
        
        Args:
            start: Filter by start date
            end: Filter by end date
        
        Returns:
            DataFrame with portfolio snapshots
        """
        pass
    
    @abstractmethod
    def close(self) -> None:
        """Close storage connection."""
        pass
```

- [ ] **Step 2: Commit**

```bash
git add quant/domain/ports/storage.py
git commit -m "feat(domain): add Storage port"
```

### Task 3.6: Create EventPublisher Port

**Files:**
- Create: `quant/domain/ports/event_publisher.py`

- [ ] **Step 1: Write EventPublisher port**

```python
"""EventPublisher port - Abstract interface for event publishing/subscribing."""

from abc import ABC, abstractmethod
from typing import Callable, List

from quant.domain.events.base import Event, EventType


# Type alias for event handlers
EventSubscriber = Callable[[Event], None]


class EventPublisher(ABC):
    """Abstract port for event publishing.
    
    Infrastructure implements this to provide event bus
    functionality for decoupled communication.
    """
    
    @abstractmethod
    def subscribe(self, event_type: EventType, handler: EventSubscriber) -> None:
        """Subscribe to events of a specific type.
        
        Args:
            event_type: Type of event to subscribe to
            handler: Callback function for events
        """
        pass
    
    @abstractmethod
    def unsubscribe(self, event_type: EventType, handler: EventSubscriber) -> None:
        """Unsubscribe from events.
        
        Args:
            event_type: Type of event
            handler: Handler to remove
        """
        pass
    
    @abstractmethod
    def publish(self, event: Event) -> None:
        """Publish an event to all subscribers.
        
        Args:
            event: Event to publish
        """
        pass
    
    def publish_nowait(self, event: Event) -> None:
        """Publish event without waiting (fire and forget).
        
        Default implementation calls publish synchronously.
        Adapters may override for async behavior.
        
        Args:
            event: Event to publish
        """
        self.publish(event)
    
    @abstractmethod
    def clear(self) -> None:
        """Clear all subscriptions."""
        pass
```

- [ ] **Step 2: Commit**

```bash
git add quant/domain/ports/event_publisher.py
git commit -m "feat(domain): add EventPublisher port"
```

---

## Phase 4: Infrastructure Layer - Adapters

**Goal:** Implement domain ports using concrete technologies.

### Task 4.1: Update EventBus to Implement EventPublisher

**Files:**
- Modify: `quant/infrastructure/events/event_bus.py`

- [ ] **Step 1: Update EventBus to implement EventPublisher port**

```python
"""EventBus implementation - Implements EventPublisher port."""

from typing import Callable, Dict, List
import threading

from quant.domain.events.base import Event, EventType
from quant.domain.ports.event_publisher import EventPublisher, EventSubscriber
from quant.shared.utils.logger import setup_logger

_logger = setup_logger("EventBus")


class EventBus(EventPublisher):
    """EventBus adapter implementing EventPublisher port.
    
    Provides publish/subscribe messaging between components.
    Thread-safe implementation using RLock.
    """
    
    def __init__(self):
        self._subscribers: Dict[EventType, List[EventSubscriber]] = {}
        self._lock = threading.RLock()
    
    def subscribe(self, event_type: EventType, handler: EventSubscriber) -> None:
        """Subscribe to events of a specific type.
        
        Args:
            event_type: Type of event to subscribe to
            handler: Callback function for events
        """
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(handler)
    
    def unsubscribe(self, event_type: EventType, handler: EventSubscriber) -> None:
        """Unsubscribe from events.
        
        Args:
            event_type: Type of event
            handler: Handler to remove
        """
        with self._lock:
            if event_type in self._subscribers:
                try:
                    self._subscribers[event_type].remove(handler)
                except ValueError:
                    pass
    
    def publish(self, event: Event) -> None:
        """Publish an event to all subscribers.
        
        Args:
            event: Event to publish
        """
        with self._lock:
            callbacks = self._subscribers.get(event.event_type, []).copy()
        
        for callback in callbacks:
            try:
                callback(event)
            except Exception as e:
                _logger.error(f"Error in event handler: {e}")
    
    def clear(self) -> None:
        """Clear all subscriptions."""
        with self._lock:
            self._subscribers.clear()
```

- [ ] **Step 2: Test EventBus implements port**

Run: `python -c "from quant.infrastructure.events.event_bus import EventBus; from quant.domain.ports.event_publisher import EventPublisher; print(f'Is EventPublisher: {issubclass(EventBus, EventPublisher)}')"`
Expected: `Is EventPublisher: True`

- [ ] **Step 3: Commit**

```bash
git add quant/infrastructure/events/event_bus.py
git commit -m "refactor(infrastructure): EventBus implements EventPublisher port"
```

### Task 4.2: Create Infrastructure Feeds Package

**Files:**
- Create: `quant/infrastructure/feeds/__init__.py`

- [ ] **Step 1: Create feeds package init**

```python
"""Data feed adapters - Implement DataFeed port."""

from quant.infrastructure.feeds.duckdb import DuckDBFeed
from quant.infrastructure.feeds.yahoo import YahooFeed

__all__ = ["DuckDBFeed", "YahooFeed"]
```

- [ ] **Step 2: Commit**

```bash
git add quant/infrastructure/feeds/__init__.py
git commit -m "feat(infrastructure): create feeds package"
```

### Task 4.3: Move and Update DuckDB Provider to Feed Adapter

**Files:**
- Create: `quant/infrastructure/feeds/duckdb.py`
- Reference: `quant/infrastructure/data/providers/duckdb_provider.py` (read only)

- [ ] **Step 1: Read existing DuckDB provider**

Run: `cat quant/infrastructure/data/providers/duckdb_provider.py`

- [ ] **Step 2: Create DuckDBFeed adapter**

```python
"""DuckDB feed adapter - Implements DataFeed port."""

from datetime import datetime
from typing import Callable, List, Optional
import pandas as pd
import duckdb

from quant.domain.models.bar import Bar
from quant.domain.ports.data_feed import DataFeed, DataFeedCallback


class DuckDBFeed(DataFeed):
    """DuckDB data feed adapter.
    
    Implements DataFeed port to provide market data from DuckDB.
    """
    
    def __init__(self, db_path: str = "quant.duckdb"):
        self._db_path = db_path
        self._conn: Optional[duckdb.DuckDBPyConnection] = None
        self._connected = False
        self._subscriptions: Dict[str, List[DataFeedCallback]] = {}
    
    @property
    def name(self) -> str:
        return "duckdb"
    
    def connect(self) -> None:
        """Connect to DuckDB."""
        if not self._connected:
            self._conn = duckdb.connect(self._db_path)
            self._connected = True
    
    def disconnect(self) -> None:
        """Disconnect from DuckDB."""
        if self._conn:
            self._conn.close()
            self._conn = None
        self._connected = False
    
    def is_connected(self) -> bool:
        """Check if connected."""
        return self._connected and self._conn is not None
    
    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1d",
    ) -> pd.DataFrame:
        """Get historical bars from DuckDB."""
        if not self.is_connected():
            self.connect()
        
        query = """
            SELECT timestamp, symbol, open, high, low, close, volume
            FROM bars
            WHERE symbol = ? 
            AND timestamp >= ? 
            AND timestamp <= ?
            AND timeframe = ?
            ORDER BY timestamp ASC
        """
        
        df = self._conn.execute(
            query, 
            [symbol, start.isoformat(), end.isoformat(), timeframe]
        ).fetchdf()
        
        return df
    
    def subscribe(self, symbols: List[str], callback: DataFeedCallback) -> None:
        """Subscribe to real-time data (not supported by DuckDB)."""
        # DuckDB is historical only - no real-time subscriptions
        pass
    
    def unsubscribe(self, symbols: List[str]) -> None:
        """Unsubscribe from real-time data."""
        pass
```

- [ ] **Step 3: Commit**

```bash
git add quant/infrastructure/feeds/duckdb.py
git commit -m "feat(infrastructure): add DuckDBFeed adapter implementing DataFeed port"
```

### Task 4.4: Move and Update Yahoo Provider to Feed Adapter

**Files:**
- Create: `quant/infrastructure/feeds/yahoo.py`

- [ ] **Step 1: Create YahooFeed adapter**

```python
"""Yahoo Finance feed adapter - Implements DataFeed port."""

from datetime import datetime
from typing import Callable, List, Optional
import pandas as pd

from quant.domain.models.bar import Bar
from quant.domain.ports.data_feed import DataFeed, DataFeedCallback


class YahooFeed(DataFeed):
    """Yahoo Finance data feed adapter.
    
    Implements DataFeed port to provide market data from Yahoo Finance.
    """
    
    def __init__(self):
        self._connected = False
        self._subscriptions: Dict[str, List[DataFeedCallback]] = {}
    
    @property
    def name(self) -> str:
        return "yahoo"
    
    def connect(self) -> None:
        """Connect to Yahoo Finance."""
        self._connected = True
    
    def disconnect(self) -> None:
        """Disconnect from Yahoo Finance."""
        self._connected = False
    
    def is_connected(self) -> bool:
        """Check if connected."""
        return self._connected
    
    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1d",
    ) -> pd.DataFrame:
        """Get historical bars from Yahoo Finance."""
        try:
            import yfinance as yf
            
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start, end=end, interval=timeframe)
            
            # Rename columns to standard format
            df = df.rename(columns={
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            })
            
            # Add symbol column
            df["symbol"] = symbol
            
            return df.reset_index()
        except ImportError:
            raise RuntimeError("yfinance not installed")
        except Exception as e:
            raise RuntimeError(f"Failed to fetch data from Yahoo: {e}")
    
    def subscribe(self, symbols: List[str], callback: DataFeedCallback) -> None:
        """Subscribe to real-time data (not supported)."""
        pass
    
    def unsubscribe(self, symbols: List[str]) -> None:
        """Unsubscribe from real-time data."""
        pass
```

- [ ] **Step 2: Commit**

```bash
git add quant/infrastructure/feeds/yahoo.py
git commit -m "feat(infrastructure): add YahooFeed adapter implementing DataFeed port"
```

### Task 4.5: Update Paper Broker to Implement Broker Port

**Files:**
- Modify: `quant/infrastructure/execution/brokers/paper.py`
- Reference: `quant/infrastructure/execution/brokers/base.py` (remove after migration)

- [ ] **Step 1: Read existing Paper broker**

Run: `cat quant/infrastructure/execution/brokers/paper.py`

- [ ] **Step 2: Update Paper broker to implement Broker port**

```python
"""Paper trading broker adapter - Implements Broker port."""

from datetime import datetime
from typing import Dict, List, Optional
import threading
import uuid

from quant.domain.models.order import Order, OrderStatus
from quant.domain.models.position import Position
from quant.domain.models.account import AccountInfo
from quant.domain.models.fill import Fill
from quant.domain.ports.broker import BrokerAdapter


class PaperBroker(BrokerAdapter):
    """Paper trading broker adapter.
    
    Implements Broker port for simulated trading without real money.
    """
    
    def __init__(self, initial_cash: float = 100000.0):
        super().__init__("paper")
        self._initial_cash = initial_cash
        self._cash = initial_cash
        self._positions: Dict[str, Position] = {}
        self._orders: Dict[str, Order] = {}
        self._fills: List[Fill] = []
        self._prices: Dict[str, float] = {}
        self._lock = threading.RLock()
    
    def connect(self) -> None:
        """Connect to paper trading."""
        self._connected = True
    
    def disconnect(self) -> None:
        """Disconnect from paper trading."""
        self._connected = False
    
    def is_connected(self) -> bool:
        """Check if connected."""
        return self._connected
    
    def submit_order(self, order: Order) -> str:
        """Submit order for paper trading."""
        with self._lock:
            # Generate broker order ID
            broker_id = f"paper_{uuid.uuid4().hex[:12].upper()}"
            
            # Store order
            self._orders[broker_id] = order
            
            # Execute immediately at last known price
            price = self._prices.get(order.symbol, 100.0)
            
            # Create fill
            fill = Fill(
                order_id=broker_id,
                symbol=order.symbol,
                side=order.side.value,
                quantity=order.quantity,
                price=price,
                commission=0.0,
                timestamp=datetime.now(),
                fill_id=f"fill_{uuid.uuid4().hex[:8]}",
                strategy_name=order.strategy_name,
            )
            
            self._fills.append(fill)
            
            # Update positions
            self._update_position(fill)
            
            return broker_id
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        with self._lock:
            if order_id in self._orders:
                order = self._orders[order_id]
                if order.is_active:
                    self._orders[order_id] = order.with_status(OrderStatus.CANCELLED)
                    return True
            return False
    
    def get_order_status(self, order_id: str) -> OrderStatus:
        """Get order status."""
        with self._lock:
            order = self._orders.get(order_id)
            return order.status if order else OrderStatus.REJECTED
    
    def get_positions(self) -> List[Position]:
        """Get all positions."""
        with self._lock:
            return list(self._positions.values())
    
    def get_account_info(self) -> AccountInfo:
        """Get account info."""
        with self._lock:
            # Calculate equity
            positions_value = sum(
                pos.market_value for pos in self._positions.values()
            )
            equity = self._cash + positions_value
            
            return AccountInfo(
                account_id="paper_account",
                cash=self._cash,
                buying_power=self._cash * 2,  # 2x margin
                equity=equity,
                currency="USD",
            )
    
    def update_price(self, symbol: str, price: float) -> None:
        """Update price for paper trading."""
        with self._lock:
            self._prices[symbol] = price
            
            # Update position market values
            if symbol in self._positions:
                self._positions[symbol].update_market_price(price)
    
    def _update_position(self, fill: Fill) -> None:
        """Update position based on fill."""
        symbol = fill.symbol
        qty = fill.quantity if fill.side == "BUY" else -fill.quantity
        
        if symbol not in self._positions:
            self._positions[symbol] = Position(symbol=symbol)
        
        self._positions[symbol].update_from_fill(qty, fill.price)
        
        # Update cash
        if fill.side == "BUY":
            self._cash -= fill.value
        else:
            self._cash += fill.net_value
```

- [ ] **Step 3: Commit**

```bash
git add quant/infrastructure/execution/brokers/paper.py
git commit -m "refactor(infrastructure): PaperBroker implements Broker port"
```

### Task 4.6: Update Futu Broker to Implement Broker Port

**Files:**
- Modify: `quant/infrastructure/execution/brokers/futu.py`

- [ ] **Step 1: Update Futu broker to implement Broker port**

```python
"""Futu broker adapter - Implements Broker port."""

from typing import List, Optional

from quant.domain.models.order import Order, OrderStatus
from quant.domain.models.position import Position
from quant.domain.models.account import AccountInfo
from quant.domain.ports.broker import BrokerAdapter


class FutuBroker(BrokerAdapter):
    """Futu broker adapter.
    
    Implements Broker port for Futu OpenAPI.
    """
    
    def __init__(self, api_key: str, api_secret: str, host: str = "127.0.0.1", port: int = 11111):
        super().__init__("futu")
        self._api_key = api_key
        self._api_secret = api_secret
        self._host = host
        self._port = port
        self._context = None  # Futu OpenD context
    
    def connect(self) -> None:
        """Connect to Futu OpenD."""
        try:
            from futu import OpenQuoteContext, OpenTradeContext
            
            self._context = OpenTradeContext(
                host=self._host,
                port=self._port,
            )
            self._connected = True
        except ImportError:
            raise RuntimeError("futu-api not installed")
    
    def disconnect(self) -> None:
        """Disconnect from Futu OpenD."""
        if self._context:
            self._context.close()
            self._context = None
        self._connected = False
    
    def is_connected(self) -> bool:
        """Check if connected."""
        return self._connected and self._context is not None
    
    def submit_order(self, order: Order) -> str:
        """Submit order to Futu."""
        if not self.is_connected():
            raise RuntimeError("Not connected to Futu")
        
        # Map order to Futu API
        # Implementation depends on futu-api specifics
        raise NotImplementedError("Futu order submission not yet implemented")
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel order on Futu."""
        if not self.is_connected():
            return False
        
        raise NotImplementedError("Futu order cancellation not yet implemented")
    
    def get_order_status(self, order_id: str) -> OrderStatus:
        """Get order status from Futu."""
        if not self.is_connected():
            return OrderStatus.REJECTED
        
        raise NotImplementedError("Futu order status not yet implemented")
    
    def get_positions(self) -> List[Position]:
        """Get positions from Futu."""
        if not self.is_connected():
            return []
        
        raise NotImplementedError("Futu positions not yet implemented")
    
    def get_account_info(self) -> AccountInfo:
        """Get account info from Futu."""
        if not self.is_connected():
            raise RuntimeError("Not connected to Futu")
        
        raise NotImplementedError("Futu account info not yet implemented")
```

- [ ] **Step 2: Commit**

```bash
git add quant/infrastructure/execution/brokers/futu.py
git commit -m "refactor(infrastructure): FutuBroker implements Broker port"
```

---

## Phase 5: Features Layer - Orchestrators

**Goal:** Rebuild feature orchestrators to use domain layer and injected ports.

### Task 5.1: Create StrategyContext for Features

**Files:**
- Create: `quant/features/trading/context.py`

- [ ] **Step 1: Write StrategyContext implementation**

```python
"""StrategyContext for trading features.

Implements StrategyContext port using dependency injection.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List

from quant.domain.ports.event_publisher import EventPublisher
from quant.domain.ports.data_feed import DataFeed

if TYPE_CHECKING:
    from quant.features.trading.portfolio import Portfolio


@dataclass
class TradingContext:
    """Context for strategies in trading features.
    
    This implements the StrategyContext port from domain.
    """
    strategy_name: str
    event_publisher: EventPublisher
    data_provider: DataFeed
    portfolio: "Portfolio"
    symbols: List[str] = None
    parameters: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.symbols is None:
            object.__setattr__(self, 'symbols', [])
        if self.parameters is None:
            object.__setattr__(self, 'parameters', {})
    
    @property
    def portfolio_accessor(self) -> "Portfolio":
        """Get portfolio accessor."""
        return self.portfolio
```

- [ ] **Step 2: Commit**

```bash
git add quant/features/trading/context.py
git commit -m "feat(features): add TradingContext for strategy DI"
```

### Task 5.2: Update Trading Engine

**Files:**
- Modify: `quant/features/trading/engine.py`

- [ ] **Step 1: Update engine to use domain ports**

```python
"""Trading engine - Orchestrates live/paper trading using domain ports."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Any
import threading
import time

from quant.domain.events.base import EventType
from quant.domain.events.market_events import MarketOpenEvent, MarketCloseEvent
from quant.domain.ports.event_publisher import EventPublisher
from quant.domain.ports.data_feed import DataFeed
from quant.domain.ports.broker import BrokerAdapter
from quant.features.trading.scheduler import Scheduler
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.trading.context import TradingContext
from quant.shared.utils.logger import setup_logger


class SystemMode(Enum):
    """System operation modes."""
    LIVE = "live"
    PAPER = "paper"
    BACKTEST = "backtest"


class TradingEngine:
    """Main trading engine - orchestrates trading operations.
    
    This is an application layer orchestrator that coordinates
    domain objects and injected infrastructure adapters.
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.mode = SystemMode(config.get("system", {}).get("mode", "paper"))
        self.logger = setup_logger("TradingEngine", config.get("system", {}).get("log_level", "INFO"))
        
        # Infrastructure adapters (injected)
        self._event_publisher: Optional[EventPublisher] = None
        self._data_feed: Optional[DataFeed] = None
        self._broker: Optional[BrokerAdapter] = None
        
        # Domain objects
        self._portfolio = Portfolio(
            initial_cash=config.get("system", {}).get("initial_cash", 100000),
            currency=config.get("system", {}).get("currency", "USD"),
        )
        self._risk_engine = RiskEngine(config, self._portfolio)
        self._scheduler = Scheduler(config)
        
        # Strategies
        self._strategies: List[Any] = []
        self._subscribed_symbols: List[str] = []
        
        # State
        self._running = False
        self._thread: Optional[threading.Thread] = None
    
    def set_event_publisher(self, publisher: EventPublisher) -> None:
        """Inject event publisher adapter."""
        self._event_publisher = publisher
    
    def set_data_feed(self, feed: DataFeed) -> None:
        """Inject data feed adapter."""
        self._data_feed = feed
    
    def set_broker(self, broker: BrokerAdapter) -> None:
        """Inject broker adapter."""
        self._broker = broker
    
    def add_strategy(self, strategy) -> None:
        """Add a strategy to the engine."""
        from quant.domain.ports.strategy import Strategy
        
        if not isinstance(strategy, Strategy):
            raise ValueError("Strategy must implement Strategy port")
        
        # Create context for strategy
        context = TradingContext(
            strategy_name=strategy.name,
            event_publisher=self._event_publisher,
            data_provider=self._data_feed,
            portfolio=self._portfolio,
        )
        
        strategy.on_start(context)
        self._strategies.append(strategy)
        
        # Subscribe strategy to events
        if self._event_publisher:
            self._event_publisher.subscribe(
                EventType.BAR, 
                lambda event, s=strategy: self._dispatch_bar(s, event)
            )
    
    def _dispatch_bar(self, strategy, event) -> None:
        """Dispatch bar event to strategy."""
        from quant.domain.events.market_events import BarEvent
        
        if isinstance(event, BarEvent) and strategy.context:
            strategy.on_bar(strategy.context, event.bar)
    
    def subscribe(self, symbols: List[str]) -> None:
        """Subscribe to symbols for data."""
        self._subscribed_symbols.extend(symbols)
        
        if self._data_feed:
            for symbol in symbols:
                self._data_feed.subscribe([symbol], self._on_bar_data)
    
    def _on_bar_data(self, bar) -> None:
        """Handle incoming bar data."""
        from quant.domain.events.market_events import BarEvent
        
        if self._event_publisher:
            event = BarEvent(bar=bar, source=self._data_feed.name if self._data_feed else None)
            self._event_publisher.publish(event)
        
        # Update broker prices for paper trading
        if self._broker and hasattr(self._broker, 'update_price'):
            self._broker.update_price(bar.symbol, bar.close)
    
    def start(self) -> None:
        """Start the engine."""
        if self._running:
            return
        
        self.logger.info(f"Starting engine in {self.mode.value} mode")
        
        # Connect adapters
        if self._data_feed:
            self._data_feed.connect()
        if self._broker:
            self._broker.connect()
        
        self._running = True
        self._scheduler.start()
        
        # Start main loop
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        
        # Emit start event
        if self._event_publisher:
            from quant.domain.events.system_events import SystemStartEvent
            self._event_publisher.publish(
                SystemStartEvent(mode=self.mode.value, config=self.config)
            )
    
    def stop(self) -> None:
        """Stop the engine."""
        if not self._running:
            return
        
        self.logger.info("Stopping engine")
        self._running = False
        
        self._scheduler.stop()
        
        # Stop strategies
        for strategy in self._strategies:
            if strategy.context:
                strategy.on_stop(strategy.context)
        
        # Disconnect adapters
        if self._data_feed:
            self._data_feed.disconnect()
        if self._broker:
            self._broker.disconnect()
        
        # Emit stop event
        if self._event_publisher:
            from quant.domain.events.system_events import SystemStopEvent
            self._event_publisher.publish(
                SystemStopEvent(reason="manual_stop")
            )
    
    def _run_loop(self) -> None:
        """Main engine loop."""
        was_market_open = False
        
        while self._running:
            try:
                is_open = self._is_market_open()
                
                if is_open and not was_market_open:
                    # Market just opened
                    if self._event_publisher:
                        self._event_publisher.publish(MarketOpenEvent(market="US"))
                elif not is_open and was_market_open:
                    # Market just closed
                    if self._event_publisher:
                        self._event_publisher.publish(MarketCloseEvent(market="US"))
                
                was_market_open = is_open
                
            except Exception as e:
                self.logger.error(f"Engine loop error: {e}")
            
            time.sleep(60)  # Check every minute
    
    def _is_market_open(self) -> bool:
        """Check if market is open."""
        from quant.shared.utils.datetime import get_current_time, is_market_open
        
        market_config = self.config.get("markets", {}).get("US", {})
        now = get_current_time(market_config.get("timezone", "America/New_York"))
        
        return is_market_open(
            now,
            market_config.get("open_hour", 9),
            market_config.get("open_minute", 30),
            market_config.get("close_hour", 16),
            market_config.get("close_minute", 0),
        )
```

- [ ] **Step 2: Commit**

```bash
git add quant/features/trading/engine.py
git commit -m "refactor(features): TradingEngine uses domain ports and DI"
```

---

## Phase 6: Strategies Layer

**Goal:** Ensure all strategies implement the Strategy port correctly.

### Task 6.1: Update SimpleMomentum Strategy

**Files:**
- Modify: `quant/strategies/simple_momentum/strategy.py`

- [ ] **Step 1: Move and update SimpleMomentum**

```python
"""Simple momentum strategy implementation."""

from quant.domain.ports.strategy import Strategy, StrategyContext
from quant.domain.models.bar import Bar


class SimpleMomentumStrategy(Strategy):
    """Simple momentum trading strategy.
    
    Buys when price is above N-day moving average.
    Sells when price is below N-day moving average.
    """
    
    def __init__(self, name: str = "SimpleMomentum", lookback: int = 20):
        super().__init__(name)
        self.lookback = lookback
    
    def on_bar(self, context: StrategyContext, bar: Bar) -> None:
        """Process new bar data."""
        # Store price history
        prices = self._get_data("prices", [])
        prices.append(bar.close)
        
        if len(prices) > self.lookback * 2:
            prices.pop(0)
        
        self._store_data("prices", prices)
        
        # Need enough data
        if len(prices) < self.lookback:
            return
        
        # Calculate moving average
        ma = sum(prices[-self.lookback:]) / self.lookback
        
        # Get current position
        position = self.get_position(bar.symbol)
        
        # Trading logic
        if bar.close > ma and position <= 0:
            # Buy signal
            if position < 0:
                # Close short first
                self.buy(bar.symbol, abs(position))
            # Open long
            self.buy(bar.symbol, 100)
        elif bar.close < ma and position >= 0:
            # Sell signal
            if position > 0:
                # Close long
                self.sell(bar.symbol, position)
            # Open short (if supported)
            # self.sell(bar.symbol, 100)
```

- [ ] **Step 2: Commit**

```bash
git add quant/strategies/simple_momentum/strategy.py
git commit -m "refactor(strategies): SimpleMomentum implements Strategy port"
```

---

## Phase 7: Migration and Cleanup

### Task 7.1: Create Compatibility Imports

**Files:**
- Modify: `quant/shared/models/__init__.py`

- [ ] **Step 1: Update shared.models to re-export from domain**

```python
"""Shared models - Re-export from domain for backward compatibility."""

# Re-export domain models for compatibility during migration
from quant.domain.models.order import Order, OrderSide, OrderType, OrderStatus
from quant.domain.models.position import Position
from quant.domain.models.trade import Trade
from quant.domain.models.fill import Fill
from quant.domain.models.bar import Bar
from quant.domain.models.account import AccountInfo

__all__ = [
    "Order",
    "OrderSide",
    "OrderType", 
    "OrderStatus",
    "Position",
    "Trade",
    "Fill",
    "Bar",
    "AccountInfo",
]
```

- [ ] **Step 2: Commit**

```bash
git add quant/shared/models/__init__.py
git commit -m "refactor(shared): re-export domain models for compatibility"
```

### Task 7.2: Run Tests

- [ ] **Step 1: Run domain tests**

Run: `python -m pytest quant/tests/domain/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 2: Run feature tests**

Run: `python -m pytest quant/tests/features/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git commit -m "test: all tests pass after hexagonal refactor"
```

### Task 7.3: Update Documentation

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Add architecture section to AGENTS.md**

Add to end of AGENTS.md:

```markdown
## Hexagonal Architecture

This codebase follows Hexagonal Architecture (Ports & Adapters) with Event-Driven communication.

### Layers

```
domain/          # Pure business logic, zero dependencies
├── models/      # Order, Position, Trade, Fill, Bar, AccountInfo
├── events/      # Domain events (immutable, dataclasses)
└── ports/       # Abstract interfaces (DataFeed, BrokerAdapter, Strategy, Storage, EventPublisher)

infrastructure/  # Implements domain ports
├── feeds/       # DataFeed implementations (DuckDB, Yahoo, Futu)
├── brokers/     # BrokerAdapter implementations (Paper, Futu, IBKR)
├── storage/     # Storage implementations (DuckDB)
└── events/      # EventPublisher implementation (EventBus)

features/        # Application orchestrators
├── trading/     # TradingEngine
├── backtest/    # BacktestEngine
└── portfolio/   # PortfolioCoordinator

strategies/      # Strategy implementations (plugins)
└── */strategy.py  # Implements Strategy port
```

### Dependency Rules

1. **Domain** has ZERO external dependencies
2. **Features** depend only on Domain (ports injected via DI)
3. **Infrastructure** implements Domain ports
4. **Strategies** implement Strategy port
5. Communication via: Direct calls, Event Bus, Dependency Injection

### Key Interfaces (Ports)

```python
from quant.domain.ports.data_feed import DataFeed
from quant.domain.ports.broker import BrokerAdapter
from quant.domain.ports.strategy import Strategy, StrategyContext
from quant.domain.ports.storage import Storage
from quant.domain.ports.event_publisher import EventPublisher
```
```

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs: document hexagonal architecture"
```

---

## Summary

This plan rebuilds the system using:

1. **Hexagonal Architecture** - Domain at center with ports/adapters
2. **Event-Driven** - Async communication via EventPublisher port
3. **Dependency Injection** - Adapters injected into features
4. **Clean Separation** - Domain has zero dependencies
5. **Plugin Architecture** - Strategies are swappable plugins

The architecture fits Hexagonal pattern perfectly:
- **Domain** = Core business logic
- **Ports** = Abstract interfaces (defined by domain)
- **Adapters** = Infrastructure implementations
- **Features** = Application orchestrators
- **Strategies** = Business logic plugins

**Why `strategies/` separate from `features/`?**
- `features/` = Orchestration (HOW to run)
- `strategies/` = Business logic (WHAT to trade)
- This follows **DDD** and **Plugin Architecture** patterns
- Strategies can be developed/tested independently
- Hot-swappable without changing orchestration code
