# System Architecture Upgrade: CIO Module + Strategy Pool + Frontend Enhancement

**Date:** 2026-04-17
**Status:** Approved

## Summary

Upgrade the quant trading system with three capabilities:
1. **CIO Module** — Automated market environment assessment and strategy weight allocation (quantitative indicators + LLM news analysis)
2. **Strategy Pool Modularization** — Restructure strategies into self-contained directories with README documentation
3. **Frontend Enhancement** — New STRATEGY POOL tab, LIVE page integration, and README modal popup

## Design Decisions

| Decision | Choice |
|----------|--------|
| CIO information source | Quantitative indicators (primary) + LLM news analysis (secondary) |
| Weight adjustment frequency | Daily (extensible to intraday later) |
| Frontend layout | New STRATEGY POOL tab + simplified summary on LIVE page |
| Strategy file structure | Per-strategy directories with README.md |
| CIO implementation | Backend engine + frontend display only |
| LLM backend | Configurable multi-backend (OpenAI, Claude, Ollama) |
| Architecture approach | Monolithic extension on existing Flask + React |

## 1. CIO Module

### 1.1 Directory Structure

```
system/quant/cio/
├── __init__.py
├── cio_engine.py          # Main engine: coordinates assessment + weight output
├── market_assessor.py     # Quantitative indicators: VIX, trend, breadth
├── news_analyzer.py       # LLM news analysis adapter
├── weight_allocator.py    # Weight allocation logic based on assessment
├── llm_adapters/
│   ├── base.py            # Abstract LLM interface
│   ├── openai_adapter.py
│   ├── claude_adapter.py
│   └── ollama_adapter.py
└── config/
    └── cio_config.yaml    # Indicator thresholds, LLM config, weight constraints
```

### 1.2 Data Flow

```
┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│  Quant      │   │  News/Text  │   │  Current    │
│  Indicators │   │  via LLM    │   │  Positions  │
└──────┬──────┘   └──────┬──────┘   └──────┬──────┘
       │                 │                 │
       ▼                 ▼                 ▼
  ┌──────────────────────────────────────────────┐
  │              CIO Engine                       │
  │  MarketAssessor → environment score (0-100)   │
  │  NewsAnalyzer   → sentiment label + confidence│
  │  WeightAllocator → per-strategy weight recs   │
  └──────────────────┬───────────────────────────┘
                     ▼
            Strategy weight allocation
            {
              env: "low_vol_bull",
              score: 78,
              weights: {
                volatility_regime: 0.40,
                simple_momentum: 0.35,
                mean_reversion: 0.25
              }
            }
```

### 1.3 CIO Engine API

The engine exposes a single `assess()` method that:
1. Collects quantitative indicators (VIX level, trend strength, market breadth, sector rotation)
2. Optionally calls LLM to analyze recent financial news (configurable source)
3. Combines quantitative score + sentiment into an environment label and confidence score
4. Maps environment to strategy weight recommendations based on configurable rules

### 1.4 LLM Adapter Interface

```python
class LLMAdapter(ABC):
    def analyze(self, prompt: str, context: dict) -> dict:
        """Send prompt to LLM, return structured analysis result."""
        ...
```

Each adapter (OpenAI, Claude, Ollama) implements this interface. Configured via `cio_config.yaml`.

### 1.5 Scheduling

- Default: once daily at market open (configurable)
- Manual trigger via API: `POST /api/cio/refresh`
- Result cached until next assessment

## 2. Strategy Pool Modularization

### 2.1 New Directory Structure

```
system/quant/strategies/
├── __init__.py
├── base.py               # Strategy abstract base class (unchanged)
├── registry.py           # Auto-scans strategy directories and registers
├── volatility_regime/
│   ├── README.md         # Logic, applicable scenarios, backtest results
│   ├── strategy.py       # Strategy implementation class
│   └── config.yaml       # Strategy parameters
├── simple_momentum/
│   ├── README.md
│   ├── strategy.py
│   └── config.yaml
├── momentum_eod/
│   └── ...
├── mean_reversion/
│   └── ...
├── dual_thrust/
│   └── ...
├── cross_sectional_mr/
│   └── ...
└── dual_momentum/
    └── ...
```

### 2.2 README.md Template

Each strategy README must contain:

```markdown
# {Strategy Name}

## Strategy Logic
Description of the core alpha signal and how it generates trades.

## Applicable Scenarios
Market conditions where this strategy performs best/worst.

## Parameters
Key configurable parameters and their defaults.

## Backtest Results
| Metric | Value |
|--------|-------|
| Sharpe (OOS) | x.xx |
| Max Drawdown | xx% |
| CAGR | xx% |
| Win Rate | xx% |
| Period | YYYY-YYYY OOS |
```

