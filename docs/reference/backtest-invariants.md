# Backtest Engine Invariants

> 每次 Agent 修改回测系统代码后，逐条验证。所有条目必须 PASS。

## 执行顺序

| # | Invariant | 测试文件 | 状态 |
|---|-----------|----------|------|
| E-1 | 信号日 T 的订单在 T+1 以开盘价成交 | `test_backtest_core.py::TestBacktesterExecution` | ✅ |
| E-2 | `on_before_trading` 在 `on_data` 之前调用 | `engine.py:230-232` 日循环顺序 | ✅ |
| E-3 | `on_data` 在 `on_after_trading` 之前调用 | `engine.py:230-327` 日循环顺序 | ✅ |
| E-4 | 延迟订单填充在策略新订单收集之前（步骤③在④之前） | `engine.py:288-342` | ✅ |
| E-5 | 每日循环末尾调用 `portfolio.reset_daily()` 和 `risk_engine.reset_daily()` | `engine.py:350-351` | ✅ |
| E-6 | `current_date` 使用 `timedelta(days=1)` 步进，非交易日由 `trading_dates_set` 跳过 | `engine.py:221-225` | ✅ |

## 成交规则

| # | Invariant | 测试文件 | 状态 |
|---|-----------|----------|------|
| F-1 | BUY trade.pnl == -commission（不含持仓盈亏） | `test_backtest_core.py::test_buy_creates_trade_with_negative_pnl` | ✅ |
| F-2 | SELL trade.pnl == realized_pnl - proportional_commission | `engine.py:644` | ✅ |
| F-3 | CN 涨跌停订单返回 `None`（可重试），不是 `[]`（永久丢弃） | `engine.py:528-530` | ✅ |
| F-4 | 资金不足返回 `[]`，不扣佣金 | `engine.py:573-574` | ✅ |
| F-5 | 无仓位 SELL 返回 `[]` | `test_backtest_core.py::test_sell_without_position_is_noop` | ✅ |
| F-6 | 开盘价 <= 0 返回 `[]` | `engine.py:514-516` | ✅ |
| F-7 | NAV = initial_cash + sum(trade.pnl) + unrealized_pnl | `test_bug_fixes.py::test_nav_equals_initial_cash_plus_total_pnl` | ✅ |
| F-8 | 所有佣金 breakdown 值 >= 0 | `test_bug_fixes.py::test_commission_always_non_negative` | ✅ |
| F-9 | SELL 佣金 >= BUY 佣金（同一市场同一价格数量） | `test_bug_fixes.py::test_sell_commission_ge_buy_commission_same_market` | ✅ |

## CN A 股

| # | Invariant | 测试文件 | 状态 |
|---|-----------|----------|------|
| CN-1 | T+1 结算：buy_date 的 lot 在 buy_date+1 才 settled | `test_backtest_core.py::test_settled_quantity_t1` | ✅ |
| CN-2 | 涨跌停 ±10/20/30% + IPO 5 天豁免 | `test_cn_market.py::TestCNPriceLimit` | ✅ |
| CN-3 | 红利税 ≤30d:20%, 31-365d:10%, >365d:0% | `test_cn_market.py::TestCNDividendTax` | ✅ |
| CN-4 | 手数取整：BUY 向下取整，<1手丢弃 | `test_cn_market.py::TestCNLotSize` | ✅ |
| CN-5 | SELL 允许碎股（不取整） | `engine.py:554-556` | ✅ |
| CN-6 | 当日买入的股票可以在次日卖出（T+1 正确） | `test_bug_fixes.py::test_cn_sell_passes_when_buy_settles_by_fill_date` | ✅ |
| CN-7 | 成交量上限 ≤ 日成交量 × 5% | `engine.py:558-566` | ✅ |
| CN-8 | 涨跌停对称性：+limit 和 -limit 边界精确 | `test_bug_fixes.py::test_cn_limit_check_symmetry` | ✅ |

## HK 港股

| # | Invariant | 测试文件 | 状态 |
|---|-----------|----------|------|
| HK-1 | 手数取整（可配置，默认 100） | `test_hk_market.py::TestHKLotSize` | ✅ |
| HK-2 | 印花税仅 SELL | `test_hk_market.py::TestHKCommission` | ✅ |
| HK-3 | T+0 可日内交易 | `test_hk_market.py::TestHKT0DayTrading` | ✅ |
| HK-4 | 无涨跌停限制 | `test_hk_market.py::TestHKNoPriceLimit` | ✅ |

## US 美股

