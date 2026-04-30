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
| E-2 | `on_before_trading` → `on_data` → `on_after_trading` 顺序不可变 | `[AUTO]` | `test_bug_fixes::TestE2CallbackOrder` |
| E-3 | 延迟订单填充在策略新订单收集之前 | `[AUTO]` | `test_bug_fixes::TestE3DeferredFillBeforeNewSignal` |
| E-4 | 每日循环末尾调用 `portfolio.reset_daily()` 和 `risk_engine.reset_daily()` | `[AUTO]` | `test_bug_fixes::TestE4ResetDailyCalled` |
| E-5 | `current_date` 步进后非交易日由 `trading_dates_set` 跳过 | `[AUTO]` | `test_bug_fixes::TestE5NonTradingDaysSkipped` |
| E-6 | CN 涨跌停检测使用昨日收盘价（非当日 bar），`prev_close_bars` 在 Step 2 覆盖前快照 | `[AUTO]` | `test_bug_fixes::test_prev_close_used_for_limit_check_not_today_bar` |
| E-7 | `close=0` 不更新 `last_prices`（防止市价归零导致 NAV 崩溃） | `[AUTO]` | `test_bug_fixes::test_zero_close_does_not_update_last_prices` |

## 成交规则

| # | Invariant | 验证 | 测试 |
|---|-----------|------|------|
| F-1 | BUY trade.pnl == -commission | `[AUTO]` | `test_backtest_core::test_buy_creates_trade_with_negative_pnl` |
| F-2 | SELL trade.pnl == realized_pnl - proportional_commission | `[AUTO]` | `test_bug_fixes::TestF2SellPnlDecomposition` |
| F-3 | `_execute_order` 返回约定：`List[Trade]`=成交, `[]`=永久丢弃（所有不可成交原因统一返回空列表） | `[AUTO]` | `test_bug_fixes::TestF3LimitHitReturnsEmpty` |
| F-4 | CN 涨跌停订单返回 `[]`（丢弃），不重试，策略在 `on_after_trading` 中基于最新数据重新判断 | `[AUTO]` | `test_bug_fixes::TestF3LimitHitReturnsEmpty` |
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
| R-5 | `reset_daily()` 清零 `_daily_order_count`、`_risk_rejected_count` 和 `_pending_order_values` | `[AUTO]` | `test_bug_fixes::TestR5ResetDaily` |
| R-6 | `risk_skipped_orders` 累计风控拒绝的订单数（含 price=None 场景） | `[AUTO]` | `test_bug_fixes::test_risk_skipped_orders_increments_on_position_limit` |

## WalkForward

| # | Invariant | 验证 | 测试 |
|---|-----------|------|------|
| WF-1 | 训练/测试窗口基于交易日索引，不是日历天 | `[AUTO]` | `test_bug_fixes::TestWF1TradingDayIndices` |
| WF-2 | 无有效参数时（best_sharpe == -inf）跳过窗口 | `[AUTO]` | `test_bug_fixes::test_no_valid_params_returns_not_viable` |
| WF-3 | 空数据返回 `is_viable=False` | `[AUTO]` | `test_backtest_core::test_empty_data_returns_not_viable` |

## 诊断

| # | Invariant | 验证 | 测试 |
|---|-----------|------|------|
| D-1 | 无法成交的延迟订单计入 `discarded_orders`（首次失败即丢弃，不重试） | `[AUTO]` | `test_bug_fixes::TestBugFix3DiscardedOrdersDiagnostic` |
| D-2 | T+1 拒绝有 `t1_rejected_sells` 计数 | `[AUTO]` | `test_cn_market::TestCNT1Settlement` |
| D-3 | 涨跌停拒绝有 `limit_rejected_orders` 计数 | `[AUTO]` | `test_bug_fixes::test_cn_limit_up_returns_none_for_retry` |
| D-4 | 手数调整有 `lot_adjusted_trades` 计数 | `[AUTO]` | `test_bug_fixes::TestD4LotAdjustedTrades` |
| D-5 | 成交量限制有 `volume_limited_trades` 计数 | `[AUTO]` | `test_bug_fixes::TestCN7VolumeParticipationLimit` |
| D-6 | `cost_drag_pct` 在 \|gross_pnl\| < 1e-10 时返回 0 | `[AUTO]` | `test_bug_fixes::test_near_zero_gross_pnl_returns_zero` |
| D-7 | `cost_drag_pct` 始终有限且非负 | `[AUTO]` | `test_bug_fixes::test_cost_drag_finite_and_non_negative` (hypothesis) |
| D-8 | `total_gross_pnl = sum(trade.pnl) + diag.total_commission`（毛利润还原公式） | `[AUTO]` | `test_bug_fixes::TestD9TotalGrossPnl` |
| D-9 | `fill_count` 仅在成交成功后递增（资金/仓位不足不计数） | `[AUTO]` | `test_bug_fixes::test_fill_count_only_increments_on_successful_fill` |

## 模型不变量

