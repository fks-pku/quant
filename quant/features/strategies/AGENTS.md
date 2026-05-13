# Strategies Feature

## 职责

策略定义、注册、发现、框架组件。当前保留策略基类、日线策略辅助基类、注册表；无内置具体策略。

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
- 当前策略池为空；新增策略时再创建对应子目录

## 修改守则

- 改策略框架：只动 `base.py`, `registry.py`
- 新增策略：在 `strategies/` 下新建目录
- 每个策略需要配一个AGENTS.md，展示其基本概述

## Known Pitfalls

- 策略在 `__init__` 中不要访问 Context，Context 在 `on_start` 时才设置
- 策略名称要与装饰器参数一致