| # | Invariant | 测试文件 | 状态 |
|---|-----------|----------|------|
| US-1 | 无手数限制（可买 1 股） | `test_us_market.py::TestUSNoLotSize` | ✅ |
| US-2 | SEC fee + FINRA TAF 仅 SELL | `test_us_market.py::TestUSCommission` | ✅ |
| US-3 | T+0 可日内交易 | `test_us_market.py::TestUST0DayTrading` | ✅ |
| US-4 | 买入滑点加价，卖出滑点减价 | `test_us_market.py::TestUSSlippage` | ✅ |

## 风控

| # | Invariant | 测试文件 | 状态 |
|---|-----------|----------|------|
| R-1 | price=None 时风控仍生效（T+1、daily loss、leverage、order rate） | `test_bug_fixes.py::test_sell_with_price_none_still_checks_t1_for_cn` | ✅ |
| R-2 | price=0 时风控仍生效 | `engine.py:467` effective_price=0 | ✅ |
| R-3 | `_pending_order_values` 累计同日同标的 pending 金额 | `risk.py:225-226` | ✅ |
| R-4 | CN T+1 预检使用 fill_date（as_of_date+1），不是 submission_date | `test_bug_fixes.py::test_cn_sell_passes_when_buy_settles_by_fill_date` | ✅ |
| R-5 | `reset_daily()` 清零 `_daily_order_count` 和 `_pending_order_values` | `risk.py:228-230` | ✅ |

## WalkForward

| # | Invariant | 测试文件 | 状态 |
|---|-----------|----------|------|
| WF-1 | 训练/测试窗口基于交易日索引，不是日历天 | `walkforward.py:88-95` | ✅ |
| WF-2 | 异常记录日志，不静默吞掉 | `walkforward.py:_find_best_params except` | ✅ |
| WF-3 | 无有效参数时（best_sharpe == -inf）跳过窗口 | `walkforward.py:107-109` | ✅ |
| WF-4 | 空数据返回 `is_viable=False` | `test_backtest_core.py::test_empty_data_returns_not_viable` | ✅ |

## 诊断

| # | Invariant | 测试文件 | 状态 |
|---|-----------|----------|------|
| D-1 | 所有丢弃路径都有 diag 计数器 | `BacktestDiagnostics` 字段 | ✅ |
| D-2 | 超期订单有 `expired_orders` 计数 | `test_bug_fixes.py::test_expired_order_counted_in_diagnostics` | ✅ |
| D-3 | T+1 拒绝有 `t1_rejected_sells` 计数 | `test_cn_market.py::TestCNT1Settlement` | ✅ |
| D-4 | 涨跌停拒绝有 `limit_rejected_orders` 计数 | `engine.py:529` | ✅ |
| D-5 | 手数调整有 `lot_adjusted_trades` 计数 | `engine.py:552` | ✅ |
| D-6 | 成交量限制有 `volume_limited_trades` 计数 | `engine.py:566` | ✅ |
| D-7 | `cost_drag_pct` 在 \|gross_pnl\| < 1e-10 时返回 0 | `test_bug_fixes.py::test_near_zero_gross_pnl_returns_zero` | ✅ |
| D-8 | `cost_drag_pct` 始终有限且非负 | `test_bug_fixes.py::test_cost_drag_finite_and_non_negative` | ✅ |
| D-9 | `avg_fill_delay_days` 在无成交时返回 0 | `test_backtest_core.py::test_avg_fill_delay_zero_when_no_fills` | ✅ |

## 模型不变量

| # | Invariant | 测试文件 | 状态 |
|---|-----------|----------|------|
| M-1 | Position.settled_quantity 只算 lot_date < as_of 的 lot | `test_backtest_core.py::test_settled_quantity_t1` | ✅ |
| M-2 | Position.remove_sell_lots 按 FIFO 切片 | `test_backtest_core.py::test_remove_sell_lots` | ✅ |
| M-3 | Position 平仓后 `_lots.clear()` | `position.py:70-71` | ✅ |
| M-4 | Trade(frozen dataclass) 不可变 | `trade.py:6 @dataclass(frozen=True)` | ✅ |
| M-5 | `_is_suspended` 返回 bool | `test_bug_fixes.py::test_is_suspended_returns_bool` | ✅ |
| M-6 | `_is_suspended`: volume=0 → True, open=0 且 close=0 → True | `test_bug_fixes.py::TestBugFix5SuspendedCheck` | ✅ |

---

## Agent 使用说明

修改回测引擎代码后：

1. 运行 `python -m pytest quant/tests/ -q` 确保 217 tests 全部通过
2. 对照本清单逐条确认受影响的 Invariant
3. 如新增功能，在对应分区新增 Invariant 行
4. 如修复新 Bug，在 `test_bug_fixes.py` 新增回归测试，并在本清单增加 Invariant
