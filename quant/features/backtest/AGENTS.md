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
| `order_executor.py` | 订单执行管线：成交价解析（MARKET/LIMIT）→手数→成交量→冲击成本→佣金→资金校验→成交 |
| `dividend_processor.py` | 除权除息：现金/送股、CN 红利税 |
| `portfolio_factory.py` | 组合/风控创建：单 Portfolio 和 SubPortfolio 模式 |
| `nav_calculator.py` | NAV 计算 + 未平仓提取 |
| `analytics.py` | 绩效指标：Sharpe、Sortino、MaxDD、胜率、盈亏比等 |
| `data_validator.py` | 数据校验：列完整性、重复检测、OHLC 逻辑、价格跳变、日期缺口等 |
| `data_provider.py` | `DataFrameProvider` 内存数据适配层：Bar/分红预索引、O(1) 点查 |
| `walkforward.py` | 步进验证引擎 + 结果导出 |

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
data_provider.py          (零内部依赖，懒导入 data_validator)
walkforward.py            (依赖 engine + analytics + data_provider)
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

    ⑤ 批量喂入当日 Bar 给策略 → strategy.on_data_batch()，无批量 hook 时回落到 on_data()

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
| 数据预索引/点查 | `data_provider.py` |
| 步进验证逻辑 | `walkforward.py` |

## Known Pitfalls

