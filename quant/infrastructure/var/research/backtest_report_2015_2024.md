# A-Share Strategy Backtest Report

**Period:** 2015-01-01 to 2024-12-31 (10 years)  
**Initial Capital:** 1,000,000 CNY  
**Market:** A-Shares (SZSE 000xxx stocks + CN indexes)  
**Slippage:** 5 bps | Commission: CN Realistic | Execution: T+1  
**Date:** 2026-04-25  

---

## Summary Table

| Strategy | Return% | Sharpe | Sortino | MaxDD% | Win Rate | Trades | Profit Factor |
|----------|--------:|-------:|--------:|-------:|---------:|-------:|--------------:|
| RegimeFilteredMomentum | **+2.97** | **0.28** | **0.42** | -3.73 | **64.3%** | 14 | **2.63** |
| DailyReturnAnomaly | +0.34 | 0.08 | 0.12 | -1.23 | 53.3% | 15 | 1.29 |
| VolatilityScaledTrend | -1.54 | -0.09 | -0.11 | -6.24 | 66.7% | 3 | 0.34 |

---

## Strategy Details

### 1. RegimeFilteredMomentum (Best)

**Source:** arXiv:2604.18821 (Liu, Apr 2026) — factor strategy deterioration after extreme runs.

**Logic:**
- Cross-sectional 20-day momentum ranking across 15 SZSE stocks
- Regime detection via realized volatility of CSI 300 index (000300):
  - Low vol (<15% ann.) → full exposure (100%)
  - Medium vol (15-25%) → reduced exposure (40-80%)
  - High vol (>25%) → minimal exposure (20%)
- Long top 20% momentum stocks with regime-adjusted position size
- Monthly rebalance (21 trading days)

**Parameters:** momentum_lookback=20, vol_lookback=20, vol_low=0.15, vol_high=0.25, holding_days=21, top_pct=0.2, max_position_pct=0.10

**Symbols:** 000333, 000651, 000858, 000001, 000568, 000725, 000625, 000878, 000709, 000876, 000538, 000786, 000063, 000938, 000825  
**Benchmark:** 000300 (CSI 300 Index)

**Results:**
- Final NAV: 1,029,693 CNY
- Total Return: +2.97%
- Sharpe Ratio: 0.28
- Max Drawdown: -3.73%
- Win Rate: 64.3%
- Profit Factor: 2.63
- Total Commission: 591 CNY, Cost Drag: 2.0%

**Assessment:** Best risk-adjusted returns. The regime filter successfully reduced exposure during volatile periods (2015 crash, 2018 bear market, 2022 COVID). Low drawdown relative to return. Trade count is low (14 round-trips) due to conservative position sizing (10% max per stock).

---

### 2. DailyReturnAnomaly

**Source:** Cakici et al. (2026) "A Unified Framework for Anomalies based on Daily Returns", Alpha Architect.

**Logic:**
- Count consecutive positive/negative return days (streak detection)
- Short-term signal: follow the streak (momentum continuation)
- Medium-term signal: bet on reversal after extended moves (>15% in 20 days)
- Composite score = short_signal * 0.6 + medium_signal * 0.4
- Weekly rebalance (5 trading days), long top 20%

**Parameters:** streak_short=3, streak_long=8, short_weight=0.6, holding_days=5, top_pct=0.2, max_position_pct=0.10

**Symbols:** 000333, 000651, 000858, 000568, 000001, 000538, 000895, 000725, 000625, 000878, 000709, 000876, 000786, 000063, 000938

**Results:**
- Final NAV: 1,003,404 CNY
- Total Return: +0.34%
- Sharpe Ratio: 0.08
- Max Drawdown: -1.23%
- Win Rate: 53.3%
- Profit Factor: 1.29
- Total Commission: 844 CNY, Cost Drag: 19.9%

**Assessment:** Marginal positive return with very low drawdown. The strategy's strength is capital preservation, not return generation. The 20% cost drag relative to gross return is concerning — reducing rebalance frequency could improve net returns. The behavioral anomaly signal is weaker in the SZSE small/mid-cap universe than expected.

---

### 3. VolatilityScaledTrend

**Source:** Alpha Architect DIY Trend-Following (2025), Quantpedia Tactical Allocation.

**Logic:**
- For each index: trend signal = close > SMA(50)
- Volatility scaling: weight = target_vol(15%) / realized_vol, capped at 25%
- Go long bullish assets with inverse-vol weighted allocation
- Monthly rebalance (21 trading days)

**Parameters:** sma_lookback=50, vol_lookback=20, target_vol=0.15, max_weight=0.25, holding_days=21

**Symbols:** 000300 (CSI 300), 000905 (CSI 500), 399006 (GEM Index)

**Results:**
- Final NAV: 984,574 CNY
- Total Return: -1.54%
- Sharpe Ratio: -0.09
- Max Drawdown: -6.24%
- Win Rate: 66.7%
- Profit Factor: 0.34
- Only 3 round-trip trades in 10 years

**Assessment:** Negative return. The strategy barely traded because indexes spent long periods below their 50-day SMA during A-share bear markets (2015-2016, 2018, 2022-2023). The trend-following approach works poorly on Chinese indexes due to mean-reverting regime characteristics. Consider using individual stocks instead of indexes, or shorter lookback periods.

---

## Key Observations

1. **Regime filtering adds value** — RegimeFilteredMomentum's volatility-based exposure reduction was the key differentiator
2. **Low trade counts** — All strategies showed surprisingly few round-trip trades (3-15 over 10 years). This is partly due to:
   - Conservative position sizing (10% max per stock)
   - The backtester counting completed round-trips (buy → sell) as one trade
   - Strategies holding positions for extended periods during trending markets
3. **A-Share market structure** — The 2015-2024 period included multiple regime transitions (bull→bear→sideways), which favors regime-aware strategies
4. **Commission drag** — DailyReturnAnomaly's weekly rebalance generated 20% cost drag vs gross returns. Monthly or quarterly rebalance would be more efficient

## Recommendations

1. **Deploy RegimeFilteredMomentum** for live/paper trading — best Sharpe (0.28), lowest risk-adjusted drawdown, highest profit factor
2. **Optimize DailyReturnAnomaly** — increase holding_days from 5 to 21 to reduce turnover costs
3. **Revise VolatilityScaledTrend** — replace indexes with liquid ETFs or individual stocks; consider shorter SMA (20-30 days) for faster trend detection
4. **Data expansion** — ingest 600xxx SH stocks (600519 Moutai, 601318 Ping An, etc.) for a broader cross-sectional universe
5. **Walk-forward validation** — run 6-month train / 1-month test walk-forward to confirm out-of-sample robustness

---

## Files

| File | Description |
|------|-------------|
| `research/RegimeFilteredMomentum_2015-01-01_2024-12-31_metrics.json` | Full metrics |
| `research/DailyReturnAnomaly_2015-01-01_2024-12-31_metrics.json` | Full metrics |
| `research/VolatilityScaledTrend_2015-01-01_2024-12-31_metrics.json` | Full metrics |
| `research/*_trades.csv` | Trade-level details |
| `research/*_equity.csv` | Equity curve |
| `research/discovered_strategies.md` | Phase 1 discovery results |
| `research/strategy_evaluation.md` | Phase 2 evaluation scores |
