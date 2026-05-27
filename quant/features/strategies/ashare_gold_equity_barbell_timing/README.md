# A-share Gold-Equity ETF Barbell Timing

Active candidate daily ETF timing strategy.

Full report: `full_research_report.html`

The strategy avoids single-stock and small-cap exposure. It uses the CSI 300 index as the market temperature signal and trades only a user-audited stable ETF registry: `510050`, `510300`, `159915`, `159949`, `510880`, and `518880`. New ETF categories must be explicitly reviewed and added to the registry before any strategy can use them.

Daily process:

1. Update bars for the audited representative equity ETF categories and gold ETF.
2. Every 20 trading days, classify market state from CSI 300 index (`000300`): close above 120-day moving average and 63-day momentum above zero means risk-on.
3. At each rebalance date, keep only registered ETFs with current bars, fund NAV/size fields, sufficient liquidity, and enough lookback history.
4. In risk-on state, rank the registered equity representatives by 63-day momentum divided by 20-day annualized volatility.
5. Allocate half of the configured exposure to the best registered equity ETF and half to the registered gold ETF.
6. In risk-off state, allocate the configured exposure to gold ETF only; if data is missing, stay in cash.

Risk-exit package:

- `risk_exit.enabled=true` by default; disabled risk-exit runs are treated as optional sensitivity/ablation research and are not shown in the default full report.
- `stop_loss_pct=0.08` exits an ETF leg whose current price falls 8% below the effective entry cost.
- `take_profit_pct=0.16` arms a trailing exit; `trailing_stop_pct=0.06` protects ETF-leg gains after that threshold is reached.
- `max_holding_days=60` exits stale ETF legs when return is below `min_time_stop_return=0.0`.
- Risk exits run daily before the rebalance gate and same-day rebalance will not immediately buy back a leg that just exited.

The universe is intentionally not a full-market ETF category universe. It is a pre-registered representative pool that reduces implicit category look-ahead risk at the cost of manual representative-pool selection bias.
