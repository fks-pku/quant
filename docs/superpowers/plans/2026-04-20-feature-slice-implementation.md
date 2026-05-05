# Feature-Slice 重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有水平分层架构重构为 Feature-Slice 垂直切片，解决"改A错B"的耦合问题，同时保持API路径不变、前端零改动。

**Architecture:** Feature-Slice 垂直切片 + 基础设施水平层。每个功能（backtest/trading/portfolio/cio/strategies）自成一体，通过 service.py 对外暴露契约，api/ 层仅做薄路由转发。

**Tech Stack:** Python 3.10+, Flask, pytest

---

## 文件结构总览

### 新目录结构
```
quant/
├── features/                    # 垂直切片
│   ├── backtest/
│   ├── trading/
│   ├── portfolio/
│   ├── cio/
│   └── strategies/
├── infrastructure/              # 基础设施
│   ├── data/
│   ├── execution/
│   └── events/
├── shared/                      # 纯共享
│   ├── models/
│   ├── utils/
│   └── config/
├── api/                         # 薄路由层
└── tests/                       # 测试
```

### 文件搬迁映射（关键）
| 原路径 | 新路径 | 说明 |
|--------|--------|------|
| `core/events.py` | `infrastructure/events/event_bus.py` | 事件总线 |
| `models/*` | `shared/models/*` | 领域模型 |
| `utils/*` | `shared/utils/*` | 工具 |
| `config/*` | `shared/config/*` | 配置 |
| `data/*` | `infrastructure/data/*` | 数据层 |
| `execution/brokers/*` | `infrastructure/execution/brokers/*` | 券商适配 |
| `execution/order_manager.py` | `infrastructure/execution/order_manager.py` | 订单管理 |
| `execution/fill_handler.py` | `infrastructure/execution/fill_handler.py` | 成交处理 |
| `core/portfolio.py` | `features/trading/portfolio.py` | 交易组合 |
| `core/risk.py` | `features/trading/risk.py` | 风控 |
| `core/scheduler.py` | `features/trading/scheduler.py` | 调度器 |
| `core/engine.py` | `features/trading/engine.py` | 交易引擎 |
| `core/backtester.py` | `features/backtest/engine.py` | 回测引擎 |
| `core/walkforward.py` | `features/backtest/walkforward.py` | 步进验证 |
| `core/analytics.py` | `features/backtest/analytics.py` | 绩效分析 |
| `execution/portfolio_coordinator.py` | `features/portfolio/coordinator.py` | 组合协调 |
| `execution/strategy_position_tracker.py` | `features/portfolio/tracker.py` | 仓位跟踪 |
| `strategies/*` | `features/strategies/*` | 策略框架 |
| `cio/*` | `features/cio/*` | CIO模块 |

---

## Phase 1: 创建骨架目录 + shared/ 迁移

**目标:** 建立新目录结构，迁移无依赖的 shared 层

---

### Task 1.1: 创建新目录结构

**Files:**
- Create directories: `quant/features/`, `quant/infrastructure/`, `quant/shared/`

- [ ] **Step 1: Create directory skeleton**

Run:
```bash
cd /Users/bytedance/Desktop/vk/quant/.vibe-kanban-workspaces/a9c9-/quant/quant
mkdir -p features/{backtest,trading,portfolio,cio,strategies}
mkdir -p infrastructure/{data/{providers},execution/brokers,events}
mkdir -p shared/{models,utils,config}
mkdir -p api/state
touch features/__init__.py
```

Expected: Directories created successfully

---

### Task 1.2: 迁移 shared/models/

**Files:**
- Create: `quant/shared/models/__init__.py`
- Create: `quant/shared/models/order.py` (from `quant/models/order.py`)
- Create: `quant/shared/models/position.py` (from `quant/models/position.py`)
- Create: `quant/shared/models/trade.py` (from `quant/models/trade.py`)

- [ ] **Step 1: Copy order.py**

Copy `quant/models/order.py` to `quant/shared/models/order.py`

```python
# quant/shared/models/order.py
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class OrderStatus(Enum):
    """Order status enum."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL_FILLED = "partial_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class OrderType(Enum):
    """Order type enum."""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderSide(Enum):
    """Order side enum."""
    BUY = "buy"
    SELL = "sell"


@dataclass
class Order:
    """Order dataclass."""
    symbol: str
    quantity: float
    side: str
    order_type: str = "MARKET"
    price: Optional[float] = None
    order_id: Optional[str] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    timestamp: Optional[datetime] = None
    strategy_name: Optional[str] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()
```

- [ ] **Step 2: Copy position.py**

Copy `quant/models/position.py` to `quant/shared/models/position.py`

```python
# quant/shared/models/position.py
from dataclasses import dataclass
from typing import Optional


@dataclass
class Position:
    """Position dataclass."""
    symbol: str
    quantity: float
    avg_cost: float
    market_value: float
    unrealized_pnl: float
    realized_pnl: float = 0.0
    sector: Optional[str] = None
```

- [ ] **Step 3: Copy trade.py**

Copy `quant/models/trade.py` to `quant/shared/models/trade.py`

```python
# quant/shared/models/trade.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class Trade:
    """Trade dataclass."""
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
    fill_price: Optional[float] = None
    intended_qty: Optional[float] = None
    cost_breakdown: Dict[str, float] = field(default_factory=dict)
```

- [ ] **Step 4: Create models/__init__.py**

```python
# quant/shared/models/__init__.py
from quant.shared.models.order import Order, OrderStatus, OrderType, OrderSide
from quant.shared.models.position import Position
from quant.shared.models.trade import Trade

__all__ = ["Order", "OrderStatus", "OrderType", "OrderSide", "Position", "Trade"]
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest quant/tests/ -q --tb=short`
Expected: Tests pass (only testing that old code still works)

---

### Task 1.3: 迁移 shared/utils/

**Files:**
- Create: `quant/shared/utils/__init__.py`
- Create: `quant/shared/utils/logger.py` (from `quant/utils/logger.py`)
- Create: `quant/shared/utils/datetime_utils.py` (from `quant/utils/datetime_utils.py`)
- Create: `quant/shared/utils/config_loader.py` (from `quant/utils/config_loader.py`)

- [ ] **Step 1: Copy logger.py**

```python
# quant/shared/utils/logger.py
import logging
import sys
from pathlib import Path


def setup_logger(name: str, level: str = "INFO") -> logging.Logger:
    """Setup a logger with consistent formatting."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))
    
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    
    return logger


def get_logger(name: str) -> logging.Logger:
    """Get an existing logger or create a new one."""
    return logging.getLogger(name)
```

- [ ] **Step 2: Copy datetime_utils.py**

Copy existing content from `quant/utils/datetime_utils.py`

- [ ] **Step 3: Copy config_loader.py**

Copy existing content from `quant/utils/config_loader.py`

- [ ] **Step 4: Create utils/__init__.py**

```python
# quant/shared/utils/__init__.py
from quant.shared.utils.logger import setup_logger, get_logger
from quant.shared.utils.config_loader import ConfigLoader
from quant.shared.utils.datetime_utils import (
    get_current_time,
    is_market_open,
    get_next_market_open,
    get_market_close,
    normalize_date,
)

__all__ = [
    "setup_logger",
    "get_logger", 
    "ConfigLoader",
    "get_current_time",
    "is_market_open",
    "get_next_market_open",
    "get_market_close",
    "normalize_date",
]
```

---

### Task 1.4: 迁移 shared/config/

**Files:**
- Create: `quant/shared/config/` directory
- Copy existing YAML files

- [ ] **Step 1: Copy config files**

```bash
cp quant/config/*.yaml quant/shared/config/
```

- [ ] **Step 2: Create config/__init__.py**

