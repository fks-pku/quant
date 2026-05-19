# Strategies Feature

## 职责

策略定义、注册、发现、框架组件。包含策略基类、日线策略辅助基类、注册表，以及研究生成/候选策略目录。

## 对外契约

- `Strategy(ABC)` - 策略抽象基类
- `StrategyRegistry` - 策略注册表
- `@strategy` - 策略装饰器

## 依赖

- `shared/utils` - logger
- `infrastructure/events` - EventBus (通过 Context)

## 不变量

- 所有策略必须使用 `@strategy` 装饰器注册
- 策略 ID 唯一，不区分大小写
- 策略目录结构: `strategies/<name>/strategy.py`
- 研究生成策略默认保持 `enabled: false` / `status: candidate`
- A 股低价小市值类策略必须显式处理退市风险：买入过滤 ST/停牌/非上市/list_status、价格下限和流动性下限，持仓触发风险时每日尝试退出

## 修改守则

- 改策略框架：只动 `base.py`, `registry.py`
- 新增策略：在 `strategies/` 下新建目录，包含 `strategy.py`、`config.yaml`、`README.md`
- 只有当策略有额外本地约束或复杂审计要求时才新增策略目录级 `AGENTS.md`

## Known Pitfalls

- 策略在 `__init__` 中不要访问 Context，Context 在 `on_start` 时才设置
- 策略名称要与装饰器参数一致
