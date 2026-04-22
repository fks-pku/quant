# AGENTS.md — Quant Trading System

## Architecture

Hexagonal (Ports & Adapters) + Event-Driven 架构。Domain 居中，通过 Ports 定义接口，Infrastructure 实现适配器，Features 编排用例。

**核心原则: Provider (DataFeed) 与 Storage 分离**
- `DataFeed` port — 从外部 API 获取数据 (Tushare, Akshare, YFinance)
- `Storage` port — 持久化和查询缓存数据 (DuckDBStorage)
- Providers 获取数据后写入 Storage，Backtest 从 Storage 读取
- DuckDB 不是 Provider，是 Storage 的实现

## Directory Structure

```
quant/
├── domain/              # 纯业务逻辑，零外部依赖 (CENTER)
│   ├── models/          # 领域模型 (Order, Position, Trade, Fill, Bar, AccountInfo)
│   ├── events/          # 领域事件 (EventType, Event, OrderEvents, MarketEvents...)
│   └── ports/           # 抽象接口 (DataFeed, BrokerAdapter, Strategy, Storage, EventPublisher)
├── infrastructure/      # 实现 domain ports (ADAPTERS)
│   ├── events/          # EventBus (implements EventPublisher)
│   ├── data/
│   │   ├── storage_duckdb.py  # DuckDBStorage (implements Storage port)
│   │   └── providers/        # External data fetchers (implement DataFeed port)
│   ├── execution/       # 券商适配 + 订单执行
│   └── var/             # 运行时数据 (DuckDB, gitignore)
├── features/            # 业务用例编排 (APPLICATION LAYER)
│   ├── backtest/        # 回测完整闭环
│   ├── trading/         # 实盘/模拟交易
│   ├── portfolio/       # 仓位管理
│   ├── cio/             # CIO 市场评估
│   ├── strategies/      # 策略框架 + 实现
│   └── research/        # 量化策略研究 (Quant Researcher)
├── shared/              # 跨 feature 纯共享工具
│   ├── models/          # 兼容层 → re-export from domain
│   ├── utils/           # 工具 (logger, config_loader, datetime_utils)
│   └── config/          # 配置 (config.yaml, brokers.yaml, strategies.yaml)
├── api/                 # Flask 薄路由层
├── frontend/            # React Dashboard UI
├── scripts/             # CLI 工具脚本
├── docs/                # 设计文档
└── tests/               # 测试
```

## Dependency Rules (铁律)

