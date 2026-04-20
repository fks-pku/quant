# Feature-Slice 垂直切片重构设计

## 目标

将现有水平分层架构重构为 Feature-Slice 垂直切片，解决"改A错B"的耦合问题。每个业务功能自成一体，AI 协作时只需关注单个 feature 目录。

## 约束

- 后端全面重构，前端只适配 API
- REST API 路径保持不变
- 每个 feature 配 AGENTS.md（非 CLAUDE.md）
- 最外层加全局 AGENTS.md

## 1. 目录总结构

```
quant/
├── features/                        # 垂直切片，每个功能自成一体
│   ├── backtest/                    # 回测完整闭环
│   ├── trading/                     # 实盘/模拟交易
│   ├── portfolio/                   # 仓位管理
│   ├── cio/                         # CIO 市场评估
│   └── strategies/                  # 策略框架 + 所有策略实现
├── infrastructure/                  # 基础设施（无业务语义）
│   ├── data/                        # 数据存储 + Provider
│   ├── execution/                   # 券商适配 + 订单执行
│   └── events/                      # 事件总线
├── shared/                          # 跨 feature 纯共享
│   ├── models/                      # 领域模型
│   ├── utils/                       # 工具
│   └── config/                      # 配置加载
├── api/                             # Flask 薄路由层
├── api_server.py                    # 入口
├── quant_system.py                  # CLI 入口
└── backtest_runner.py               # CLI 入口
```

## 2. 依赖规则（铁律）

```
api/  →  features/*/service.py  →  infrastructure/ + shared/
                ↑                         ↑
          禁止跨 feature import      禁止依赖 feature
```

| 规则 | 说明 |
|------|------|
| feature 间禁止互 import | backtest 不能 import trading 的内部文件，只能通过 service api |
| feature 只暴露 service.py | api.py 是类型契约，service.py 是唯一入口 |
| infrastructure 不感知 feature | data/execution/events 不知道谁在调用它 |
| api/ 不直接碰 infrastructure | 路由层只调 feature service，永远不 import infrastructure |
| shared/ 无业务语义 | models/utils/config 是纯工具，不含任何业务逻辑 |

唯一允许的跨 feature 调用：通过 `infrastructure/events/event_bus.py` 发布/订阅事件。

## 3. 各 Feature 内部结构

### 3.1 features/backtest/

```
features/backtest/
├── api.py               # 对外契约：类型 + 函数签名
├── service.py           # 回测编排逻辑（合并 backtest_runner.py + backtest_bp.py 逻辑）
├── engine.py            # 回测引擎（原 core/backtester.py）
├── walkforward.py       # 步进验证（原 core/walkforward.py）
├── analytics.py         # 绩效分析（原 core/analytics.py）
├── AGENTS.md
└── __init__.py
```

对外契约（api.py）：
- `run_backtest(config: BacktestConfig) -> BacktestResult`
- `run_walkforward(config: WalkforwardConfig) -> WalkforwardResult`

### 3.2 features/trading/

```
features/trading/
├── api.py               # 对外契约
├── service.py           # 交易编排（原 quant_system.py 核心逻辑）
├── engine.py            # 交易引擎（原 core/engine.py，移除 PaperBroker 直接依赖）
├── risk.py              # 风控（原 core/risk.py）
├── scheduler.py         # 调度器（原 core/scheduler.py）
├── portfolio.py         # 仓位/净值跟踪（原 core/portfolio.py）
├── AGENTS.md
└── __init__.py
```

对外契约（api.py）：
- `start_trading(config: TradingConfig) -> None`
- `stop_trading() -> None`
- `get_trading_status() -> TradingStatus`

### 3.3 features/portfolio/

```
features/portfolio/
├── api.py               # 对外契约
├── service.py           # 仓位查询/管理编排
├── tracker.py           # 策略仓位跟踪（原 execution/strategy_position_tracker.py）
├── coordinator.py       # 多策略协调（原 execution/portfolio_coordinator.py）
├── AGENTS.md
└── __init__.py
```

对外契约（api.py）：
- `get_strategy_positions(strategy_id: str) -> dict`
- `get_combined_portfolio() -> CombinedPortfolio`
- `check_risk(strategy_id: str, order: Order) -> RiskCheckResult`

### 3.4 features/cio/

