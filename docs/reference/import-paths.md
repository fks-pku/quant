# Import Path Reference

## Domain Layer (preferred)

```python
from quant.domain.models.order import Order, OrderSide, OrderType, OrderStatus
from quant.domain.models.position import Position
from quant.domain.models.trade import Trade
from quant.domain.models.fill import Fill
from quant.domain.models.bar import Bar
from quant.domain.models.account import AccountInfo
from quant.domain.models.risk_check import RiskCheckResult
from quant.domain.events.base import Event, EventType
from quant.domain.ports.data_feed import DataFeed
from quant.domain.ports.broker import BrokerAdapter
from quant.domain.ports.strategy import Strategy, StrategyContext
from quant.domain.ports.storage import Storage
from quant.domain.ports.event_publisher import EventPublisher
```

## Infrastructure (implements domain ports)

```python
from quant.infrastructure.events import EventBus
from quant.infrastructure.execution.brokers.paper import PaperBroker
from quant.infrastructure.data.storage_duckdb import DuckDBStorage
from quant.infrastructure.data.providers.tushare import TushareProvider
from quant.infrastructure.data.providers.akshare import AkshareProvider
```

## Features (orchestrators)

```python
from quant.features.backtest.engine import Backtester
from quant.features.trading.engine import Engine, SystemMode, Context
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.strategies import Strategy, StrategyRegistry
from quant.features.cio import CIOEngine
from quant.features.research import ResearchEngine, CandidatePool, ResearchScheduler
```

## Backward Compatibility (re-exports from domain)

```python
from quant.shared.models import Order, Position, Trade, Fill, Bar, AccountInfo
from quant.shared.utils import setup_logger, ConfigLoader
```

## Python Package

- Package name: `quant`
- Import format: `from quant.domain.models.order import Order`
- No relative imports — always use full `quant.*` path