```python
# quant/shared/config/__init__.py
from pathlib import Path

CONFIG_DIR = Path(__file__).parent
```

- [ ] **Step 3: Commit Phase 1**

```bash
git add quant/shared/ quant/features/ quant/infrastructure/ quant/api/state/
git commit -m "feat: Phase 1 - create skeleton directories and migrate shared layer"
```

---

## Phase 2: Infrastructure 层迁移

**目标:** 迁移 data/, execution/, events/ 到 infrastructure/

---

### Task 2.1: 迁移 events 模块

**Files:**
- Create: `quant/infrastructure/events/__init__.py`
- Create: `quant/infrastructure/events/event_bus.py`

- [ ] **Step 1: Create event_bus.py**

Copy from `quant/core/events.py`, update import:

```python
# quant/infrastructure/events/event_bus.py
"""Internal event bus for pub/sub communication between components."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Any, Optional
import threading

from quant.shared.utils.logger import setup_logger

_logger = setup_logger("EventBus")


class EventType(Enum):
    """Event types for the internal event bus."""
    BAR = "bar"
    QUOTE = "quote"
    TRADE = "trade"
    ORDER_SUBMIT = "order_submit"
    ORDER_FILL = "order_fill"
    ORDER_CANCEL = "order_cancel"
    ORDER_REJECT = "order_reject"
    POSITION_UPDATE = "position_update"
    RISK_CHECK = "risk_check"
    MARKET_OPEN = "market_open"
    MARKET_CLOSE = "market_close"
    STRATEGY_SIGNAL = "strategy_signal"
    SYSTEM_SHUTDOWN = "system_shutdown"
    RESEARCH_SEARCH_DONE = "research_search_done"
    RESEARCH_IDEA_SCORED = "research_idea_scored"
    RESEARCH_CODE_READY = "research_code_ready"
    RESEARCH_REPORT_DONE = "research_report_done"
    RESEARCH_ERROR = "research_error"


@dataclass
class Event:
    """Base event class."""
    event_type: EventType
    timestamp: datetime
    data: Any
    source: Optional[str] = None


class EventBus:
    """Publish/subscribe event bus for component communication."""

    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable]] = {}
        self._lock = threading.RLock()

    def subscribe(self, event_type: EventType, callback: Callable) -> None:
        """Subscribe to an event type."""
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: EventType, callback: Callable) -> None:
        """Unsubscribe from an event type."""
        with self._lock:
            if event_type in self._subscribers:
                self._subscribers[event_type].remove(callback)

    def publish(self, event: Event) -> None:
        """Publish an event to all subscribers."""
        with self._lock:
            callbacks = self._subscribers.get(event.event_type, []).copy()

        for callback in callbacks:
            try:
                callback(event)
            except Exception as e:
                _logger.error(f"Error in event callback: {e}")

    def publish_nowait(self, event_type: EventType, data: Any, source: Optional[str] = None) -> None:
        """Publish an event without waiting (fire and forget)."""
        event = Event(
            event_type=event_type,
            timestamp=datetime.now(),
            data=data,
            source=source,
        )
        self.publish(event)

    def clear(self) -> None:
        """Clear all subscribers."""
        with self._lock:
            self._subscribers.clear()
```

- [ ] **Step 2: Create events/__init__.py**

```python
# quant/infrastructure/events/__init__.py
from quant.infrastructure.events.event_bus import EventBus, EventType, Event

__all__ = ["EventBus", "EventType", "Event"]
```

---

### Task 2.2: 迁移 data 模块

**Files:**
- Create: `quant/infrastructure/data/__init__.py`
- Create: `quant/infrastructure/data/providers/base.py`
- Create: `quant/infrastructure/data/providers/__init__.py`
- Copy all provider files

- [ ] **Step 1: Copy all data files**

```bash
cp quant/data/providers/*.py quant/infrastructure/data/providers/
cp quant/data/storage*.py quant/infrastructure/data/
cp quant/data/normalizer.py quant/infrastructure/data/
cp quant/data/symbol_registry.py quant/infrastructure/data/
```

- [ ] **Step 2: Update imports in copied files**

Replace `from quant.utils.logger` with `from quant.shared.utils.logger`
Replace `from quant.models` with `from quant.shared.models`

Example for storage_duckdb.py:
```python
# Update these imports:
from quant.shared.utils.logger import setup_logger
# Keep other imports as they reference other data modules
```

- [ ] **Step 3: Create data/__init__.py**

```python
# quant/infrastructure/data/__init__.py
from quant.infrastructure.data.storage_duckdb import DuckDBStorage
from quant.infrastructure.data.storage import Storage
from quant.infrastructure.data.symbol_registry import SymbolRegistry
from quant.infrastructure.data.normalizer import DataNormalizer

__all__ = ["DuckDBStorage", "Storage", "SymbolRegistry", "DataNormalizer"]
```

- [ ] **Step 4: Create data/providers/__init__.py**

```python
# quant/infrastructure/data/providers/__init__.py
from quant.infrastructure.data.providers.base import DataProvider

__all__ = ["DataProvider"]
```

---

### Task 2.3: 迁移 execution 模块

**Files:**
- Create: `quant/infrastructure/execution/__init__.py`
- Create: `quant/infrastructure/execution/brokers/__init__.py`
- Create: `quant/infrastructure/execution/brokers/base.py` (modified)
- Copy all broker files
- Copy order_manager.py, fill_handler.py

- [ ] **Step 1: Copy broker files**

```bash
cp quant/execution/brokers/*.py quant/infrastructure/execution/brokers/
```

- [ ] **Step 2: Modify brokers/base.py**

Remove the re-export of models:

```python
# quant/infrastructure/execution/brokers/base.py
"""Base abstract class for broker adapters."""

from abc import ABC, abstractmethod
from typing import List

from quant.shared.models import Order, OrderStatus, Position, AccountInfo


__all__ = ["BrokerAdapter"]


class BrokerAdapter(ABC):
    """Abstract base class for broker adapters."""

    def __init__(self, name: str):
        self.name = name
        self._connected = False

    @abstractmethod
    def connect(self) -> None:
        pass

    @abstractmethod
    def disconnect(self) -> None:
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        pass

    @abstractmethod
    def submit_order(self, order: Order) -> str:
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        pass

    @abstractmethod
    def get_positions(self) -> List[Position]:
        pass

    @abstractmethod
    def get_account_info(self) -> AccountInfo:
        pass

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderStatus:
        pass

    def update_price(self, symbol: str, price: float) -> None:
        """Update price for paper trading (optional). Brokers that don't support this do nothing."""
        pass
```

- [ ] **Step 3: Copy and update paper.py**

Update imports in `quant/infrastructure/execution/brokers/paper.py`:

```python
from quant.shared.models import Order, OrderStatus, Position, AccountInfo
from quant.infrastructure.execution.brokers.base import BrokerAdapter
from quant.shared.utils.logger import setup_logger
```

`PaperBroker.update_price()` 已经存在，不需要添加。

- [ ] **Step 4: Copy order_manager.py**

Update imports:
```python
from quant.shared.models.order import Order, OrderStatus
from quant.infrastructure.execution.brokers.base import BrokerAdapter
from quant.shared.utils.logger import setup_logger
from quant.infrastructure.events import EventBus, EventType
```

- [ ] **Step 5: Copy fill_handler.py**

Update imports:
```python
from quant.infrastructure.events import EventBus, EventType
from quant.shared.utils.logger import setup_logger
```

- [ ] **Step 6: Create execution/__init__.py**

```python
# quant/infrastructure/execution/__init__.py
from quant.infrastructure.execution.brokers.base import BrokerAdapter
from quant.infrastructure.execution.order_manager import OrderManager
from quant.infrastructure.execution.fill_handler import FillHandler

__all__ = ["BrokerAdapter", "OrderManager", "FillHandler"]
```

