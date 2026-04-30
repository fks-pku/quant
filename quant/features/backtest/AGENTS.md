# Backtest Feature

## 职责

回测从数据加载到结果输出的完整闭环。包含回测引擎、步进验证、绩效分析。

## 文件结构

| 文件 | 职责 |
|------|------|
| `engine.py` | 编排器：日循环调度，委托所有子模块 |
| `entities.py` | 纯数据结构：BacktestResult, BacktestDiagnostics, CommissionConfig, Exporter, Context |
| `market_rules.py` | 市场规则注册：市场识别、手数、涨跌停、T+1 结算、停牌判定、FIFO 切片 |
| `commission.py` | 佣金引擎：CN/HK/US 费率计算、成交量上限 |
| `order_executor.py` | 订单执行管线：滑点→手数→成交量→佣金→资金校验→成交 |
| `dividend_processor.py` | 除权除息：现金/送股、CN 红利税 |
| `portfolio_factory.py` | 组合/风控创建：单 Portfolio 和 SubPortfolio 模式 |
| `nav_calculator.py` | NAV 计算 + 未平仓提取 |
| `analytics.py` | 绩效指标：Sharpe、Sortino、MaxDD、胜率、盈亏比等 |
| `data_validator.py` | 数据校验：列完整性、重复检测、OHLC 逻辑、价格跳变、日期缺口等 |
| `walkforward.py` | 步进验证 + `DataFrameProvider` 内存数据适配层 |

## 模块依赖链（严格 DAG）

```
domain/models/trade.py
    ↓
entities.py               (零内部依赖)
    ↓
market_rules.py           (依赖 entities)
commission.py             (依赖 entities + market_rules)
    ↓
order_executor.py         (依赖 entities + commission + market_rules)
dividend_processor.py     (依赖 entities + market_rules)
portfolio_factory.py      (依赖 entities)
nav_calculator.py         (依赖 entities)
    ↓
engine.py                 (编排器，依赖所有模块)
analytics.py              (独立，依赖 entities via domain)
walkforward.py            (依赖 engine + analytics)
```

## 对外契约

- `Backtester(config, event_bus=None, lot_sizes=None, ipo_dates=None)` - 回测编排器
- `BacktestResult(final_nav, total_return, sharpe_ratio, ...)` - 回测结果
- `BacktestDiagnostics` - 成交质量诊断
- `BacktestResultExporter.to_csv(result, output_path)` - 导出 equity + trades CSV
- `WalkForwardEngine(train_window_days, test_window_days, step_days)` - 步进验证
- `DataFrameProvider(data, dividends)` - DataFrame → OHLCV 查询适配层
- `DataValidator.validate(data)` → `ValidationReport` - 数据校验（errors + warnings + stats）
- `DataValidator.preflight(data)` - 回测前必检，有 error 直接 raise

## 日循环执行顺序（严禁重排 — 防未来信息泄露）

```
while current_date ≤ end:
    if 非交易日 → skip

    ① on_before_trading（可选 hook，策略预计算）

    ② prev_close_bars = dict(prev_bars)  # 保存昨日收盘用于涨跌停检查
       加载当日 Bar 数据 → today_bars / last_prices / prev_bars

    ③ 处理除权除息 → dividend_processor.process_dividends()

    ④ 执行昨日延迟订单 → order_executor.execute_order()
       使用 prev_close_bars（非 prev_bars）作为涨跌停检查依据

    ⑤ 喂入当日 Bar 给策略 → strategy.on_data()

    ⑥ 更新组合市价 → pos.update_market_price(close)

    ⑦ 策略生成信号 → strategy.on_after_trading()

    ⑧ 收集新订单 → deferred_orders

    ⑨ 记录 NAV → nav_calculator.calculate_daily_nav()
       reset_daily → portfolio.reset_daily() + risk_engine.reset_daily()
```

## 修改守则

| 改什么 | 改哪个文件 |
|--------|-----------|
| 回测日循环调度 | `engine.py` |
| 佣金费率/计算 | `commission.py` |
| 市场规则（涨跌停/T+1/手数） | `market_rules.py` |
| 订单执行流程（滑点/成交） | `order_executor.py` |
| 除权除息/红利税 | `dividend_processor.py` |
| 组合/风控创建逻辑 | `portfolio_factory.py` |
| NAV/未平仓计算 | `nav_calculator.py` |
| 数据结构定义 | `entities.py` |
| 绩效指标计算 | `analytics.py` |
| 数据校验规则 | `data_validator.py` |
| 步进验证逻辑 | `walkforward.py` |

## Known Pitfalls

- `prev_close_bars` 在 Step ② 中必须在 `prev_bars` 更新之前捕获，否则涨跌停检查会用今日数据
- `portfolio.reset_daily()` 和 `risk_engine.reset_daily()` 在每个日循环末尾调用，勿遗漏
- `order_executor` 中 SELL 路径的 settled_quantity 检查：CN T+1 用 `pos.settled_quantity()`，其他市场用 `pos.quantity`
- 未成交订单不重试：策略应自行在 `on_after_trading` 中基于最新数据重新判断
- `_BacktestContext` 和 `_BacktestOrderManager` 定义在 `entities.py`，不要暴露到外部