```
features/cio/
├── api.py               # 对外契约
├── service.py           # CIO 编排（原 api/state.py 中 CIO 相关逻辑）
├── engine.py            # CIO 引擎（原 cio/cio_engine.py）
├── market_assessor.py   # 市场评估（原 cio/market_assessor.py）
├── news_analyzer.py     # 新闻分析（原 cio/news_analyzer.py）
├── weight_allocator.py  # 权重分配（原 cio/weight_allocator.py）
├── llm_adapters/        # LLM 适配器（保持原结构）
│   ├── base.py
│   ├── openai_adapter.py
│   ├── claude_adapter.py
│   └── ollama_adapter.py
├── config/
│   └── cio_config.yaml
├── AGENTS.md
└── __init__.py
```

### 3.5 features/strategies/

```
features/strategies/
├── api.py               # 对外契约
├── service.py           # 策略管理编排（策略启停、状态查询、元数据）
├── registry.py          # 策略注册表 + @strategy 装饰器
├── base.py              # 策略 ABC
├── factors.py           # 因子库
├── framework/           # 策略框架组件（拆分原 framework.py）
│   ├── signal_generator.py
│   ├── portfolio_constructor.py
│   ├── alpha_engine.py
│   └── execution_scheduler.py
├── simple_momentum/
│   ├── strategy.py
│   └── config.yaml
├── volatility_regime/
│   ├── strategy.py
│   └── config.yaml
├── cross_sectional_mr/
│   ├── strategy.py
│   └── config.yaml
├── AGENTS.md
└── __init__.py
```

原 api/state.py 中硬编码的策略元数据全部移入 service.py，从 registry + config 动态读取。

## 4. Infrastructure 层

```
infrastructure/
├── data/
│   ├── __init__.py
│   ├── providers/
│   │   ├── base.py
│   │   ├── yahoo.py
│   │   ├── alpha_vantage.py
│   │   ├── yfinance_provider.py
│   │   ├── futu.py
│   │   └── duckdb_provider.py
│   ├── storage.py
│   ├── storage_duckdb.py
│   ├── normalizer.py
│   └── symbol_registry.py
├── execution/
│   ├── __init__.py
│   ├── brokers/
│   │   ├── base.py              # BrokerAdapter ABC（移除 models re-export）
│   │   ├── paper.py
│   │   ├── futu.py
│   │   ├── futu_connection.py
│   │   ├── futu_trade.py
│   │   └── futu_position.py
│   ├── order_manager.py
│   └── fill_handler.py
└── events/
    ├── __init__.py
    └── event_bus.py             # EventBus + EventType
```

关键修改：
- brokers/base.py 移除 Order/Position 等 re-export，统一从 shared.models import
- paper.py 的 update_price() 提升到 BrokerAdapter ABC，消除 engine 中的 isinstance 检查

## 5. Shared 层

```
shared/
├── __init__.py
├── models/
│   ├── __init__.py
│   ├── order.py
│   ├── position.py
│   ├── trade.py
│   └── account.py
├── utils/
│   ├── __init__.py
│   ├── logger.py
│   ├── datetime_utils.py
│   └── config_loader.py
└── config/
    ├── config.yaml
    ├── brokers.yaml
    └── strategies.yaml
```

## 6. API 层

```
api/
├── __init__.py
├── state_bp.py
├── strategies_bp.py
├── backtest_bp.py
├── cio_bp.py
├── futu_bp.py
├── positions_bp.py
├── system_bp.py
└── state/
    ├── __init__.py
    └── runtime.py              # 仅保留系统运行时状态（< 50 行）
```

原 api/state.py（446 行）拆解：
- 策略元数据/模拟逻辑 → features/strategies/service.py
- CIO 工厂 → features/cio/service.py
- 系统生命周期 → features/trading/service.py
- 纯运行时状态 → api/state/runtime.py

每个 blueprint 变成 10-30 行薄层：参数解析 + 调 service + 格式化响应。API 路径完全不变。

## 7. 文件搬迁映射