- [ ] **Step 7: Create execution/brokers/__init__.py**

```python
# quant/infrastructure/execution/brokers/__init__.py
from quant.infrastructure.execution.brokers.base import BrokerAdapter
from quant.infrastructure.execution.brokers.paper import PaperBroker

__all__ = ["BrokerAdapter", "PaperBroker"]
```

- [ ] **Step 8: Commit Phase 2**

```bash
git add quant/infrastructure/
git commit -m "feat: Phase 2 - migrate infrastructure layer (data, execution, events)"
```

---

## Phase 3: Feature 层迁移（从 portfolio 开始）

**目标:** 迁移 portfolio feature（最简单，依赖最少）

---

### Task 3.1: 创建 portfolio feature

**Files:**
- Create: `quant/features/portfolio/__init__.py`
- Create: `quant/features/portfolio/api.py`
- Create: `quant/features/portfolio/tracker.py`
- Create: `quant/features/portfolio/coordinator.py`

- [ ] **Step 1: Copy tracker.py**

```python
# quant/features/portfolio/tracker.py
"""Strategy position tracker for per-strategy attribution."""

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional
from collections import defaultdict

from quant.shared.utils.logger import setup_logger

_logger = setup_logger("StrategyPositionTracker")

DEFAULT_STRATEGY = "default"


@dataclass
class PositionSnapshot:
    """Snapshot of strategy positions."""
    date: date
    strategy_name: str
    nav: float
    market_value: float
    cash: float
    unrealized_pnl: float
    realized_pnl: float


@dataclass
class StrategyPosition:
    """Position attributed to a strategy."""
    symbol: str
    quantity: float
    avg_cost: float
    market_value: float
    unrealized_pnl: float
    strategy_name: str


class StrategyPositionTracker:
    """Track positions per strategy."""

    def __init__(self):
        self._positions: Dict[str, StrategyPosition] = {}
        self._orders: Dict[str, str] = {}  # order_id -> strategy_name
        self._strategy_nav: Dict[str, float] = defaultdict(float)
        self._logger = _logger

    def record_order(self, order_id: str, strategy_name: str) -> None:
        """Record that an order belongs to a strategy."""
        self._orders[order_id] = strategy_name

    def get_strategy_for_order(self, order_id: str) -> str:
        """Get the strategy name for an order."""
        return self._orders.get(order_id, DEFAULT_STRATEGY)

    def update_position(
        self,
        symbol: str,
        quantity: float,
        avg_cost: float,
        market_value: float,
        unrealized_pnl: float,
        strategy_name: str = DEFAULT_STRATEGY,
    ) -> None:
        """Update a position for a strategy."""
        key = f"{strategy_name}:{symbol}"
        self._positions[key] = StrategyPosition(
            symbol=symbol,
            quantity=quantity,
            avg_cost=avg_cost,
            market_value=market_value,
            unrealized_pnl=unrealized_pnl,
            strategy_name=strategy_name,
        )

    def get_positions(self, strategy_name: Optional[str] = None) -> List[StrategyPosition]:
        """Get positions for a strategy (or all if None)."""
        if strategy_name:
            return [
                pos for key, pos in self._positions.items()
                if pos.strategy_name == strategy_name
            ]
        return list(self._positions.values())

    def get_breakdown(self) -> Dict:
        """Get position breakdown by strategy."""
        by_strategy = defaultdict(list)
        for pos in self._positions.values():
            by_strategy[pos.strategy_name].append({
                "symbol": pos.symbol,
                "quantity": pos.quantity,
                "avg_cost": pos.avg_cost,
                "market_value": pos.market_value,
                "unrealized_pnl": pos.unrealized_pnl,
            })
        return dict(by_strategy)

    def calibrate(self, holdings: List[Dict]) -> Dict:
        """Calibrate tracker with actual broker holdings."""
        # Simple calibration - in real implementation would reconcile
        result = {}
        for h in holdings:
            symbol = h.get("symbol", "")
            result[symbol] = {
                "quantity": h.get("qty", 0),
                "price": h.get("nominal_price", 0),
                "cost": h.get("cost_price", 0),
                "strategy": DEFAULT_STRATEGY,
            }
        return result

    def snapshot_all(self, total_nav: float) -> List[PositionSnapshot]:
        """Create snapshots for all strategies."""
        snapshots = []
        by_strategy = defaultdict(lambda: {"mv": 0, "cash": 0, "upnl": 0, "rpnl": 0})
        
        for pos in self._positions.values():
            s = by_strategy[pos.strategy_name]
            s["mv"] += pos.market_value
            s["upnl"] += pos.unrealized_pnl

        for strategy_name, values in by_strategy.items():
            snapshots.append(PositionSnapshot(
                date=date.today(),
                strategy_name=strategy_name,
                nav=total_nav,  # Simplified - would be per-strategy NAV
                market_value=values["mv"],
                cash=values["cash"],
                unrealized_pnl=values["upnl"],
                realized_pnl=values["rpnl"],
            ))
        
        return snapshots


# Global tracker instance
_tracker: Optional[StrategyPositionTracker] = None


def get_tracker() -> StrategyPositionTracker:
    """Get the global tracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = StrategyPositionTracker()
    return _tracker


def reset_tracker() -> None:
    """Reset the global tracker."""
    global _tracker
    _tracker = StrategyPositionTracker()
```

- [ ] **Step 2: Copy coordinator.py**

```python
# quant/features/portfolio/coordinator.py
"""Portfolio coordinator for multi-strategy position management."""

from typing import Dict, List, Any
from collections import defaultdict

from quant.shared.utils.logger import setup_logger

_logger = setup_logger("PortfolioCoordinator")


class PortfolioCoordinator:
    """Coordinate positions across multiple strategies."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._strategy_limits: Dict[str, Dict] = {}
        self._allocations: Dict[str, float] = defaultdict(float)

    def set_strategy_limit(self, strategy_name: str, max_position_pct: float) -> None:
        """Set position limit for a strategy."""
        self._strategy_limits[strategy_name] = {
            "max_position_pct": max_position_pct,
        }

    def check_allocation(self, strategy_name: str, symbol: str, quantity: float, price: float) -> bool:
        """Check if an allocation is allowed."""
        limits = self._strategy_limits.get(strategy_name, {})
        max_pct = limits.get("max_position_pct", 1.0)
        
        # Simplified check - real implementation would check against total NAV
        return True

    def get_combined_positions(self) -> List[Dict]:
        """Get combined positions across all strategies."""
        # Would aggregate positions from all strategies
        return []
```

- [ ] **Step 3: Create portfolio/__init__.py**

```python
# quant/features/portfolio/__init__.py
from quant.features.portfolio.tracker import (
    StrategyPositionTracker,
    get_tracker,
    reset_tracker,
    DEFAULT_STRATEGY,
)
from quant.features.portfolio.coordinator import PortfolioCoordinator

__all__ = [
    "StrategyPositionTracker",
    "get_tracker",
    "reset_tracker",
    "DEFAULT_STRATEGY",
    "PortfolioCoordinator",
]
```

- [ ] **Step 4: Create portfolio/api.py**

```python
# quant/features/portfolio/api.py
"""Portfolio feature API contracts."""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class CombinedPortfolio:
    """Combined portfolio across all strategies."""
    total_nav: float
    cash: float
    positions_value: float
    unrealized_pnl: float
    realized_pnl: float
    positions: List[Dict]


@dataclass
class RiskCheckResult:
    """Risk check result."""
    passed: bool
    reason: Optional[str] = None
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest quant/tests/ -q --tb=short`
Expected: Tests still pass

---

### Task 3.2: 创建 trading feature

