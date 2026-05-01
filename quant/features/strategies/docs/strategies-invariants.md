# Strategies 不变量

Strategies 模块提供策略框架：注册表、基类、因子库。

---

## 通用约定

| 组件 | 职责 |
|------|------|
| `Strategy` (ABC) | 策略基类，定义 on_start/on_data/on_after_trading 等钩子 |
| `StrategyRegistry` | 全局注册表，`@strategy` 装饰器 + 目录自动发现 |
| `Factors` | 因子计算工具库 |

### 核心不变式

- S1 `@strategy("Name")` 装饰后 `StrategyRegistry.is_registered("Name") == True`
- S2 `StrategyRegistry.create("Unknown")` 抛 `ValueError`
- S3 策略名称大小写敏感（注册时原样存储）
- S4 `Strategy._adj(bar, "close")` 优先用 `adj_close`，缺失/NaN 时回退 `close`
- S5 `Strategy.buy/sell` 在 `context==None` 时返回 `None`（静默失败）

---

## CASE-1: 注册表 CRUD

### 操作

1. `@strategy("TestStrat")` 装饰一个类
2. `StrategyRegistry.is_registered("TestStrat")`
3. `StrategyRegistry.create("TestStrat", param=1)`
4. `StrategyRegistry.list_strategies()` 包含 "TestStrat"
5. `StrategyRegistry.create("NonExist")` → ValueError

### 断言

```
S1-01  is_registered("TestStrat") == True
S1-02  create 返回正确实例
S1-03  create("NonExist") raises ValueError
S1-04  list_strategies() 包含注册名
```

### 对应测试: `test_s1_*` in `test_strategies_invariants.py`

---

## CASE-2: _adj 辅助函数优先级

### 操作

1. `_adj({"close": 100.0, "adj_close": 105.0}, "close")` → 105.0
2. `_adj({"close": 100.0}, "close")` → 100.0
3. `_adj({"close": 100.0, "adj_close": NaN}, "close")` → 100.0
4. `_adj({"close": 100.0, "adj_close": None}, "close")` → 100.0

### 断言

```
S2-01  有 adj_close 时用 adj_close
S2-02  无 adj_close 时用 close
S2-03  adj_close 为 NaN 时回退 close
S2-04  adj_close 为 None 时回退 close
```

### 对应测试: `test_s2_*` in `test_strategies_invariants.py`

---

## CASE-3: buy/sell 无 context 静默失败

### 前置条件

`Strategy` 子类实例，`context=None`

### 操作

1. `strategy.buy("AAPL", 100)` → None
2. `strategy.sell("AAPL", 100)` → None

### 断言

```
S3-01  buy returns None when context is None
S3-02  sell returns None when context is None
```

### 对应测试: `test_s3_*` in `test_strategies_invariants.py`

---

## CASE-4: on_fill 更新内部持仓

### 前置条件

`Strategy` 子类实例

### 操作

1. `on_fill(ctx, fill(side="BUY", quantity=100, symbol="AAPL"))`
2. `on_fill(ctx, fill(side="SELL", quantity=40, symbol="AAPL"))`

### 断言

```
S4-01  after BUY: _positions["AAPL"] == 100
S4-02  after SELL: _positions["AAPL"] == 60
```

### 对应测试: `test_s4_*` in `test_strategies_invariants.py`