### 2.3 Registry Update

`registry.py` will auto-discover strategies by scanning subdirectories that contain a `strategy.py` file. Each strategy class registers itself via a `@register_strategy` decorator or by conforming to a naming convention.

### 2.4 Migration

- Move each strategy class from `implementations.py` into its own `{strategy_name}/strategy.py`
- Move existing docs from `docs/` into respective `README.md` files
- Update all imports throughout the codebase
- Existing `docs/` and `examples/` directories will be removed after migration

## 3. API Extensions

### New Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/cio/assessment` | Current market assessment + weight recommendations |
| POST | `/api/cio/refresh` | Manually trigger CIO re-assessment |
| GET | `/api/strategy-pool` | Strategy pool status (weights, capital, performance) |
| POST | `/api/strategy-pool/weights` | Manually adjust strategy weights |
| GET | `/api/strategies/:id/readme` | Get strategy README.md content |

### Response Examples

**GET /api/cio/assessment:**
```json
{
  "environment": "low_vol_bull",
  "score": 78,
  "sentiment": "moderately_bullish",
  "confidence": 0.82,
  "weights": {
    "volatility_regime": 0.40,
    "simple_momentum": 0.35,
    "mean_reversion": 0.25
  },
  "indicators": {
    "vix": 14.5,
    "vix_percentile": 22,
    "trend_strength": 0.72,
    "market_breadth": 0.65
  },
  "last_updated": "2026-04-17T09:30:00",
  "llm_summary": "Markets show moderate bullish sentiment..."
}
```

**GET /api/strategy-pool:**
```json
{
  "total_capital": 100000,
  "strategies": [
    {
      "id": "volatility_regime",
      "name": "Volatility Regime",
      "enabled": true,
      "weight": 0.40,
      "allocated_capital": 40000,
      "current_pnl": 2340,
      "backtest_sharpe": 0.97,
      "has_readme": true
    }
  ]
}
```

## 4. Frontend Architecture

### 4.1 New STRATEGY POOL Tab

Added alongside existing BACKTEST / LIVE TRADING tabs. Contains:

1. **CIO Assessment Panel** (top)
   - Environment label, score (0-100), sentiment
   - Key indicator values (VIX, trend, breadth)
   - LLM summary text
   - Last updated timestamp + refresh button

2. **Strategy Card Grid** (main area)
   - Each strategy as a card showing: name, weight bar, allocated capital, status, P&L, key backtest metrics
   - Disabled strategies shown grayed out with weight 0%
   - "README" link on each card → opens modal popup

3. **Weight Adjustment** (interaction)
   - CIO-recommended weights displayed by default
   - User can manually override weights via slider or input
   - Total must sum to 100% (enforced by UI)

### 4.2 LIVE Page Integration

Upgrade the existing "STRATEGY ZONE" panel:
- Show CIO environment label + score in one line
- Show weight distribution as a horizontal stacked bar chart
- "→ Go to Strategy Pool" link to navigate to STRATEGY POOL tab
- Keep existing strategy selector dropdown

### 4.3 README Modal

- Triggered by clicking "README" link on any strategy card
- Opens a centered modal overlay with dark background
- Renders Markdown content using a lightweight Markdown renderer (react-markdown or marked)
- Strategy name as modal title
- Close button (X) in top-right corner
- Click outside modal to dismiss

### 4.4 New React Components

```
frontend/src/
├── StrategyPoolPage.js      # STRATEGY POOL tab content
├── CIOAssessmentPanel.js     # CIO assessment display
├── StrategyCard.js           # Individual strategy card
├── StrategyReadmeModal.js    # README markdown modal
└── StrategyWeightBar.js      # Weight distribution bar (shared)
```

## 5. Scope

### In Scope
- CIO engine with quantitative indicators + LLM integration
- Strategy directory restructuring (7 strategies migrated)
- STRATEGY POOL tab with full management UI
- LIVE page STRATEGY ZONE upgrade
- README modal popup
- New API endpoints
- LLM adapter layer (OpenAI, Claude, Ollama)

### Out of Scope
- Intraday CIO re-assessment (future)
- Automated trading based on CIO weights (CIO recommends, human decides for now)
- Strategy backtest execution from UI (already exists)
- Real broker integration changes
- News data source integration (will use configurable API, default to placeholder)