1. **domain/** 零外部依赖 — 不依赖任何其他层
2. **features/** 只依赖 domain (通过 ports 注入，不直接访问 infrastructure)
3. **infrastructure/** 实现 domain ports (依赖 domain)
4. **shared/** 无业务语义，纯工具 (models/ 仅做兼容 re-export)
5. **api/** 只调 features
6. **feature 之间禁止互 import**
7. 层间通信: 直接调用 + Event Bus (pub/sub) + 依赖注入 (DI)

## Domain Layer

### Models (`quant.domain.models.*`)

| Model | Type | Description |
|-------|------|-------------|
| Order | frozen dataclass | 交易订单，immutable value object |
| Position | mutable dataclass | 持仓跟踪 (quantity, P&L) |
| Trade | frozen dataclass | 已完成交易 round-trip |
| Fill | frozen dataclass | 订单成交 |
| Bar | frozen dataclass | OHLCV 市场数据 |
| AccountInfo | frozen dataclass | 账户信息 |

### Events (`quant.domain.events.*`)

| Event | Description |
|-------|-------------|
| Event (base) | 基础事件类 (EventType, event_id, timestamp) |
| OrderSubmittedEvent | 订单提交 |
| OrderFilledEvent | 订单成交 |
| BarEvent | 市场数据更新 |
| MarketOpenEvent / MarketCloseEvent | 市场开关 |
| StrategySignalEvent | 策略信号 |
| SystemStartEvent / SystemStopEvent | 系统生命周期 |

### Ports (`quant.domain.ports.*`)

| Port | Description | Implementations |
|------|-------------|----------------|
| DataFeed | 数据源接口 (get_bars, subscribe) | TushareProvider, AkshareProvider, YfinanceProvider |
| BrokerAdapter | 券商接口 (submit_order, get_positions) | PaperBroker, FutuProvider |
| Strategy | 策略接口 (on_bar, buy, sell) | VolatilityRegime, SimpleMomentum, CrossSectionalMeanReversion |
| Storage | 持久化接口 (save_bars, get_bars, get_symbols, get_lot_size) | DuckDBStorage |
| EventPublisher | 事件发布接口 (subscribe, publish) | EventBus |

## Data Architecture

### Two-Port Separation

```
DataFeed port (fetch from external)     Storage port (persist & query)
┌─────────────────────┐                ┌─────────────────────┐
│ TushareProvider     │──write──────→  │                     │
│ AkshareProvider     │──write──────→  │   DuckDBStorage     │
│ YfinanceProvider    │──write──────→  │   (implements       │
└─────────────────────┘                │    Storage port)     │
                                       │                     │
┌─────────────────────┐                │                     │
│ Backtester          │←──read────────│                     │
│ API endpoints       │←──read────────│                     │
└─────────────────────┘                └─────────────────────┘
```

- **Providers** fetch from external APIs, cache via Storage
- **Backtest** reads from Storage only — doesn't care where data came from
- **API layer** reads from Storage (read-only mode)

### Providers

| Provider | File | Markets | Storage |
|----------|------|---------|---------|
| TushareProvider | `infrastructure/data/providers/tushare.py` | CN | DuckDB (via Storage port) |
| AkshareProvider | `infrastructure/data/providers/akshare.py` | CN | — |
| YfinanceProvider | `infrastructure/data/providers/yfinance_provider.py` | US | Parquet |

### Storage

| Storage | File | Description |
|---------|------|-------------|
| DuckDBStorage | `infrastructure/data/storage_duckdb.py` | Implements Storage port. Supports `read_only=True` for readers. |

### DuckDB Connection Rules

- **Writers** (ingest scripts, providers): `DuckDBStorage()` — default read-write
- **Readers** (API, backtest, providers reading cache): `DuckDBStorage(read_only=True)`
- Only ingest scripts and providers with fresh data should write
- **Never** open write connections from API endpoints — prevents data corruption

### Symbol Registry Markets

| Market | Code Pattern | Example | DuckDB Table |
|--------|--------------|---------|--------------|
| US | Letters | `AAPL`, `SPY` | `daily_us` / `minute_us` |
| HK | 5-digit numeric | `00700` | `daily_hk` / `minute_hk` |
| CN | 6-digit numeric (0/3/6/8/9 prefix) | `600519` | `daily_cn` / `minute_cn` |

### Tushare Configuration

```yaml
data:
  tushare:
    token: "YOUR_TUSHARE_TOKEN"
    api_url: "http://..."   # optional custom server URL
```

### CN Market Notes

- Lot size: 100 shares (backtester enforces lot rounding)
- CN stocks (e.g. 600519 茅台 ~¥1700/share) require higher `initial_cash` (500K+)
- Default 100K is insufficient for high-price CN stocks

### Backtest Commission Models

| Market | Commission | Stamp Duty | Other Fees |
|--------|-----------|------------|------------|
| US | per-share $0.005 min $1 | — | — |
| HK | 0.03% min HK$3 | 0.13% on SELL | SFC levy + clearing + trading fee |
| CN | 0.025% min ¥5 | 0.05% on SELL | Transfer fee 0.001% |

## Feature Index

| Feature | Path | Description |
|---------|------|-------------|
| backtest | features/backtest/ | 回测引擎、步进验证、绩效分析 |
| trading | features/trading/ | 交易引擎、风控、调度器、组合管理 |
| portfolio | features/portfolio/ | 策略仓位跟踪、多策略协调 |
| cio | features/cio/ | CIO 市场评估、新闻分析、权重分配 |
| strategies | features/strategies/ | 策略基类、注册表、因子库、策略实现 |
| research | features/research/ | 策略发现、评估、回测、候选池管理 |

### Strategies (implemented)

| Strategy | Directory | CN Compatible | Notes |
|----------|-----------|---------------|-------|
| SimpleMomentum | `strategies/simple_momentum/` | Yes | Cross-sectional momentum; single-stock mode auto-detects |
| CrossSectionalMeanReversion | `strategies/cross_sectional_mr/` | Partially | Needs market_symbol=000300 |
| VolatilityRegime | `strategies/volatility_regime/` | No | Requires VIX data |

### Frontend Strategy List

Frontend only shows strategies with implementations in `features/strategies/*/strategy.py`. Registry in `api/state/runtime.py`.

## Python Package

- 包名: `quant`
- Import 格式: `from quant.domain.models.order import Order`
- 禁止相对导入，必须使用完整 `quant.*` 路径

## Commands

```bash
python quant/api_server.py                                        # 启动 API 服务
python -m pytest quant/tests/ -q                                  # 运行测试
python quant/backtest_runner.py --strategy SimpleMomentum ...     # CLI 回测
python quant/quant_system.py --mode paper                         # CLI 实盘/模拟
python quant/scripts/ingest_akshare.py --symbol 600519 ...        # 从 akshare 抓取 A-share 数据存入 DuckDB
python quant/scripts/ingest_tushare.py --symbol 600519 --start 2023-01-01 --end 2025-01-01  # 从 tushare 抓取
```

## Import Path Reference

```python
# Domain Layer (首选)
from quant.domain.models.order import Order, OrderSide, OrderType, OrderStatus
from quant.domain.models.position import Position
from quant.domain.models.trade import Trade
from quant.domain.models.fill import Fill
from quant.domain.models.bar import Bar
from quant.domain.models.account import AccountInfo
from quant.domain.events.base import Event, EventType
from quant.domain.ports.data_feed import DataFeed
from quant.domain.ports.broker import BrokerAdapter
from quant.domain.ports.strategy import Strategy, StrategyContext
from quant.domain.ports.storage import Storage
from quant.domain.ports.event_publisher import EventPublisher

