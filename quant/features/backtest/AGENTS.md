# Backtest Feature

## 职责

回测从数据加载到结果输出的完整闭环。包含回测引擎、步进验证、绩效分析。

## 文件结构

| 文件 | 职责 |
|------|------|
| `engine.py` | 回测引擎核心：日循环、T+1 成交、佣金、持仓管理 |
| `analytics.py` | 绩效指标：Sharpe、Sortino、MaxDD、胜率、盈亏比等 |
| `walkforward.py` | 步进验证 + `DataFrameProvider` 内存数据适配层 |

## 对外契约

- `Backtester(config, event_bus=None, lot_sizes=None, ipo_dates=None)` - 回测引擎
- `BacktestResult(final_nav, total_return, sharpe_ratio, ...)` - 回测结果
- `BacktestDiagnostics` - 成交质量诊断（停牌、成交量限制、丢弃等）
- `BacktestResultExporter.to_csv(result, output_path)` - 导出 equity + trades CSV
- `WalkForwardEngine(train_window_days, test_window_days, step_days)` - 步进验证
- `DataFrameProvider(data, dividends)` - DataFrame → OHLCV 查询适配层

## 日循环执行顺序（严禁重排 — 防未来信息泄露）

```
while current_date ≤ end:
    if 非交易日 → skip

    ① on_before_trading（可选 hook，策略预计算）

    ② 加载当日 Bar 数据
       today_bars / last_prices / prev_bars / _suspended
       ⚠️ 此步骤只加载数据，不调用 strategy.on_data()

    ③ 处理除权除息（现金/送股 + CN 红利税）

    ④ 执行昨日延迟订单（T+1，当日开盘价成交）
       for each deferred_order:
         ├ 无 bar 数据或停牌 → 丢弃，diag.discarded_orders++
         ├ _execute_order 返回 [] → 丢弃，diag.discarded_orders++
         └ _execute_order 返回 Trade 列表 → 成交，on_fill 回调
       deferred_orders = []  （清空，不重试）

    ⑤ 喂入当日 Bar 给策略
       strategy.on_data(ctx, bar)
       ⚠️ 在 fill 之后调用，portfolio.positions 已反映当日成交结果
          策略在 on_data 中看到的持仓是最新状态

    ⑥ 更新组合市价
       pos.update_market_price(close)

    ⑦ 策略生成信号
       strategy.on_after_trading(ctx, today)

    ⑧ 收集新订单 → deferred_orders
       pending_orders 打上 _signal_date

    ⑨ 记录 NAV + reset_daily
       equity_curve.append(nav)
       portfolio.reset_daily()
       risk_engine.reset_daily()
```

### 执行顺序设计原理

1. **④ 在 ⑤ 之前**：先执行昨日订单再喂数据，策略看到的持仓是成交后的最新状态
2. **⑤ 在 ⑦ 之前**：先喂数据再生成信号，策略基于完整的市场数据做决策
3. **⑦ 在 ⑧ 之前**：信号生成后立即收集，确保不遗漏
4. **⑨ 在最后**：NAV 和日状态重置放在所有操作完成后
5. **④ 不重试**：未成交订单直接丢弃，策略在 ⑦ 中基于最新数据重新判断是否下单

## 单笔订单执行流程（`_execute_order`）

统一先做成交前置检查，全部通过后才计佣金和更新状态：

```
① 开盘价有效性检查（≤0 → 丢弃）
② 涨跌停检查（CN 专属：±10/20/30%，IPO 5 天豁免）
   → 返回 []（丢弃），计入 limit_rejected_orders
③ 滑点修正：fill_price = open ± slippage
④ 手数取整（HK/CN）：
   BUY：向下取整手（<1手 → 丢弃）
   SELL：保留原数量（允许碎股）
⑤ 成交量上限：≤ 日成交量 × 5%（不足 → 丢弃）
⑥ 仅校验：计算佣金 → 拼 total_cost/cash_check
⑦ 资金不足（BUY）/ 无可卖仓位或 T+1 未结算（SELL）→ 丢弃
⑧ 以上全部通过 → 记录佣金、更新持仓（含 realized_pnl）、扣/加 cash、生成 Trade
```

佣金只在第 ⑧ 步确认成交后才计入 `diag.total_commission`，不会出现先扣佣金后判定无法成交的情况。

## 市场差异化规则