- Backtest signal generation after daily order-manager prep must call runtime `run_daily_snapshots()`; do not split D-close feed, portfolio close-price marking, and `after_trading` into a separate backtest-only path.
- CN commission routing is security-type aware: stock-like 6-digit CN symbols keep stamp duty, while ETF/LOF/fund code prefixes use the CN fund commission path with no stock stamp duty. Research backtests default fund fees to `fund_percent=0.0001` and `fund_min_per_order=0.0`.
- BUY liquidity control is a global execution invariant: after market impact is applied, final BUY notional must still be `<= max_participation_rate * ADV value` (default research gate 5% ADV). Do not implement per-strategy sizing that assumes this cap can be bypassed.
- `prev_close_bars` 在 Step ② 中必须在 `prev_bars` 更新之前捕获，否则涨跌停检查会用今日数据
- `portfolio.reset_daily()` 和 `risk_engine.reset_daily()` 在每个日循环末尾调用，勿遗漏
- `order_executor` 中 SELL 路径的 settled_quantity 检查：CN T+1 用 `pos.settled_quantity()`，其他市场用 `pos.quantity`
- 未成交订单不重试：策略应自行在 `on_after_trading` 中基于最新数据重新判断
- `_BacktestContext` 和 `_BacktestOrderManager` 定义在 `entities.py`，策略只能看到 scoped `order_manager` 代理；不要把 raw `_BacktestOrderManager` 暴露给策略
- Engine 通过 `_BacktestContext.prepare_for_trading_day()` 和 `drain_orders()` 公开方法与 Context 交互，不要直接访问 `_pending_orders`、`_current_date`、`_last_prices` 等私有属性
- Market order 无有效价格时（`effective_price <= 0`）直接 reject，不再用 share count 充当 dollar value 绕过风控
- CN 回测数据应通过 `DuckDBProvider`/`DuckDBStorage(use_security_status=True)` 读取；它会用 `cn_status.duckdb::cn_security_status_daily` 给股票 `daily_cn_ochl` 补 `_suspended`、`tradable`、`is_st`、`up_limit/down_limit`，并为无 OHLC 的停牌日生成 synthetic bar；ETF 和指数日线分别从 `cn_etf`/`cn_index` sidecar 读取
- ST 不自动从回测 universe 剔除，也不自动禁止交易；优先用 status 表 `up_limit/down_limit`，缺失时 `is_st=True` 按 5% 涨跌停 fallback
- `is_suspended()` 优先检查 `bar["_suspended"]` / `tradable=False` / `has_daily_bar=False` 显式状态，其次才用 volume=0 / close=open=0 启发式
- 研究生成的小市值低价策略通过策略层 `delisting_risk_guard` 过滤退市风险：`close >= 2`、流动性下限、ST/停牌/非上市/list_status 过滤，并在持仓触发风险时每日尝试 SELL；该退出不能被 `holding_days` 调仓门控阻挡，见 `test_backtest_invariants.py` CASE-37
- 当日 `tradable=False` 的标的提交订单应在 `_BacktestOrderManager` submission 阶段拒绝；已有 deferred order 到停牌日则在 Step ④ 丢弃
- `DataFrameProvider._build_index()` 遇到重复 (symbol, date) 时保留 volume 更高的行，而非先到先得
- `domain.ports.strategy.Strategy` 是架构端口定义，实际策略必须继承 `features.strategies.base.Strategy`
- 多策略回测必须使用独立 `SubPortfolio`；未显式传 `strategy_allocations` 时按策略数等权分配初始资金
- 显式 `strategy_allocations` 必须覆盖所有策略且不能包含未知策略名，总和必须 `<= 1.0`
- master Portfolio 不承载策略持仓；它只保留未分配现金，并在子组合 `close()` 后回收现金
- 只有单策略且未传 `strategy_allocations` 时才使用非 SubPortfolio 模式
- 重复策略名称会在 `engine.py run()` 开头抛出 `ValueError`，防止 fills 交叉到错误策略
- `_BacktestOrderManager` 用 `_pending_buy_symbols` 集合拒绝同一天同一策略对同一 symbol 的重复 BUY 订单（日线策略去重）
- `profit_factor` 在 gross_loss==0（全胜）时返回 `MAX_PROFIT_FACTOR=9999.0` 而非 `float('inf')`（避免 JSON 序列化失败）
- `sortino` 在全正收益时返回 `float('inf')`（完美策略不应排在中庸之下）
- HK 印花税双向收取（BUY+SELL 各 0.1%），使用 `math.ceil()` 向上取整到整数元
- CN 涨跌停价格计算使用 `_round_half_up()`（四舍五入），不使用 Python `round()`（银行家舍入）
- CN 市场识别按 6 位数字处理，覆盖 A 股股票、ETF/基金、债券与 B 股；如 `510300`、`159915`、`512880` 必须归入 CNY
- CN 涨跌停拒绝按方向处理：涨停拒绝 BUY、跌停拒绝 SELL；涨停 SELL 和跌停 BUY 不因涨跌停规则拒绝
- LIMIT 订单必须有正数限价；BUY 仅在 next open <= limit 时成交，SELL 仅在 next open >= limit 时成交，冲击成本后不得穿越限价
- 一个回测实例不得混合币种；跨 USD/CNY/HKD 标的需要拆成不同回测或引入明确 FX 层
- 停牌 synthetic bar 不更新 `last_prices`/`prev_bars`，避免用停牌填充值重估 NAV 或污染后续涨跌停基准
- 最后一个交易日 after-close 产生的 deferred order 没有真实下一交易日，应过期计入 diagnostics，不得用 synthetic bar 成交
- `on_stop` 订单不是普通 T+1 deferred order；默认 `backtest.force_close_on_stop=True` 时按最后有效 close 做强制清算，并写入 `forced_closeout_orders/forced_closeout_trades`，设为 `False` 时订单过期丢弃
- `DataFrameProvider.get_bars()` 基于 `_bar_map` 去重索引返回数据，与 `get_bar_for_date()` 一致
- Generic `data_provider.get_bars()` fallback must request and retain only `current_date`; never use `current_date + 1 day`, because inclusive DuckDB ranges would leak tomorrow's daily bar.
- Daily order submission rejects symbols missing from `today_bars`; `on_stop` close-out passes `tradable_today=None` to bypass this daily-bar gate explicitly.
- `process_dividends()` 记录 `total_cash_dividends/total_dividend_tax/total_net_dividends`，并返回送股记录列表；engine 在 Step ③ 后分发 synthetic fill 给策略以保持 `strategy._positions` 同步
- ETF/基金日线出现大幅 `adj_factor` 跳变且原始价格反向跳变时，Step ③ 必须按份额折算调整组合 lots 并发 synthetic fill 同步策略仓位，避免把份额折算当成真实收益
- SubPortfolio 模式下送股 synthetic fill 必须只分发给对应策略，不能广播给所有持有同一 symbol 的策略
- 严格回测报告使用的日线 provider 若底层存在 `corp_actions.cn_dividends`，必须暴露 `get_dividend_for_date()`；否则 cash dividend、CN 红利税和送股 synthetic fill 都不会进入回测
- A 股严格回测若启用冲击成本模型，provider 必须提供模型字段（如 `adv20_value`、`volatility20`），并在报告 constraints 中写清 `execution_cost_model`
- CN 日线启用 `execution_cost_model` 的 MARKET 信号必须在 D 日提交阶段冻结为成本保护 LIMIT；其他市场需显式 `market_orders_as_limits: true`；限价只使用 D 日可知 reference/ADV/volatility，D+1 数据只能决定可成交性和成交结果
- 交易级统计的 round-trip PnL 必须包含按 FIFO 分摊的 BUY 佣金；SELL trade 的 `pnl` 只覆盖卖出侧佣金
- `DataFrameProvider._trading_dates` 存储 `date` 对象，engine 使用 `current_date.date()` 查询
- Walk-forward `test_sharpe_std` 使用 `ddof=1`（样本标准差）
- `risk_price_deviation_limit` 从 config 读取（键名 `risk_price_deviation_limit`），而非硬编码 0.15
- `order.strategy` 为 `None` 时 engine 记录 error 并跳过 fill 分发，不会广播到所有策略
- Step ⑤ 优先调用策略 `on_data_batch(context, bars)`；新增日线策略应继承 `DailyBarStrategy` 或显式实现批量 hook，保持与单条 `on_data` 语义一致
- `DataValidator.validate()` 对坏 timestamp、NaN/null、非数值 OHLCV 必须返回 `ValidationReport`，不得冒出 pandas 原始异常
- `execute_order()` 在任何 cash/position mutation 前必须拒绝非有限或非正成交价，使用 `PRICE_INVALID`
- `on_stop` forced close-out 是显式清仓语义；默认可绕过 CN T+1，且 final NAV 更新最后真实交易日，不追加 `end+1`
- CN SELL 的 volume cap 不做整手归零；碎股/部分成交数量应可通过，CN BUY 与 HK 仍按 lot size 约束
- CN SELL lot handling allows a mixed round-lot plus odd-lot quantity such as 150 shares to sell in one order; only CN BUY and HK orders enforce lot-size rounding.
- benchmark 显著性是单尾跑赢检验；负 excess-return `t_stat` 不能标记为显著跑赢
- IPO 日期元数据可能是 `date` 或 `datetime`，涨跌停豁免判断前必须归一化
