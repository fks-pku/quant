# Backtest Engine Invariants

> 每次 Agent 修改回测系统代码后，逐条验证。所有条目必须 PASS。
>
> **验证方式标记**：
> - `[AUTO]` = 有自动化测试，运行 pytest 即可验证
> - `[MANUAL]` = 需人工审查代码逻辑，无自动化测试

---

## 执行顺序

| # | Invariant | 验证 | 测试 |
|---|-----------|------|------|
| E-1 | 信号日 T 的订单在 T+1 以开盘价成交 | `[AUTO]` | `test_backtest_core::TestBacktesterExecution` |
| E-2 | `on_before_trading` → `on_data` → `on_after_trading` 顺序不可变 | `[MANUAL]` | `engine.py` 日循环步骤 ①②③④ |
| E-3 | 延迟订单填充在策略新订单收集之前 | `[MANUAL]` | `engine.py:288-342` |
| E-4 | 每日循环末尾调用 `portfolio.reset_daily()` 和 `risk_engine.reset_daily()` | `[MANUAL]` | `engine.py:350-351` |
| E-5 | `current_date` 步进后非交易日由 `trading_dates_set` 跳过 | `[MANUAL]` | `engine.py:221-225` |

## 成交规则

| # | Invariant | 验证 | 测试 |
|---|-----------|------|------|
| F-1 | BUY trade.pnl == -commission | `[AUTO]` | `test_backtest_core::test_buy_creates_trade_with_negative_pnl` |
| F-2 | SELL trade.pnl == realized_pnl - proportional_commission | `[AUTO]` | `test_bug_fixes::TestF2SellPnlDecomposition` |
| F-3 | `_execute_order` 返回约定：`List[Trade]`=成交, `[]`=永久丢弃, `None`=延迟重试 | `[AUTO]` | `test_bug_fixes::TestF10ExecuteOrderReturnConvention` |
| F-4 | CN 涨跌停订单返回 `None`（延迟重试），非 `[]` | `[AUTO]` | `test_bug_fixes::TestF3LimitHitReturnsNone` |
| F-5 | 资金不足返回 `[]` | `[AUTO]` | `test_backtest_core::test_insufficient_cash_rejects_buy` |
| F-6 | 无仓位 SELL 返回 `[]` | `[AUTO]` | `test_backtest_core::test_sell_without_position_is_noop` |
| F-7 | 开盘价 <= 0 返回 `[]` | `[AUTO]` | `test_bug_fixes::TestF6ZeroOpenPriceReturnsEmpty` |
| F-8 | 全部平仓后：final_nav = initial_cash + sum(trade.pnl) | `[AUTO]` | `test_bug_fixes::test_nav_equals_initial_cash_plus_total_pnl` |
| F-9 | 有持仓时：final_nav = initial_cash + sum(trade.pnl) + unrealized_pnl | `[AUTO]` | `test_bug_fixes::TestF7NavWithOpenPositions` |
| F-10 | 所有佣金 breakdown 值 >= 0（所有市场） | `[AUTO]` | `test_bug_fixes::test_commission_always_non_negative` (hypothesis) |
| F-11 | SELL 佣金 >= BUY 佣金（同一市场同一价格数量） | `[AUTO]` | `test_bug_fixes::test_sell_commission_ge_buy_commission_same_market` (hypothesis) |
| F-12 | 买入滑点加价，卖出减价（所有市场） | `[AUTO]` | `test_bug_fixes::TestF11SlippageDirectionAllMarkets` |

## CN A 股

| # | Invariant | 验证 | 测试 |
|---|-----------|------|------|
| CN-1 | T+1 结算：buy_date 的 lot 在 buy_date+1 才 settled，当日买入次日可卖 | `[AUTO]` | `test_backtest_core::test_settled_quantity_t1` + `test_bug_fixes::test_cn_sell_passes_when_buy_settles_by_fill_date` |
| CN-2 | 涨跌停 ±10/20/30% + IPO 5 天豁免 | `[AUTO]` | `test_cn_market::TestCNPriceLimit` |
| CN-3 | 红利税 ≤30d:20%, 31-365d:10%, >365d:0% | `[AUTO]` | `test_cn_market::TestCNDividendTax` |
| CN-4 | 手数取整：BUY 向下取整，<1手丢弃 | `[AUTO]` | `test_cn_market::TestCNLotSize` |
| CN-5 | SELL 允许碎股（不取整） | `[AUTO]` | `test_bug_fixes::TestCN5SellFractionalShares` |
| CN-6 | 成交量上限 ≤ 日成交量 × 5% | `[AUTO]` | `test_bug_fixes::TestCN7VolumeParticipationLimit` |
| CN-7 | 涨跌停对称性：+limit 和 -limit 边界精确 | `[AUTO]` | `test_bug_fixes::test_cn_limit_check_symmetry` (hypothesis) |

## HK 港股

| # | Invariant | 验证 | 测试 |
|---|-----------|------|------|
| HK-1 | 手数取整（可配置，默认 100） | `[AUTO]` | `test_hk_market::TestHKLotSize` |
| HK-2 | 印花税仅 SELL | `[AUTO]` | `test_hk_market::TestHKCommission` |
| HK-3 | T+0 可日内交易 | `[AUTO]` | `test_hk_market::TestHKT0DayTrading` |
| HK-4 | 无涨跌停限制 | `[AUTO]` | `test_hk_market::TestHKNoPriceLimit` |

## US 美股