**Files:**
- Create: `quant/features/trading/__init__.py`
- Create: `quant/features/trading/portfolio.py`
- Create: `quant/features/trading/risk.py`
- Create: `quant/features/trading/scheduler.py`
- Create: `quant/features/trading/engine.py`

- [ ] **Step 1: Copy portfolio.py**

```python
# quant/features/trading/portfolio.py
"""Portfolio tracker for positions, NAV, and P&L."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any
import threading

import pandas as pd

from quant.shared.models.position import Position


__all__ = ["Position", "PortfolioSnapshot", "Portfolio"]


@dataclass
class PortfolioSnapshot:
    """Snapshot of portfolio state at a point in time."""
    timestamp: datetime
    total_value: float
    cash: float
    positions_value: float
    unrealized_pnl: float
    realized_pnl: float
    margin_used: float


class Portfolio:
    """Tracks positions, NAV, and P&L in-memory per session."""

    def __init__(self, initial_cash: float = 100000.0, currency: str = "USD"):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.currency = currency
        self.positions: Dict[str, Position] = {}
        self.orders: List[Dict[str, Any]] = []
        self.snapshots: List[PortfolioSnapshot] = []
        self._lock = threading.RLock()
        self._starting_nav = initial_cash
        self._daily_pnl = 0.0
        self._session_start = datetime.now()

    @property
    def nav(self) -> float:
        """Net Asset Value."""
        return self.cash + sum(p.market_value for p in self.positions.values())

    @property
    def starting_nav(self) -> float:
        """Starting NAV for daily loss tracking."""
        return self._starting_nav

    @property
    def total_unrealized_pnl(self) -> float:
        """Total unrealized P&L."""
        return sum(p.unrealized_pnl for p in self.positions.values())

    @property
    def total_realized_pnl(self) -> float:
        """Total realized P&L."""
        return sum(p.realized_pnl for p in self.positions.values())

    @property
    def margin_used(self) -> float:
        """Total margin used."""
        return sum(
            p.market_value * 0.5 for p in self.positions.values()
        )

    def update_position(
        self,
        symbol: str,
        quantity: float,
        price: float,
        cost: float,
        sector: Optional[str] = None,
    ) -> None:
        """Update or create a position."""
        with self._lock:
            if symbol not in self.positions:
                self.positions[symbol] = Position(
                    symbol=symbol,
                    quantity=0,
                    avg_cost=0,
                    market_value=0,
                    unrealized_pnl=0,
                    realized_pnl=0,
                    sector=sector,
                )

            pos = self.positions[symbol]
            old_cost = pos.avg_cost * pos.quantity

            if quantity != 0:
                new_cost = cost + old_cost
                new_qty = quantity + pos.quantity
                pos.avg_cost = new_cost / new_qty if new_qty != 0 else 0
                pos.quantity = new_qty

            pos.market_value = pos.quantity * price
            pos.unrealized_pnl = pos.market_value - (pos.avg_cost * pos.quantity)

            if sector:
                pos.sector = sector

    def close_position(self, symbol: str, price: float) -> float:
        """Close a position and return realized P&L."""
        with self._lock:
            if symbol not in self.positions:
                return 0.0

            pos = self.positions[symbol]
            proceeds = pos.quantity * price
            cost_basis = pos.avg_cost * pos.quantity
            realized = proceeds - cost_basis

            self.cash += proceeds
            pos.realized_pnl += realized
            pos.quantity = 0
            pos.market_value = 0
            pos.unrealized_pnl = 0

            return realized

    def get_position(self, symbol: str) -> Optional[Position]:
        """Get a position by symbol."""
        return self.positions.get(symbol)

    def get_all_positions(self) -> List[Position]:
        """Get all current positions."""
        with self._lock:
            return [
                pos for pos in self.positions.values() if pos.quantity != 0
            ]

    def get_sector_exposure(self) -> Dict[str, float]:
        """Get exposure by sector as percentage of NAV."""
        sector_values: Dict[str, float] = {}
        for pos in self.positions.values():
            if pos.sector and pos.quantity != 0:
                sector_values[pos.sector] = (
                    sector_values.get(pos.sector, 0) + pos.market_value
                )

        nav = self.nav if self.nav != 0 else 1
        return {k: v / nav for k, v in sector_values.items()}

    def check_daily_loss(self, limit_pct: float) -> bool:
        """Check if daily loss exceeds limit."""
        current_nav = self.nav
        loss = self._starting_nav - current_nav
        loss_pct = loss / self._starting_nav if self._starting_nav != 0 else 0
        return loss_pct > limit_pct

    def record_snapshot(self) -> None:
        """Record a portfolio snapshot."""
        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            total_value=self.nav,
            cash=self.cash,
            positions_value=sum(p.market_value for p in self.positions.values()),
            unrealized_pnl=self.total_unrealized_pnl,
            realized_pnl=self.total_realized_pnl,
            margin_used=self.margin_used,
        )
        self.snapshots.append(snapshot)

    def reset_daily(self) -> None:
        """Reset for a new trading day."""
        self._starting_nav = self.nav
        self._daily_pnl = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert portfolio to dictionary."""
        return {
            "nav": self.nav,
            "cash": self.cash,
            "currency": self.currency,
            "initial_cash": self.initial_cash,
            "total_unrealized_pnl": self.total_unrealized_pnl,
            "total_realized_pnl": self.total_realized_pnl,
            "margin_used": self.margin_used,
            "positions": {
                symbol: {
                    "quantity": pos.quantity,
                    "avg_cost": pos.avg_cost,
                    "market_value": pos.market_value,
                    "unrealized_pnl": pos.unrealized_pnl,
                    "realized_pnl": pos.realized_pnl,
                    "sector": pos.sector,
                }
                for symbol, pos in self.positions.items()
                if pos.quantity != 0
            },
        }
```

- [ ] **Step 2: Copy risk.py**

```python
# quant/features/trading/risk.py
"""Risk engine for position and portfolio level risk checks."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from quant.shared.utils.logger import setup_logger
from quant.infrastructure.events import EventBus, EventType

_logger = setup_logger("RiskEngine")


@dataclass
class RiskCheckResult:
    """Risk check result."""
    passed: bool
    reason: Optional[str] = None


class RiskEngine:
    """Risk engine for pre-trade and portfolio risk checks."""

    def __init__(self, config: Dict[str, Any], portfolio: Any, event_bus: EventBus):
        self.config = config.get("risk", {})
        self.portfolio = portfolio
        self.event_bus = event_bus
        self._logger = _logger

        self.max_position_pct = self.config.get("max_position_pct", 0.20)
        self.max_sector_pct = self.config.get("max_sector_pct", 1.0)
        self.max_daily_loss_pct = self.config.get("max_daily_loss_pct", 0.10)
        self.max_leverage = self.config.get("max_leverage", 2.0)
        self.max_orders_minute = self.config.get("max_orders_minute", 100)

        self._orders_this_minute = 0
        self._last_minute = 0

    def check_order(self, symbol: str, quantity: float, price: float, side: str) -> RiskCheckResult:
        """Check if an order passes risk checks."""
        # Position limit check
        position_value = abs(quantity) * price
        nav = self.portfolio.nav if self.portfolio else 1
        
        if position_value / nav > self.max_position_pct:
            return RiskCheckResult(
                passed=False,
                reason=f"Position size {position_value/nav:.1%} exceeds limit {self.max_position_pct:.1%}"
            )

        # Daily loss check
        if self.portfolio and hasattr(self.portfolio, 'check_daily_loss'):
            if self.portfolio.check_daily_loss(self.max_daily_loss_pct):
                return RiskCheckResult(
                    passed=False,
                    reason=f"Daily loss limit {self.max_daily_loss_pct:.1%} exceeded"
                )

        return RiskCheckResult(passed=True)

    def check_portfolio(self) -> RiskCheckResult:
        """Check portfolio-level risk."""
        # Leverage check
        if self.portfolio:
            total_value = self.portfolio.nav
            margin_used = getattr(self.portfolio, 'margin_used', 0)
            if total_value > 0 and margin_used / total_value > self.max_leverage:
                return RiskCheckResult(
                    passed=False,
                    reason=f"Leverage {margin_used/total_value:.1f}x exceeds limit {self.max_leverage:.1f}x"
                )

        return RiskCheckResult(passed=True)
```

