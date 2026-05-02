# Portfolio Feature

## 职责

多策略仓位归属、协调、风控检查。包含策略仓位跟踪器和组合协调器。

## 对外契约

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
