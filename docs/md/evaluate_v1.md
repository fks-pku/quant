# Quant System Evaluation Report v1

**Date:** 2026-04-15  
**Current Standing: 3/10** — "Well-Architected Prototype, Not Yet a Trading System"

To beat 90% of personal quant systems, you need to be in the top decile. Honestly, most personal quant systems are garbage — so the bar is higher than it sounds. Here's a brutal, honest evaluation.

---

## 1. CRITICAL BUGS — System Cannot Actually Trade

These are **system-breaking**. No amount of alpha research matters if the engine doesn't work.

| # | Issue | File | Severity |
|---|-------|------|----------|
| 1 | **`OrderManager` is never set on `Context`** — strategy `buy()`/`sell()` silently fail | `engine.py`, `strategies/base.py` | **FATAL** |
| 2 | **Engine never calls strategy `execute()`** — VolatilityRegime and SimpleMomentum's trading logic is unreachable | `engine.py` | **FATAL** |
| 3 | **API server generates fake random data** — dashboard shows fabricated PnL, trades, and equity curves | `api_server.py:315` | **FATAL** |
| 4 | **Backtester P&L is inverted for closing sells** — `if SELL: pnl = -pnl` negates correct calculation | `backtester.py:236-251` | **FATAL** |
| 5 | **Commission calc is wrong** — multiplies `price * qty * per_share` instead of just `qty * per_share` | `backtester.py:270` | **HIGH** |
| 6 | **Engine fires `MARKET_OPEN` every 60 seconds** during market hours instead of once | `engine.py` | **HIGH** |
| 7 | **Two disconnected strategy systems** — `config.yaml` lists example strategies; `strategies.yaml` has real ones. Neither loads the other | `quant_system.py` | **HIGH** |
| 8 | **Kelly sizing is hardcoded** — `calculate_kelly(0.55, 1.0, 1.0)` ignores actual strategy stats | `framework.py:266` | **HIGH** |

---

## 2. EVALUATION BY DIMENSION

### Architecture Design: 7/10
The modular design is actually good. EventBus, pluggable providers/brokers, factor library, walk-forward framework — these are the right abstractions. The PRD is excellent (8/10). But the implementation is disconnected — like having a great blueprint but the plumber never connected the pipes.

### Backtesting Rigor: 3/10
You have the *skeleton* of professional backtesting (walk-forward, slippage, commission), but:
- Walk-forward `aggregate_max_dd` is a **made-up heuristic** (`test_return * 0.15`), not actual drawdown
- Walk-forward selects params by **train Sharpe**, not out-of-sample — this is data-snooping
- No regime coverage testing (does your backtest span 2008, 2020, 2022?)
- No multiple hypothesis testing correction
- No parameter sensitivity analysis
- P&L calculation is **wrong** — backtests produce meaningless numbers

### Alpha Signal Quality: 2/10
Your strategies are educational toy examples:
- **MomentumEOD**: buys yesterday's top gainers — this is well-known to have crashed post-2010
- **MeanReversion1m**: basic RSI with no edge validation
- **VolatilityRegime**: the logic is reasonable (regime switching on VIX) but it **never executes trades** due to the engine bug
- No IC (Information Coefficient) testing anywhere
- No factor exposure decomposition — you don't know if your "alpha" is just beta
- No signal decay analysis
- No out-of-sample validation on any signal

### Risk Management: 4/10
- Position limits exist (5% per symbol, 25% per sector, 2% daily loss) — this is good
- But there's **no kill switch** — when daily loss is breached, orders are rejected but positions aren't closed
- No correlation-based portfolio risk (only sector exposure)
- No tail risk management
- No regime-adaptive risk (same limits in bull and bear markets)
- Margin hardcodes 50% regardless of asset

### Data Pipeline: 4/10
- Three providers (Yahoo, Alpha Vantage, Futu) is good for redundancy
- But Yahoo data has **no survivorship bias correction** — testing on current S&P 500 members adds ~2% annual phantom returns
- No point-in-time constituent data
- No data quality validation (spikes, gaps, stale prices)
- Storage has no cleanup mechanism — SQLite grows unbounded

### Execution: 3/10
- Paper broker has hardcoded base prices and no commission model
- Futu broker (746 lines) is the most complete component but has parameter issues
- No short selling support in paper broker
- Fill handler treats all sells as full position closes
- No smart order routing or execution optimization

### Testing: 2/10
- 5 test functions total, all happy-path unit tests
- **Zero tests** for: Backtester, WalkForward, RiskEngine, OrderManager, FillHandler, any strategy, Storage, FutuBroker
- No integration tests — the system has never been tested end-to-end

---

## 3. IMPROVEMENT ROADMAP — From 3/10 to 9/10

### Phase 1: Fix the Foundation (Week 1-2) — Get to 5/10

**Priority: System must actually work before anything else.**

