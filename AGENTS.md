# AGENTS.md — Quant Trading System

## Architecture

Feature-Slice 垂直切片架构。业务功能按 Feature 组织，基础设施水平分层。

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
│   ├── models/        # 领域模型 (Order, Position, Trade)
│   ├── utils/         # 工具 (logger, config_loader, datetime_utils)
│   └── config/        # 配置 (config.yaml, brokers.yaml, strategies.yaml)
├── api/               # Flask 薄路由层
│   └── state/         # 运行时状态
└── tests/             # 测试
```

## Dependency Rules (铁律)

- **feature 之间禁止互 import**，只通过 service 对外
- **api/** 只调 features，不碰 infrastructure
- **infrastructure/** 不依赖任何 feature
- **shared/** 无业务语义，纯工具

## Feature Index

| Feature | Path | Description |
|---------|------|-------------|
| backtest | features/backtest/ | 回测引擎、步进验证、绩效分析 |
| trading | features/trading/ | 交易引擎、风控、调度器、组合管理 |
| portfolio | features/portfolio/ | 策略仓位跟踪、多策略协调 |
| cio | features/cio/ | CIO 市场评估、新闻分析、权重分配 |
| strategies | features/strategies/ | 策略基类、注册表、因子库、策略实现 |

## Python Package

- 包名: `quant`
- Import 格式: `from quant.features.backtest.engine import Backtester`
- 禁止相对导入，必须使用完整 `quant.*` 路径

## Commands

```bash
python quant/api_server.py                                        # 启动 API 服务
python -m pytest quant/tests/ -q                                  # 运行测试
python quant/backtest_runner.py --strategy SimpleMomentum ...     # CLI 回测
python quant/quant_system.py --mode paper                         # CLI 实盘/模拟
```

## Import Path Reference

```python
from quant.features.backtest.engine import Backtester
from quant.features.trading.engine import Engine, SystemMode, Context
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine, RiskCheckResult
from quant.features.portfolio.tracker import get_tracker, DEFAULT_STRATEGY
from quant.features.portfolio.coordinator import PortfolioCoordinator
from quant.features.strategies import Strategy, StrategyRegistry
from quant.features.cio import CIOEngine
from quant.infrastructure.events import EventBus, EventType
from quant.infrastructure.data import DuckDBStorage
from quant.infrastructure.execution import OrderManager, FillHandler
from quant.shared.models import Order, Position, Trade
from quant.shared.utils import setup_logger, ConfigLoader
```

## Key Conventions

- Python 3.10+ with type hints
- No comments unless explicitly requested
- Dataclasses for all data structures
- ABC + abstract methods for interfaces
- Thread safety: `threading.RLock()` for shared state
- Logging: `from quant.shared.utils.logger import setup_logger`