- [ ] **Step 3: Copy scheduler.py**

```python
# quant/features/trading/scheduler.py
"""Market hours scheduler."""

from datetime import datetime, time
from typing import Any, Callable, Dict, List, Optional
import threading

from quant.shared.utils.logger import setup_logger
from quant.shared.utils.datetime_utils import get_current_time, is_market_open
from quant.infrastructure.events import EventBus, EventType

_logger = setup_logger("Scheduler")


class Scheduler:
    """Schedule tasks around market hours."""

    def __init__(self, config: Dict[str, Any], event_bus: EventBus):
        self.config = config
        self.event_bus = event_bus
        self._logger = _logger
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._callbacks: List[Callable] = []

    def add_callback(self, callback: Callable) -> None:
        """Add a callback to run on schedule."""
        self._callbacks.append(callback)

    def start(self) -> None:
        """Start the scheduler."""
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._logger.info("Scheduler started")

    def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        self._logger.info("Scheduler stopped")

    def _run(self) -> None:
        """Main scheduler loop."""
        while self._running:
            try:
                for callback in self._callbacks:
                    try:
                        callback()
                    except Exception as e:
                        self._logger.error(f"Callback error: {e}")
            except Exception as e:
                self._logger.error(f"Scheduler error: {e}")
            
            import time
            time.sleep(60)  # Check every minute
```

- [ ] **Step 4: Copy engine.py (修复 PaperBroker 依赖)**

```python
# quant/features/trading/engine.py
"""Main event loop and orchestration engine."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Any
import threading
import time

from quant.infrastructure.events import EventBus, EventType
from quant.features.trading.scheduler import Scheduler
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.shared.utils.logger import setup_logger


class SystemMode(Enum):
    """System operation modes."""
    LIVE = "live"
    PAPER = "paper"
    BACKTEST = "backtest"


@dataclass
class Context:
    """Strategy context providing access to system components."""
    portfolio: Portfolio
    risk_engine: RiskEngine
    event_bus: EventBus
    order_manager: Any
    data_provider: Any
    broker: Any


class Engine:
    """Main event loop and orchestration engine."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.mode = SystemMode(config.get("system", {}).get("mode", "paper"))
        self.logger = setup_logger("Engine", config.get("system", {}).get("log_level", "INFO"))

        self.event_bus = EventBus()
        self.portfolio = Portfolio(
            initial_cash=config.get("system", {}).get("initial_cash", 100000),
            currency=config.get("system", {}).get("currency", "USD"),
        )
        self.risk_engine = RiskEngine(config, self.portfolio, self.event_bus)
        self.scheduler = Scheduler(config, self.event_bus)
        self.order_manager = None

        self.strategies: List[Any] = []
        self.data_providers: Dict[str, Any] = {}
        self.broker: Optional[Any] = None

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._subscribed_symbols: List[str] = []

    def set_data_provider(self, name: str, provider: Any) -> None:
        """Set a data provider."""
        self.data_providers[name] = provider

    def set_broker(self, broker: Any) -> None:
        """Set the broker adapter."""
        self.broker = broker

    def set_order_manager(self, order_manager: Any) -> None:
        """Set the order manager."""
        self.order_manager = order_manager

    def add_strategy(self, strategy: Any) -> None:
        """Add a strategy to the engine and wire it to the event bus."""
        strategy.context = Context(
            portfolio=self.portfolio,
            risk_engine=self.risk_engine,
            event_bus=self.event_bus,
            order_manager=self.order_manager,
            data_provider=self.data_providers.get("default"),
            broker=self.broker,
        )
        self.strategies.append(strategy)

        self.event_bus.subscribe(EventType.BAR, lambda event: self._dispatch_bar(strategy, event))
        self.event_bus.subscribe(EventType.MARKET_OPEN, lambda event: self._dispatch_market_open(strategy, event))
        self.event_bus.subscribe(EventType.MARKET_CLOSE, lambda event: self._dispatch_market_close(strategy, event))

    def _dispatch_bar(self, strategy: Any, event: Any) -> None:
        if hasattr(strategy, "on_data") and strategy.context:
            strategy.on_data(strategy.context, event.data)

    def _dispatch_market_open(self, strategy: Any, event: Any) -> None:
        if hasattr(strategy, "on_before_trading") and strategy.context:
            ts = event.data.get("timestamp", datetime.now()) if isinstance(event.data, dict) else datetime.now()
            trading_date = ts.date() if hasattr(ts, "date") else ts
            strategy.on_before_trading(strategy.context, trading_date)

    def _dispatch_market_close(self, strategy: Any, event: Any) -> None:
        if hasattr(strategy, "on_after_trading") and strategy.context:
            ts = event.data.get("timestamp", datetime.now()) if isinstance(event.data, dict) else datetime.now()
            trading_date = ts.date() if hasattr(ts, "date") else ts
            strategy.on_after_trading(strategy.context, trading_date)

    def subscribe(self, symbols: List[str]) -> None:
        """Subscribe to symbols for real-time data."""
        self._subscribed_symbols.extend(symbols)
        for name, provider in self.data_providers.items():
            if hasattr(provider, "subscribe"):
                provider.subscribe(symbols, lambda data, src=name: self._on_data(src, data))

    def _on_data(self, provider_name: str, data: Any) -> None:
        """Handle incoming data from providers — dispatches via EventBus."""
        if isinstance(data, dict):
            symbol = data.get("symbol")
            if symbol and self.broker and hasattr(self.broker, 'update_price'):
                # Polymorphic call - no isinstance check needed
                self.broker.update_price(symbol, data.get("close", 0))
        self.event_bus.publish_nowait(EventType.BAR, data, provider_name)

    def start(self) -> None:
        """Start the engine in a background thread."""
        if self._running:
            return

        self.logger.info(f"Starting engine in {self.mode.value} mode")
        self._running = True

        for strategy in self.strategies:
            if hasattr(strategy, "on_start"):
                strategy.on_start(strategy.context)

        self.scheduler.start()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the engine."""
        if not self._running:
            return

        self.logger.info("Stopping engine")
        self._running = False

        self.scheduler.stop()

        for strategy in self.strategies:
            if hasattr(strategy, "on_stop"):
                strategy.on_stop(strategy.context)

        for name, provider in self.data_providers.items():
            if hasattr(provider, "disconnect"):
                provider.disconnect()

        if self.broker and hasattr(self.broker, "disconnect"):
            self.broker.disconnect()

    def _run_loop(self) -> None:
        """Main engine loop for Mode A (Live) and Mode C (Paper)."""
        was_market_open = False
        while self._running:
            try:
                self._heartbeat()
                is_open = self._is_market_open()
                if is_open and not was_market_open:
                    self.event_bus.publish_nowait(
                        EventType.MARKET_OPEN,
                        {"timestamp": datetime.now()}
                    )
                elif not is_open and was_market_open:
                    self.event_bus.publish_nowait(
                        EventType.MARKET_CLOSE,
                        {"timestamp": datetime.now()}
                    )
                was_market_open = is_open
            except Exception as e:
                self.logger.error(f"Engine loop error: {e}")
            time.sleep(60)

    def _heartbeat(self) -> None:
        """Run periodic health checks."""
        if self.mode == SystemMode.LIVE:
            for name, provider in self.data_providers.items():
                if hasattr(provider, "is_connected") and not provider.is_connected():
                    self.logger.warning(f"Data provider {name} disconnected, attempting reconnect")
                    if hasattr(provider, "connect"):
                        provider.connect()

    def _is_market_open(self) -> bool:
        """Check if market is currently open."""
        from quant.shared.utils.datetime_utils import get_current_time, is_market_open

        market_config = self.config.get("markets", {}).get("US", {})
        now = get_current_time(market_config.get("timezone", "America/New_York"))
        return is_market_open(
            now,
            market_config.get("open_hour", 9),
            market_config.get("open_minute", 30),
            market_config.get("close_hour", 16),
            market_config.get("close_minute", 0),
        )

    def run_backtest(self, start_date: datetime, end_date: datetime, speed: str = "1x") -> None:
        """Run backtest mode with historical data replay."""
        self.logger.info(f"Running backtest from {start_date} to {end_date} at {speed}")
        self.mode = SystemMode.BACKTEST

        speed_multiplier = {"1x": 1, "10x": 10, "100x": 100, "end_of_day": float("inf")}.get(speed, 1)

        for strategy in self.strategies:
            if hasattr(strategy, "on_start"):
                strategy.on_start(strategy.context)

        current_date = start_date
        while current_date <= end_date:
            for name, provider in self.data_providers.items():
                if hasattr(provider, "get_bars"):
                    data = provider.get_bars(
                        self._subscribed_symbols,
                        current_date,
                        current_date,
                        self.config.get("data", {}).get("default_timeframe", "5m"),
                    )
                    for _, row in data.iterrows():
                        self.event_bus.publish_nowait(EventType.BAR, row, name)

            current_date = current_date + timedelta(days=1)

        for strategy in self.strategies:
            if hasattr(strategy, "on_stop"):
                strategy.on_stop(strategy.context)

    def get_portfolio_status(self) -> Dict[str, Any]:
        """Get current portfolio status."""
        return self.portfolio.to_dict()
```