| # | Invariant | 验证 | 测试 |
|---|-----------|------|------|
| M-1 | Position.settled_quantity 只算 lot_date < as_of 的 lot | `[AUTO]` | `test_backtest_core::test_settled_quantity_t1` |
| M-2 | Position.remove_sell_lots 按 FIFO 切片 | `[AUTO]` | `test_backtest_core::test_remove_sell_lots` |
| M-3 | Position 平仓后（is_flat）`_lots.clear()` | `[AUTO]` | `test_backtest_core::test_update_from_fill_sell_closes` |
| M-4 | Trade (frozen dataclass) 不可变 | `[AUTO]` | `test_bug_fixes::TestM4TradeFrozen` |
| M-5 | `_is_suspended`: volume=0 → True, open=0 且 close=0 → True | `[AUTO]` | `test_bug_fixes::TestBugFix5SuspendedCheck` |

## 成交内部步骤

| # | Invariant | 验证 | 测试 |
|---|-----------|------|------|
| X-1 | `_execute_order` 内部顺序：开盘价检查 → 涨跌停 → 滑点 → 手数 → 成交量 → 佣金 → 资金/仓位 → 确认成交 | `[AUTO]` | `test_bug_fixes::TestX1ExecutionStepOrder` |
| X-2 | 滑点在涨跌停检查之后应用（涨跌停不触发滑点） | `[AUTO]` | `test_bug_fixes::TestX1ExecutionStepOrder::test_slippage_applied_after_limit_check` |
| X-3 | 手数取整在滑点之后、成交量上限之前 | `[AUTO]` | `test_bug_fixes::TestX1ExecutionStepOrder::test_lot_rounding_after_slippage_cn` |
| X-4 | 佣金仅在确认成交后计入 `diag.total_commission`（不扣后退） | `[AUTO]` | `test_bug_fixes::TestD9TotalGrossPnl`（公式正确隐含佣金正确计入） |

## 除权除息

| # | Invariant | 验证 | 测试 |
|---|-----------|------|------|
| DIV-1 | 现金红利：`portfolio.cash += cash_div × quantity - tax` | `[AUTO]` | `test_bug_fixes::TestDIV1CashDividendProcessing` |
| DIV-2 | CN 红利税按 lot 持仓天数分层（≤30d/31-365d/>365d） | `[AUTO]` | `test_cn_market::TestCNDividendTax` |
| DIV-3 | 送股：`pos.adjust_lots_for_stock_dividend(ratio)` 按比例调整已有 lot 的 qty×(1+ratio) / price÷(1+ratio)，不新增 lot | `[AUTO]` | `test_backtest_core::TestStockDividendLotTracking::test_stock_dividend_adjusts_lots_proportionally` |
| DIV-4 | 除权除息在延迟订单填充之前处理 | `[AUTO]` | `test_bug_fixes::TestDIV4DividendsBeforeFills` |

## 市场收盘

| # | Invariant | 验证 | 测试 |
|---|-----------|------|------|
| MTM-1 | 每日循环末尾 `_update_portfolio_prices` 用 `last_prices` 更新所有持仓市价 | `[AUTO]` | `test_bug_fixes::TestMTM1PortfolioPricesUpdated` |
| MTM-2 | NAV 在市价更新后记录（`equity_curve.append(nav)` 在 `_update_portfolio_prices` 之后） | `[AUTO]` | `test_bug_fixes::TestMTM1PortfolioPricesUpdated`（market_value 与 close 对齐即证明） |
| MTM-3 | `entry_times`/`entry_prices` 在全仓平仓后清理（`pos.quantity <= 0`） | `[AUTO]` | `test_bug_fixes::TestMTM3EntryTimesCleanup` |

## 开放持仓报告

| # | Invariant | 验证 | 测试 |
|---|-----------|------|------|
| OP-1 | `open_positions` 含 symbol/quantity/entry_price/current_price/unrealized_pnl/market_value | `[AUTO]` | `test_bug_fixes::TestF7NavWithOpenPositions` |
| OP-2 | `open_positions` 优先使用 `_earliest_lot_time(pos)` 而非 `entry_times` | `[AUTO]` | `test_bug_fixes::TestOP2EarliestLotTimePreference` |

## 延迟订单边界

| # | Invariant | 验证 | 测试 |
|---|-----------|------|------|
| DEF-1 | 延迟订单在 T+1 首次尝试执行，失败即永久丢弃（计入 `discarded_orders`），不重试 | `[AUTO]` | `test_bug_fixes::TestBugFix3DiscardedOrdersDiagnostic` |
| DEF-2 | 首日 CN 标的无 prev_bar 时跳过涨跌停检查（prev_close 不可用） | `[AUTO]` | `test_bug_fixes::TestDEF2FirstDayNoPrevBar` |

## 佣金率精确性