# Infrastructure (implements domain ports)
from quant.infrastructure.events import EventBus, EventType, Event
from quant.infrastructure.execution.brokers.paper import PaperBroker
from quant.infrastructure.data.storage_duckdb import DuckDBStorage
from quant.infrastructure.data.providers.tushare import TushareProvider
from quant.infrastructure.data.providers.akshare import AkshareProvider

# Features (orchestrators)
from quant.features.backtest.engine import Backtester
from quant.features.trading.engine import Engine, SystemMode, Context
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine, RiskCheckResult
from quant.features.strategies import Strategy, StrategyRegistry
from quant.features.cio import CIOEngine
from quant.features.research import ResearchEngine, CandidatePool, ResearchScheduler

# Backward Compatibility (re-exports from domain)
from quant.shared.models import Order, Position, Trade
from quant.shared.utils import setup_logger, ConfigLoader
```

## 已清理的旧路径 (禁止使用)

- `quant.core.*` → `quant.features.trading.*` / `quant.features.backtest.*`
- `quant.data.*` → `quant.infrastructure.data.*`
- `quant.execution.*` → `quant.infrastructure.execution.*`
- `quant.models.*` → `quant.domain.models.*`
- `quant.utils.*` → `quant.shared.utils.*`
- `quant.strategies.*` → `quant.features.strategies.*`
- `quant.cio.*` → `quant.features.cio.*`
- `quant.config.*` → `quant.shared.config.*`
- `DuckDBProvider` → 直接使用 `DuckDBStorage` (不再作为 provider)

## Key Conventions

- Python 3.10+ with type hints
- No comments unless explicitly requested
- Frozen dataclasses for immutable value objects (domain models)
- ABC + abstract methods for ports (domain interfaces)
- Thread safety: `threading.RLock()` for shared state
- Logging: `from quant.shared.utils.logger import setup_logger`
- DuckDB readers must use `read_only=True` to prevent write-lock conflicts