1. **Fix OrderManager wiring** — Set `order_manager` on `Context` so `buy()`/`sell()` work
2. **Fix engine-strategy integration** — Engine must call `execute()` or strategies must use `on_data()` consistently
3. **Fix backtester P&L** — Correct the sell-side calculation, fix commission model
4. **Fix walk-forward** — Use actual max drawdown (not heuristic), select params by OOS Sharpe
5. **Connect strategies.yaml to quant_system.py** — Load real strategies, not just examples
6. **Remove fake data from API server** — Either show real data or show "no data"
7. **Add end-to-end integration test** — One test: load config → create strategy → run backtest → verify results

### Phase 2: Professional Backtesting (Week 3-4) — Get to 7/10

This is where you separate from 90% of retail quants.

8. **Implement IC framework** — For every signal, calculate Information Coefficient, IR, t-stat. Minimum bar: IC > 0.02, t-stat > 2
9. **Add regime coverage check** — Backtests MUST span 2008 crisis, 2020 COVID, 2022 bear market. If your data doesn't cover these, you're flying blind
10. **Parameter sensitivity analysis** — Test each parameter ±20%. If Sharpe changes >20%, you're overfit
11. **Factor decomposition** — Regress strategy returns against Fama-French factors (MKT, SMB, HML, UMD). If your "alpha" disappears after factor adjustment, you don't have alpha
12. **Multiple hypothesis correction** — Track every strategy/parameter you test. Apply Bonferroni or BH correction. If you test 50 variations, your 0.01 p-value is NOT significant
13. **Data quality pipeline** — Audit for extreme returns, zero prices, stale data, missing dates
14. **Proper transaction cost model** — Use square-root market impact model. Include commission + spread + impact. For US equity, minimum 3-5 bps per round trip

### Phase 3: Real Alpha Research (Week 5-8) — Get to 8/10

15. **Replace toy strategies with research-grade signals:**
    - Earnings momentum (standardized unexpected earnings)
    - Short interest signals
    - Option-implied volatility spreads
    - Cross-sectional mean reversion (5-day)
    - Intraday volume patterns
16. **Signal combination framework** — Use IC-weighted combination of uncorrelated signals
17. **Regime-aware allocation** — Detect market state (HMM or simple SMA rules) and shift strategy weights
18. **Walk-forward production** — Every strategy that goes live must have passed walk-forward with <50% Sharpe degradation

### Phase 4: Institutional-Grade Risk & Execution (Week 9-12) — Get to 9/10

19. **Risk parity position sizing** — Weight positions by inverse volatility, not equal weight
20. **Drawdown kill switch** — Auto-close all positions when daily loss exceeds threshold
21. **Correlation monitoring** — Track rolling correlation between positions; reduce exposure when correlations spike
22. **Execution optimization** — Use TWAP/VWAP for large orders, limit orders to reduce costs
23. **Live monitoring & alerting** — Real-time P&L tracking with deviation alerts (live vs. expected)
24. **Out-of-sample tracking** — Compare live performance to backtest expectations; flag degradation early

---

## 4. HONEST TRUTH

Your system has **good bones** but is currently in a state where the engine doesn't trade, the backtests produce wrong numbers, and the dashboard shows fabricated data. This is normal for an early-stage system — but it means you're at **month 1 of a 6-month journey** to something competitive.

The **single most impactful thing** you can do is fix the foundation (Phase 1) and then focus relentlessly on backtesting rigor (Phase 2). Most retail quants fail because their backtests lie to them — they optimize on noise and deploy overfit strategies. If your backtesting is honest, you'll already be ahead of 70% of personal quant systems.

To beat 90%, you need:
1. A working system (you don't have this yet)
2. Honest backtesting with walk-forward validation
3. At least 2-3 signals with genuine IC > 0.03 that survive factor decomposition
4. Robust risk management that prevents blowups

The documentation and architecture vision are genuinely good. The gap is purely in execution and integration. Fix the bugs, wire up the components, validate with rigor, and you'll have something real.

---

## 5. SCORECARD SUMMARY

| Dimension | Score | Target (90th percentile) | Gap |
|-----------|-------|--------------------------|-----|
| Architecture | 7/10 | 8/10 | Small — fix integrations |
| Backtesting | 3/10 | 8/10 | Large — needs complete overhaul |
| Alpha Signals | 2/10 | 7/10 | Large — toy → research grade |
| Risk Management | 4/10 | 8/10 | Medium — needs kill switch, correlation risk |
| Data Pipeline | 4/10 | 7/10 | Medium — survivorship bias, data quality |
| Execution | 3/10 | 7/10 | Large — paper broker broken, no optimization |
| Testing | 2/10 | 8/10 | Large — near zero coverage |
| Security | 2/10 | 7/10 | Large — no auth, exposed credentials |
| Documentation | 8/10 | 7/10 | **Ahead** — excellent PRD |
| Frontend | 6/10 | 6/10 | OK — shows fake data though |
| **Overall** | **3/10** | **7.5/10** | **4.5 points to close** |

---

*Generated by quantitative-research skill evaluation. Re-evaluate after each phase completion.*