| # | Invariant | 验证 | 测试 |
|---|-----------|------|------|
| US-1 | 无手数限制（可买 1 股） | `[AUTO]` | `test_us_market::TestUSNoLotSize` |
| US-2 | SEC fee + FINRA TAF 仅 SELL | `[AUTO]` | `test_us_market::TestUSCommission` |
| US-3 | T+0 可日内交易 | `[AUTO]` | `test_us_market::TestUST0DayTrading` |
| US-4 | 买入滑点加价，卖出滑点减价 | `[AUTO]` | `test_us_market::TestUSSlippage` |

## 风控

| # | Invariant | 验证 | 测试 |
|---|-----------|------|------|
| R-1 | price=None 时风控仍生效 | `[AUTO]` | `test_bug_fixes::test_sell_with_price_none_still_checks_t1_for_cn` |
| R-2 | price=0 时风控仍生效 | `[AUTO]` | `test_bug_fixes::TestR2PriceZeroRiskCheck` |
| R-3 | `_pending_order_values` 累计同日同标的 pending 金额 | `[AUTO]` | `test_bug_fixes::TestR3PendingOrderValuesAccumulate` |
| R-4 | CN T+1 预检使用 fill_date（as_of_date+1），非 submission_date | `[AUTO]` | `test_bug_fixes::test_cn_sell_passes_when_buy_settles_by_fill_date` |
| R-5 | `reset_daily()` 清零 `_daily_order_count` 和 `_pending_order_values` | `[AUTO]` | `test_bug_fixes::TestR5ResetDaily` |

## WalkForward

| # | Invariant | 验证 | 测试 |
|---|-----------|------|------|
| WF-1 | 训练/测试窗口基于交易日索引，不是日历天 | `[MANUAL]` | `walkforward.py:88-95` unique_dates 索引 |
| WF-2 | 无有效参数时（best_sharpe == -inf）跳过窗口 | `[AUTO]` | `test_bug_fixes::test_no_valid_params_returns_not_viable` |
| WF-3 | 空数据返回 `is_viable=False` | `[AUTO]` | `test_backtest_core::test_empty_data_returns_not_viable` |

## 诊断

| # | Invariant | 验证 | 测试 |
|---|-----------|------|------|
| D-1 | 超期订单有 `expired_orders` 计数 | `[AUTO]` | `test_bug_fixes::test_expired_order_counted_in_diagnostics` |
| D-2 | T+1 拒绝有 `t1_rejected_sells` 计数 | `[AUTO]` | `test_cn_market::TestCNT1Settlement` |
| D-3 | 涨跌停拒绝有 `limit_rejected_orders` 计数 | `[AUTO]` | `test_bug_fixes::test_cn_limit_up_returns_none_for_retry` |
| D-4 | 手数调整有 `lot_adjusted_trades` 计数 | `[MANUAL]` | `engine.py` lot_qty != int(quantity) 时 +1 |
| D-5 | 成交量限制有 `volume_limited_trades` 计数 | `[AUTO]` | `test_bug_fixes::TestCN7VolumeParticipationLimit` |
| D-6 | `cost_drag_pct` 在 \|gross_pnl\| < 1e-10 时返回 0 | `[AUTO]` | `test_bug_fixes::test_near_zero_gross_pnl_returns_zero` |
| D-7 | `cost_drag_pct` 始终有限且非负 | `[AUTO]` | `test_bug_fixes::test_cost_drag_finite_and_non_negative` (hypothesis) |
| D-8 | `avg_fill_delay_days` 在无成交时返回 0 | `[AUTO]` | `test_backtest_core::test_avg_fill_delay_zero_when_no_fills` |

## 模型不变量

| # | Invariant | 验证 | 测试 |
|---|-----------|------|------|
| M-1 | Position.settled_quantity 只算 lot_date < as_of 的 lot | `[AUTO]` | `test_backtest_core::test_settled_quantity_t1` |
| M-2 | Position.remove_sell_lots 按 FIFO 切片 | `[AUTO]` | `test_backtest_core::test_remove_sell_lots` |
| M-3 | Position 平仓后（is_flat）`_lots.clear()` | `[AUTO]` | `test_backtest_core::test_update_from_fill_sell_closes` |
| M-4 | Trade (frozen dataclass) 不可变 | `[MANUAL]` | `trade.py:6 @dataclass(frozen=True)` |
| M-5 | `_is_suspended`: volume=0 → True, open=0 且 close=0 → True | `[AUTO]` | `test_bug_fixes::TestBugFix5SuspendedCheck` |

## 已知限制

| # | 限制 | 影响 | 修复优先级 |
|---|------|------|-----------|
| K-1 | 多策略共享 portfolio 时，所有策略接收所有 fills，`_positions` 追踪的是组合仓位而非策略自身仓位 | 多策略回测时策略无法知道自己的独立持仓 | HIGH |
| K-2 | `WalkForward._find_best_params` 异常仅记录日志，未抛出 | 策略 bug 可能被隐藏 | LOW |
| K-3 | D-4 手数调整计数无专项测试 | 仅代码审查覆盖 | LOW |

---

## Agent 使用说明

修改回测引擎代码后：

1. 运行 `python -m pytest quant/tests/ -q` 确保全部测试通过
2. 对照本清单确认受影响的 Invariant
3. `[AUTO]` 标记的条目已由 pytest 覆盖，测试通过即代表 Invariant 成立
4. `[MANUAL]` 标记的条目需要人工审查对应代码路径
5. 新增功能 → 在对应分区新增 Invariant 行 + 测试
6. 修复新 Bug → 在 `test_bug_fixes.py` 新增回归测试 + 在本清单增加 Invariant

**统计**: `[AUTO]` 条目占比应持续提升。当前目标：> 85%。