| 原路径 | 新路径 |
|--------|--------|
| `core/engine.py` | `features/trading/engine.py` |
| `core/portfolio.py` | `features/trading/portfolio.py` |
| `core/risk.py` | `features/trading/risk.py` |
| `core/scheduler.py` | `features/trading/scheduler.py` |
| `core/backtester.py` | `features/backtest/engine.py` |
| `core/walkforward.py` | `features/backtest/walkforward.py` |
| `core/analytics.py` | `features/backtest/analytics.py` |
| `core/events.py` | `infrastructure/events/event_bus.py` |
| `execution/order_manager.py` | `infrastructure/execution/order_manager.py` |
| `execution/fill_handler.py` | `infrastructure/execution/fill_handler.py` |
| `execution/portfolio_coordinator.py` | `features/portfolio/coordinator.py` |
| `execution/strategy_position_tracker.py` | `features/portfolio/tracker.py` |
| `execution/brokers/*` | `infrastructure/execution/brokers/*` |
| `strategies/base.py` | `features/strategies/base.py` |
| `strategies/registry.py` | `features/strategies/registry.py` |
| `strategies/factors.py` | `features/strategies/factors.py` |
| `strategies/framework.py` | `features/strategies/framework/*.py`（拆为 4 个文件）|
| `strategies/<name>/` | `features/strategies/<name>/` |
| `cio/*` | `features/cio/*` |
| `data/*` | `infrastructure/data/*` |
| `models/*` | `shared/models/*` |
| `utils/*` | `shared/utils/*` |
| `config/*` | `shared/config/*` |
| `api/state.py` | 拆解到各 feature service + `api/state/runtime.py` |

## 8. 核心解耦改动

| 问题 | 解法 |
|------|------|
| api/state.py 446 行上帝模块 | 拆解到各 feature service，runtime 状态 <50 行 |
| core/engine.py → PaperBroker isinstance | update_price() 提升到 BrokerAdapter ABC |
| backtest_bp.py 重复 backtest_runner.py 逻辑 | 统一到 features/backtest/service.py |
| brokers/base.py re-export models | 移除，统一从 shared.models import |
| framework.py 411 行 5 个类 | 拆为 4 个独立文件 |
| 硬编码策略元数据（20+ 全局变量） | 从 registry + config 动态读取 |
| 18 处 lazy import 隐藏依赖 | 消除循环风险后转为顶层 import |

## 9. AGENTS.md 体系

### 每个 Feature 的 AGENTS.md 模板

```markdown
# <Feature Name>

## 职责
<1-2 句话说明这个 feature 做什么>

## 对外契约（service.py）
- `function_name(input: Type) -> ReturnType` — 简要说明

## 依赖
- infrastructure/data — 通过 xxx
- infrastructure/execution — 通过 xxx
- shared/models — xxx

## 不变量
- <必须始终成立的业务规则>

## 修改守则
- 改业务逻辑：只动 service.py / 内部模块
- 改数据结构：必须同步更新 api.py 类型并跑测试
- 新增功能：先写测试，再实现

## Known Pitfalls
- <历史踩过的坑>
```

### 全局 AGENTS.md（最外层 + quant/ 根）

包含：架构概览、Feature 索引、依赖规则、Python 包约定、常用命令。

## 10. 迁移策略

6 阶段，每阶段独立可验证，必须 `python -m pytest quant/tests/ -q` 全绿：

1. **Phase 1**: 创建骨架目录 + shared/ 迁移
2. **Phase 2**: infrastructure/ 迁移（data + execution + events）
3. **Phase 3**: feature 搬迁（portfolio → trading → backtest → cio → strategies）
4. **Phase 4**: API 层重构（拆解 state.py，薄路由化）
5. **Phase 5**: 入口文件适配（api_server.py / quant_system.py / backtest_runner.py）
6. **Phase 6**: 写各 feature AGENTS.md + 更新根 AGENTS.md + 最外层全局 AGENTS.md

## 11. Import 路径变更

```python
# 旧
from quant.core.backtester import Backtester
from quant.core.events import EventBus, EventType
from quant.models import Order, Position
from quant.strategies.base import Strategy

# 新
from quant.features.backtest.engine import Backtester
from quant.infrastructure.events.event_bus import EventBus, EventType
from quant.shared.models import Order, Position
from quant.features.strategies.base import Strategy
```

shared/models/\_\_init__.py 保持便捷导出：`from quant.shared.models import Order`。

## 12. 预期收益

| 指标 | 现状 | 重构后 |
|------|------|--------|
| 改回测功能影响的文件范围 | 跨 core/, api/, strategies/ 6-10 文件 | 仅 features/backtest/ 内 2-3 文件 |
| 改策略不影响其他功能 | 否（state.py 硬编码所有策略元数据） | 是（strategies feature 自包含） |
| AI 改一个功能的上下文 | 需读 5+ 目录 | 只读 1 个 feature 目录 + AGENTS.md |
| api/state.py | 446 行上帝模块 | <50 行纯运行时状态 |