- [ ] **Step 5: Create trading/__init__.py**

```python
# quant/features/trading/__init__.py
from quant.features.trading.engine import Engine, SystemMode, Context
from quant.features.trading.scheduler import Scheduler
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine, RiskCheckResult

__all__ = [
    "Engine",
    "SystemMode",
    "Context",
    "Scheduler",
    "Portfolio",
    "RiskEngine",
    "RiskCheckResult",
]
```

- [ ] **Step 6: Commit**

```bash
git add quant/features/trading/ quant/features/portfolio/
git commit -m "feat: Phase 3a - migrate trading and portfolio features"
```

---

### Task 3.3: 创建 backtest feature

**Files:**
- Create: `quant/features/backtest/__init__.py`
- Create: `quant/features/backtest/analytics.py`
- Create: `quant/features/backtest/walkforward.py`
- Create: `quant/features/backtest/engine.py`

- [ ] **Step 1: Copy analytics.py**

Copy `quant/core/analytics.py` to `quant/features/backtest/analytics.py`, update imports.

- [ ] **Step 2: Copy walkforward.py**

Copy `quant/core/walkforward.py` to `quant/features/backtest/walkforward.py`, update imports.

- [ ] **Step 3: Copy backtester.py as engine.py**

Copy `quant/core/backtester.py` to `quant/features/backtest/engine.py`, update imports:
- `from quant.core.events` -> `from quant.infrastructure.events`
- `from quant.core.portfolio` -> `from quant.features.trading.portfolio`
- `from quant.core.risk` -> `from quant.features.trading.risk`
- `from quant.core.analytics` -> `from quant.features.backtest.analytics`
- `from quant.models.trade` -> `from quant.shared.models.trade`

- [ ] **Step 4: Create backtest/__init__.py**

```python
# quant/features/backtest/__init__.py
from quant.features.backtest.engine import (
    Backtester,
    BacktestResult,
    BacktestDiagnostics,
    BacktestResultExporter,
)
from quant.features.backtest.walkforward import WalkForwardEngine, DataFrameProvider
from quant.features.backtest.analytics import (
    calculate_sharpe,
    calculate_sortino,
    calculate_max_drawdown,
    calculate_performance_metrics,
    PerformanceMetrics,
)

__all__ = [
    "Backtester",
    "BacktestResult",
    "BacktestDiagnostics",
    "BacktestResultExporter",
    "WalkForwardEngine",
    "DataFrameProvider",
    "calculate_sharpe",
    "calculate_sortino",
    "calculate_max_drawdown",
    "calculate_performance_metrics",
    "PerformanceMetrics",
]
```

- [ ] **Step 5: Commit**

```bash
git add quant/features/backtest/
git commit -m "feat: Phase 3b - migrate backtest feature"
```

---

### Task 3.4: 创建 strategies feature

**Files:**
- Create: `quant/features/strategies/__init__.py`
- Create: `quant/features/strategies/base.py`
- Create: `quant/features/strategies/registry.py`
- Create: `quant/features/strategies/factors.py`
- Copy strategy implementations

- [ ] **Step 1: Copy base.py**

Update imports in `quant/features/strategies/base.py`:
- `from quant.utils.logger` -> `from quant.shared.utils.logger`
- `from quant.core.engine` TYPE_CHECKING import path stays relative

- [ ] **Step 2: Copy registry.py**

Update imports in `quant/features/strategies/registry.py`:
- `from quant.strategies.base` -> `from quant.features.strategies.base`

- [ ] **Step 3: Copy factors.py**

Update imports if any.

- [ ] **Step 4: Copy strategy implementations**

```bash
cp -r quant/strategies/simple_momentum quant/features/strategies/
cp -r quant/strategies/volatility_regime quant/features/strategies/
cp -r quant/strategies/cross_sectional_mr quant/features/strategies/
```

Update imports in each strategy's `strategy.py`:
- `from quant.strategies.base` -> `from quant.features.strategies.base`
- `from quant.strategies.registry` -> `from quant.features.strategies.registry`

- [ ] **Step 5: Create strategies/__init__.py**

```python
# quant/features/strategies/__init__.py
from quant.features.strategies.base import Strategy
from quant.features.strategies.registry import StrategyRegistry, strategy
from quant.features.strategies.factors import Factors

__all__ = ["Strategy", "StrategyRegistry", "strategy", "Factors"]
```

- [ ] **Step 6: Commit**

```bash
git add quant/features/strategies/
git commit -m "feat: Phase 3c - migrate strategies feature"
```

---

### Task 3.5: 创建 cio feature

**Files:**
- Copy entire `quant/cio/` to `quant/features/cio/`

- [ ] **Step 1: Copy cio module**

```bash
cp -r quant/cio/* quant/features/cio/
```

- [ ] **Step 2: Update imports in cio files**

Update imports in all cio files:
- `from quant.utils.logger` -> `from quant.shared.utils.logger`

- [ ] **Step 3: Create cio/__init__.py**

```python
# quant/features/cio/__init__.py
from quant.features.cio.cio_engine import CIOEngine
from quant.features.cio.market_assessor import MarketAssessor
from quant.features.cio.news_analyzer import NewsAnalyzer
from quant.features.cio.weight_allocator import WeightAllocator

__all__ = [
    "CIOEngine",
    "MarketAssessor",
    "NewsAnalyzer",
    "WeightAllocator",
]
```

- [ ] **Step 4: Commit Phase 3**

```bash
git add quant/features/cio/
git commit -m "feat: Phase 3d - migrate cio feature"
```

---

## Phase 4: API 层重构

**目标:** 将 api/state.py 拆解，创建薄路由层

---

### Task 4.1: 创建 api/state/runtime.py

**Files:**
- Create: `quant/api/state/runtime.py`

- [ ] **Step 1: Create runtime state file**