| # | Invariant | 验证 | 测试 |
|---|-----------|------|------|
| COM-1 | US per_share $0.005/股 最低 $1.0/单 | `[AUTO]` | `test_us_market::TestUSCommission` |
| COM-2 | CN 佣金 0.025% 最低 ¥5 + 印花税（仅 SELL）0.05% + 过户费 0.001% + 规管费 0.002% | `[AUTO]` | `test_cn_market::TestCNCommission` |
| COM-3 | HK 佣金 0.03% 最低 HK$3 + 印花税（仅 SELL）0.1%（`HK_STAMP_DUTY_RATE=0.001`） + SFC levy + 清算费 + 交易费 + 系统费 HK$0.50 | `[AUTO]` | `test_hk_market::TestHKCommission` |

## 现金守恒

| # | Invariant | 验证 |
|---|-----------|------|
| CASH-1 | `master.cash >= 0` 贯穿整个回测周期（SubPortfolio 模式下也不可为负） | `[MANUAL]` |
| CASH-2 | `Σ(sub.allocated_capital) <= initial_cash` — 创建 SubPortfolio 时校验，防止超额分配 | `[MANUAL]` |
| CASH-3 | `NAV_total = master.cash + Σ(sub.position.market_value)` 每日守恒（仅在佣金扣除、价格变动、分红时改变） | `[MANUAL]` |

## FIFO 持仓队列

| # | Invariant | 验证 |
|---|-----------|------|
| FIFO-1 | `Σ(lot.qty) == pos.quantity` — 所有活跃持仓的 lot 数量之和等于持仓总量（非仅平仓场景） | `[MANUAL]` |
| FIFO-2 | `pos.avg_cost == Σ(lot.qty × lot.price) / Σ(lot.qty)` — 平均成本由 FIFO lots 加权推导，计算路径唯一 | `[MANUAL]` |
| FIFO-3 | BUY 后 `lot.price` 应含佣金/share（`fill_price + commission / quantity`），保证成本基准完整 | `[MANUAL]` |

## 持仓账簿平衡

| # | Invariant | 验证 |
|---|-----------|------|
| ACCT-1 | BUY: `Δportfolio.cash + Δ(pos.cost_basis) = -(commission + slippage_cost)` — 现金流出 = 持仓成本增加 + 费用 | `[MANUAL]` |
| ACCT-2 | SELL: `pos.realized_pnl == fills_proceeds - cost_basis_removed - commission` — 已实现盈亏与现金变动一致 | `[MANUAL]` |

## SubPortfolio 隔离

| # | Invariant | 验证 |
|---|-----------|------|
| SUB-1 | `sub.cash >= 0` 始终成立（cash setter 有 clamp） | `[MANUAL]` |
| SUB-2 | `Δsub.cash == Δmaster.cash` — 每次 sub.cash 变动通过 setter 同步到 master | `[MANUAL]` |
| SUB-3 | sub 买入 `total_cost`（含佣金）不能超过 `sub.cash` | `[MANUAL]` |
| SUB-4 | sub 买入 `total_cost`（含佣金）不能超过 `master.cash`（防止共享池超支） | `[MANUAL]` |
| SUB-5 | `open_positions[].entry_price == pos.avg_cost`（含佣金后的加权成本） | `[MANUAL]` |

## 订单收集

| # | Invariant | 验证 |
|---|-----------|------|
| ORD-1 | pending orders 收集不依赖 `data_provider` 是否为 truthy — 即使 `data_provider=None` 也不丢失已提交订单 | `[MANUAL]` |

## 已知限制

| # | 限制 | 影响 | 修复优先级 |
|---|------|------|-----------|
| K-1 | ~~多策略共享 portfolio~~ 已通过 `strategy_allocations` + SubPortfolio 解决 | 已修复 | ~~HIGH~~ CLOSED |
| K-2 | `WalkForward._find_best_params` 异常仅记录日志，未抛出 | 策略 bug 可能被隐藏 | LOW |
| K-3 | CN IPO 5 天豁免使用日历天（`(current_date - ipo_d).days`），非交易日 | 5 个日历天可能仅含 3-4 个交易日 | LOW |
| K-4 | `_is_suspended` 将 `volume=0` 判定为停牌，低流动性标的零成交日可能误判 | 有效订单被丢弃 | LOW |

---

## Agent 使用说明

修改回测引擎代码后：

1. 运行 `python -m pytest quant/tests/ -q` 确保全部测试通过
2. 对照本清单确认受影响的 Invariant
3. `[AUTO]` 标记的条目已由 pytest 覆盖，测试通过即代表 Invariant 成立
4. `[MANUAL]` 标记的条目需要人工审查对应代码路径
5. 新增功能 → 在对应分区新增 Invariant 行 + 测试
6. 修复新 Bug → 在 `test_bug_fixes.py` 新增回归测试 + 在本清单增加 Invariant
7. `[MANUAL]` 条目补齐测试后升级为 `[AUTO]`

**统计**: 73 `[AUTO]` + 14 `[MANUAL]` = **87** 条不变量。`[MANUAL]` 条目标记了当前无自动化测试覆盖的关键约束，应逐步补齐测试后升级为 `[AUTO]`。