| 规则 | CN (A股) | HK (港股) | US (美股) |
|------|----------|----------|----------|
| 市场识别 | 6位数字，首码 0/3/6/8/9 | `HK.` 前缀 或 5位数字 | 其余 |
| 货币 | CNY | HKD | USD |
| 手数 | 100 股/手 | 可配置（默认 100） | 无限制 |
| T+N 结算 | T+1（当日买入次日可卖） | T+0 | T+0 |
| 涨跌停 | ±10%/20%/30% + IPO 前5天豁免 | 无 | 无 |
| 红利税 | ≤30天 20%, 31-365天 10%, >365天 0% | 无 | 无 |

## 佣金模型

| 市场 | BUY 费用项 | SELL 费用项 | 特殊规则 |
|------|-----------|-----------|---------|
| US | per_share × qty | per_share × qty + SEC fee + FINRA TAF | 最低 $1.0/单 |
| CN | 0.025% + 过户费 + 规管费 | 0.025% + 0.05% 印花税 + 过户费 + 规管费 | 最低 ¥5/单 |
| HK | 0.03% + SFC levy + 清算费 + 交易费 + 系统费 | 0.03% + 0.13% 印花税 + SFC levy + 清算费 + 交易费 + 系统费 | 最低 HK$3/单，系统费 HK$0.50 |

佣金含在 Trade 的 `cost_breakdown` 字段中，`commission = sum(cost_breakdown.values())`。

## Trade 记录约定

- **BUY Trade**：`entry_time == exit_time`，`pnl = realized_pnl = -commission`（已实现佣金支出）。分析模块通过 `_round_trip_trades()` 过滤 SELL 成交来计算胜率/盈亏比，BUY 不计入。
- **SELL Trade**：按 FIFO lot 切片，每 lot 生成一条 Trade。`pnl = (fill_price - lot_price) * qty - sub_commission`，`realized_pnl = (fill_price - lot_price) * qty`（不含佣金）。realized_pnl 通过 `portfolio.update_position(realized_pnl=...)` 传入，由 Portfolio 层跟踪。
- **total_gross_pnl**：`sum(t.pnl for all trades) + diag.total_commission` = 还原除佣金前毛利润。

## 诊断追踪（`BacktestDiagnostics`）

| 字段 | 含义 |
|------|------|
| `suspended_days` | 停牌日数（当日有任意标的 volume=0） |
| `volume_limited_trades` | 因超 5% 成交量上限被截断的订单数 |
| `lot_adjusted_trades` | 因手数取整被调整的订单数 |
| `fill_count` | 成交笔数 |
| `discarded_orders` | 被丢弃的订单总数（含停牌、涨跌停、资金不足等所有原因） |
| `t1_rejected_sells` | 因 T+1 未结算被拒绝的卖单（含在 discarded_orders 中） |
| `limit_rejected_orders` | 因 CN 涨跌停被丢弃的订单（含在 discarded_orders 中） |
| `total_commission` | 累计佣金 |
| `risk_skipped_orders` | 被风控拒绝的订单 |
| `cost_drag_pct` | 佣金 / |毛利润| × 100% |

## 依赖

- `features/trading` — Portfolio（持仓跟踪）、RiskEngine（风控预检）
- `domain/models` — Trade、Position、Order
- `shared/utils` — logger

## 修改守则

- 改回测引擎逻辑：只动 `engine.py`
- 改绩效指标计算：只动 `analytics.py`
- 改进退验证逻辑：只动 `walkforward.py`

## `_execute_order` 返回值约定

- `List[Trade]`（非空）→ 成交，调用方处理 Trade 和 on_fill 回调
- `[]`（空列表）→ 丢弃（资金不足、无仓位、涨跌停、开盘价无效等所有不可成交原因）

## Known Pitfalls

- `Backtester._create_context()` 使用了内部类 `BacktestOrderManager`/`BacktestBroker`，不要暴露到外部
- `lot_sizes` 字典的 key 格式要与 symbol 一致（包含 `HK.`/`US.` 前缀，CN 为纯数字）
- `portfolio.reset_daily()` 和 `risk_engine.reset_daily()` 在每个日循环末尾调用，勿遗漏否则日内亏损限额和风控计数器不重置
- `pos.update_market_price(price)` 应替代直接写 `pos.market_value`/`pos.unrealized_pnl`
- `RiskEngine._check_order_rate()` 在回测模式（as_of_date != None）使用日计数器替代 wall clock，实盘模式仍用时间戳
- `_check_position_size()` 会累计当日同标的的 pending 订单金额，防止多次下单绕过仓位限制
- `portfolio.update_position()` 的 SELL 路径接受 `realized_pnl` 参数，不要在外部手动修改 `pos.realized_pnl`
- `entry_times` 每次 BUY 都会刷新，`open_positions` 优先使用 `_earliest_lot_time(pos)`
- 未成交订单不重试：策略应自行在 `on_after_trading` 中基于最新数据重新判断是否下单