```python
# quant/api/state/runtime.py
"""Minimal runtime state for API layer."""

import threading
from typing import Any, Dict, Optional

# System runtime state
system_process = None
system_thread = None
system_status = 'stopped'
simulation_running = False

# Portfolio runtime data
portfolio_data: Dict[str, float] = {
    'nav': 100000.0,
    'total_unrealized_pnl': 0.0,
    'total_realized_pnl': 0.0
}
strategies_data: list = []
positions_data: list = []
orders_data: list = []
selected_strategy = 'VolatilityRegime'

# Backtest results
_backtest_results: Dict[str, Any] = {}
_backtest_lock = threading.Lock()

# Futu broker
_futu_lock = threading.Lock()
_futu_broker: Optional[Any] = None

# Mock data (for simulation mode)
MOCK_SYMBOLS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'SPY', 'QQQ']
MOCK_PRICES = {
    'AAPL': 178.50, 'MSFT': 378.25, 'GOOGL': 141.80,
    'AMZN': 185.60, 'TSLA': 245.20, 'SPY': 512.30, 'QQQ': 438.50, 'VIX': 14.5
}


def _get_futu_broker():
    """Get Futu broker instance."""
    global _futu_broker
    return _futu_broker
```

---

### Task 4.2: 更新 API blueprints

**Files:**
- Modify: `quant/api/system_bp.py`
- Modify: `quant/api/strategies_bp.py`
- Modify: `quant/api/backtest_bp.py`
- Modify: `quant/api/cio_bp.py`
- Modify: `quant/api/futu_bp.py`
- Modify: `quant/api/positions_bp.py`

- [ ] **Step 1: Update system_bp.py**

```python
# quant/api/system_bp.py
import time
import threading
from flask import Blueprint, jsonify

from quant.api.state.runtime import (
    system_status, portfolio_data, strategies_data, positions_data,
    selected_strategy, system_process, system_thread, simulation_running,
)

system_bp = Blueprint('system', __name__)


@system_bp.route('/api/status', methods=['GET'])
def get_status():
    return jsonify({
        'status': system_status,
        'portfolio': portfolio_data,
        'strategies': strategies_data,
        'positions': positions_data,
        'selected_strategy': selected_strategy
    })


# ... rest of the file unchanged
```

- [ ] **Step 2: Update strategies_bp.py**

Replace imports from `quant.api.state` to use feature services. This requires creating the strategies service first.

- [ ] **Step 3: Update backtest_bp.py**

Replace imports to use new paths:
- `from quant.core.backtester` -> `from quant.features.backtest`
- `from quant.strategies.registry` -> `from quant.features.strategies`

- [ ] **Step 4: Update cio_bp.py**

Replace imports to use new paths.

- [ ] **Step 5: Update futu_bp.py**

Replace imports:
- `from quant.execution.strategy_position_tracker` -> `from quant.features.portfolio`

- [ ] **Step 6: Update positions_bp.py**

Replace imports to use new paths.

---

### Task 4.3: 删除旧 api/state.py

**Files:**
- Delete: `quant/api/state.py` (after verifying all imports updated)

- [ ] **Step 1: Remove old state file**

```bash
rm quant/api/state.py
git add -A
git commit -m "feat: Phase 4 - refactor API layer, remove api/state.py god module"
```

---

## Phase 5: 入口文件适配

**目标:** 更新 api_server.py, quant_system.py, backtest_runner.py 使用新路径

---

### Task 5.1: 更新 api_server.py

**Files:**
- Modify: `quant/api_server.py`

- [ ] **Step 1: Update imports**

```python
# Update imports to new paths
from quant.api.state.runtime import _load_strategy_state  # If this function is needed
# ... keep other imports
```

---

### Task 5.2: 更新 quant_system.py

**Files:**
- Modify: `quant/quant_system.py`

- [ ] **Step 1: Update all imports**

Replace all imports:
- `from quant.core.engine` -> `from quant.features.trading`
- `from quant.core.events` -> `from quant.infrastructure.events`
- `from quant.data.providers` -> `from quant.infrastructure.data.providers`
- `from quant.execution` -> `from quant.infrastructure.execution`
- `from quant.strategies` -> `from quant.features.strategies`

---

### Task 5.3: 更新 backtest_runner.py

**Files:**
- Modify: `quant/backtest_runner.py`

- [ ] **Step 1: Update all imports**

Replace all imports to use new paths.

- [ ] **Step 2: Commit Phase 5**

```bash
git add quant/api_server.py quant/quant_system.py quant/backtest_runner.py
git commit -m "feat: Phase 5 - update entry points to use new module paths"
```

---

## Phase 6: AGENTS.md 和测试

**目标:** 创建所有 AGENTS.md 文件，确保测试通过

---

### Task 6.1: 创建根目录 AGENTS.md

**Files:**
- Create: `/Users/bytedance/Desktop/vk/quant/.vibe-kanban-workspaces/a9c9-/quant/AGENTS.md`

- [ ] **Step 1: Create global AGENTS.md**

```markdown
# AGENTS.md — Quant Trading System

## Architecture

垂直切片架构。所有业务功能按 Feature 组织，基础设施水平分层。

## Directory Structure

```
quant/
├── features/           # 垂直切片业务功能
│   ├── backtest/      # 回测完整闭环
│   ├── trading/       # 实盘/模拟交易
│   ├── portfolio/     # 仓位管理
│   ├── cio/           # CIO 市场评估
│   └── strategies/    # 策略框架 + 实现
├── infrastructure/    # 基础设施（无业务语义）
│   ├── data/          # 数据存储 + Provider
│   ├── execution/     # 券商适配 + 订单执行
│   └── events/        # 事件总线
├── shared/            # 跨 feature 纯共享
│   ├── models/        # 领域模型
│   ├── utils/         # 工具
│   └── config/        # 配置
└── api/               # Flask 薄路由层
```

## Dependency Rules

- **feature 之间禁止互 import**，只通过 service.py 对外
- **api/** 只调 features/*/service.py，不碰 infrastructure
- **infrastructure/** 不依赖任何 feature
- **shared/** 无业务语义，纯工具

## Feature Index

| Feature | Path | Description |
|---------|------|-------------|
| backtest | features/backtest/ | 回测完整闭环，见 features/backtest/AGENTS.md |
| trading | features/trading/ | 实盘/模拟交易，见 features/trading/AGENTS.md |
| portfolio | features/portfolio/ | 仓位管理，见 features/portfolio/AGENTS.md |
| cio | features/cio/ | CIO 市场评估，见 features/cio/AGENTS.md |
| strategies | features/strategies/ | 策略框架 + 实现，见 features/strategies/AGENTS.md |

## Python Package

- 包名: `quant`
- Import 格式: `from quant.features.backtest.engine import Backtester`
- 禁止相对导入，必须使用完整路径

## Commands

```bash
# 启动 API 服务
python quant/api_server.py

# 运行测试
python -m pytest quant/tests/ -q

# CLI 回测
python quant/backtest_runner.py --strategy SimpleMomentum --start 2024-01-01 --end 2024-12-31 --symbols HK.00700,HK.09988

# CLI 实盘/模拟
python quant/quant_system.py --mode paper
```

## Migration Notes

重构后 import 路径变更：

```python
# 旧 (已废弃)
from quant.core.backtester import Backtester
from quant.models import Order

# 新
from quant.features.backtest.engine import Backtester
from quant.shared.models import Order
```
```

---

### Task 6.2: 创建各 Feature 的 AGENTS.md

**Files:**
- Create: `quant/features/backtest/AGENTS.md`
- Create: `quant/features/trading/AGENTS.md`
- Create: `quant/features/portfolio/AGENTS.md`
- Create: `quant/features/cio/AGENTS.md`
- Create: `quant/features/strategies/AGENTS.md`

- [ ] **Step 1: Create backtest/AGENTS.md**

```markdown
# Backtest Feature

## 职责

回测从数据加载到结果输出的完整闭环。包含回测引擎、步进验证、绩效分析。

## 对外契约

主要类：
- `Backtester(config, event_bus=None, lot_sizes=None)` - 回测引擎
- `BacktestResult` - 回测结果数据类
- `BacktestResultExporter` - 结果导出

## 依赖

- `infrastructure/events` - 通过 EventBus 发布事件
- `features/trading` - 使用 Portfolio, RiskEngine
- `shared/models` - 使用 Trade, Position
- `shared/utils` - 使用 logger

## 不变量

- 回测使用 T+1 成交（信号日次日开盘价成交）
- 港股有手数限制，美股无
- 成交量限制：单订单不超过当日成交量的 5%

## 修改守则

- 改回测逻辑：只动 `engine.py`
- 改绩效计算：只动 `analytics.py`
- 改步进验证：只动 `walkforward.py`
- 改数据结构：同步更新所有引用的地方

## Known Pitfalls

- `Backtester._create_context()` 使用了内部类 `BacktestOrderManager`，不要暴露到外部
- `lot_sizes` 字典的 key 格式要与 symbol 一致（包含 HK./US. 前缀）
```

- [ ] **Step 2: Create trading/AGENTS.md**

```markdown
# Trading Feature

## 职责

实盘/模拟交易的全生命周期管理。包含交易引擎、风控、调度器、组合管理。

## 对外契约

主要类：
- `Engine(config)` - 交易引擎主类
- `SystemMode(Enum)` - LIVE/PAPER/BACKTEST
- `Context` - 策略上下文
- `Portfolio` - 组合跟踪
- `RiskEngine` - 风控引擎
- `Scheduler` - 市场时间调度

## 依赖

- `infrastructure/events` - EventBus
- `infrastructure/execution` - OrderManager, FillHandler
- `shared/models` - Order, Position
- `shared/utils` - logger, datetime_utils

## 不变量

- Engine 在单独线程运行，通过 EventBus 与策略通信
- Portfolio 是线程安全的（使用 RLock）
- RiskEngine 在订单提交前进行风控检查

## 修改守则

- 改引擎逻辑：只动 `engine.py`
- 改风控规则：只动 `risk.py`
- 改组合计算：只动 `portfolio.py`
- 改调度逻辑：只动 `scheduler.py`

## Known Pitfalls

- `Engine._on_data()` 中使用 `hasattr(self.broker, 'update_price')` 替代了原来的 `isinstance` 检查
- 策略通过 Context 访问所有系统组件，不要直接引用 Engine
```

- [ ] **Step 3: Create portfolio/AGENTS.md**

```markdown
# Portfolio Feature

## 职责

多策略仓位归属、协调、风控检查。包含策略仓位跟踪器和组合协调器。

## 对外契约

主要类：
- `StrategyPositionTracker` - 策略级别仓位跟踪
- `get_tracker()` - 获取全局跟踪器实例
- `PortfolioCoordinator` - 多策略组合协调

## 依赖

- `shared/utils` - logger
- `infrastructure/execution` - 通过 order_id 关联策略

## 不变量

- 一个订单只能属于一个策略
- 仓位快照每日记录一次
- DEFAULT_STRATEGY = "default" 用于未归属订单

## 修改守则

- 改仓位跟踪：只动 `tracker.py`
- 改组合协调：只动 `coordinator.py`

## Known Pitfalls

- `calibrate()` 方法目前只返回简单映射，需要与实盘持仓对账
- 策略名称作为 key 的一部分，改名会导致历史数据丢失
```

- [ ] **Step 4: Create cio/AGENTS.md**

```markdown
# CIO Feature

## 职责

CIO 市场评估和策略权重分配。包含市场评估器、新闻分析器、权重分配器。

## 对外契约

主要类：
- `CIOEngine` - CIO 引擎主类
- `MarketAssessor` - 市场评估
- `NewsAnalyzer` - 新闻分析
- `WeightAllocator` - 权重分配

## 依赖

- `shared/utils` - logger
- LLM 适配器（OpenAI/Claude/Ollama）

## 不变量

- VIX < 15: 牛市，> 25: 熊市
- 权重总和必须等于 1.0
- LLM 调用失败时优雅降级到中性默认

## 修改守则

- 改市场评估逻辑：只动 `market_assessor.py`
- 改新闻分析：只动 `news_analyzer.py`
- 改权重算法：只动 `weight_allocator.py`

## Known Pitfalls

- LLM API 调用可能超时，需要设置超时时间
- 新闻文本过长需要截断
```

- [ ] **Step 5: Create strategies/AGENTS.md**

```markdown
# Strategies Feature

## 职责

策略定义、注册、发现、框架组件。包含策略基类、注册表、因子库。

## 对外契约

主要类/装饰器：
- `Strategy(ABC)` - 策略抽象基类
- `StrategyRegistry` - 策略注册表
- `@strategy` - 策略装饰器
- `Factors` - 因子库

## 依赖

- `shared/utils` - logger
- `infrastructure/events` - EventBus (通过 Context)

## 不变量

- 所有策略必须使用 `@strategy` 装饰器注册
- 策略 ID 唯一，不区分大小写
- 策略目录结构: `strategies/<name>/strategy.py`

## 修改守则

- 改策略框架：只动 `base.py`, `registry.py`
- 改因子库：只动 `factors.py`
- 新增策略：在 `strategies/` 下新建目录

## Known Pitfalls

- 策略在 `__init__` 中不要访问 Context，Context 在 `on_start` 时才设置
- 策略名称要与装饰器参数一致
```

---

### Task 6.3: 更新测试导入路径

**Files:**
- Modify: All test files in `quant/tests/`

- [ ] **Step 1: Update test imports**

批量替换测试文件中的导入路径：
- `from quant.core` -> `from quant.features`
- `from quant.models` -> `from quant.shared.models`
- `from quant.utils` -> `from quant.shared.utils`
- `from quant.execution` -> `from quant.infrastructure.execution`
- `from quant.strategies` -> `from quant.features.strategies`
- `from quant.cio` -> `from quant.features.cio`

- [ ] **Step 2: Run all tests**

```bash
python -m pytest quant/tests/ -v --tb=short
```

Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add quant/tests/ AGENTS.md quant/features/*/AGENTS.md
git commit -m "feat: Phase 6 - add AGENTS.md docs and update test imports"
```

---

## 完成验证

- [ ] **Step 1: Verify directory structure**

Run:
```bash
tree -L 3 quant/
```
Expected: See new directory structure

- [ ] **Step 2: Verify imports work**

Run:
```python
python -c "
from quant.features.backtest import Backtester
from quant.features.trading import Engine
from quant.features.portfolio import get_tracker
from quant.features.strategies import Strategy, StrategyRegistry
from quant.features.cio import CIOEngine
from quant.infrastructure.events import EventBus
from quant.infrastructure.data import DuckDBStorage
from quant.infrastructure.execution import OrderManager
from quant.shared.models import Order, Position, Trade
print('All imports successful!')
"
```

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest quant/tests/ -q
```

- [ ] **Step 4: Final commit**

```bash
git log --oneline -10
```

---

## Summary

This plan implements a complete Feature-Slice refactoring:

1. **Phase 1**: Create skeleton directories, migrate shared layer
2. **Phase 2**: Migrate infrastructure (data, execution, events)
3. **Phase 3**: Migrate features (portfolio, trading, backtest, strategies, cio)
4. **Phase 4**: Refactor API layer, remove api/state.py god module
5. **Phase 5**: Update entry points (api_server.py, quant_system.py, backtest_runner.py)
6. **Phase 6**: Add AGENTS.md documentation, update tests

Each phase is self-contained and testable. API paths remain unchanged for frontend compatibility.
