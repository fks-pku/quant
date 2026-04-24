# Frontend Two-Module Redesign

**Date**: 2026-04-17
**Status**: Approved
**Approach**: Single-page scroll (方案A)

## Overview

Consolidate the current 3-tab layout (回测/实盘/策略池) into 2 tabs: **回测** and **实盘**. The CIO strategy pool merges into the live trading module. Keep existing dark theme visual style.

## Architecture

### Navigation

- 2 tabs: `backtest` and `live`
- Header unchanged: QUANTSYSTEM logo + status badge + broker selector + start/stop
- Continue using `activeTab` state (no React Router)

### Component File Structure

| File | Action | Purpose |
|------|--------|---------|
| `App.js` | Modify | 2 tabs only, move inline live JSX out |
| `App.css` | Modify | Add live module styles |
| `BacktestDashboard.js` | Modify | Add strategy-specific parameter inputs |
| `LiveTradingPage.js` | **New** | Live trading main page (scroll layout) |
| `PositionOverview.js` | **New** | Asset summary + tabbed holdings tables |
| `StrategyManagement.js` | **New** | CIO panel + activation dropdown + strategy table |
| `StrategyDetailModal.js` | **Rewrite** | 3-tab modal (README / backtest / live performance) |
| `CIOAssessmentPanel.js` | Keep | No changes |
| `StrategyWeightBar.js` | Keep | No changes |
| `StrategyPoolPage.js` | Remove | Merged into StrategyManagement |
| `StrategyCard.js` | Remove | No longer needed |
| `StrategyReadmeModal.js` | Remove | Merged into StrategyDetailModal README tab |

## Module 1: Backtest

### Preserved from Current

- Strategy selector dropdown (change from hardcoded 4 to dynamic `/api/strategies`)
- Date range, symbols, initial cash, slippage inputs
- Async execution via POST `/api/backtest/run` + polling `/api/backtest/result/<id>`
- 8 metric cards (Final NAV, Total Return, Sharpe, Sortino, Max DD, Win Rate, Profit Factor, Total Trades)
- Equity curve SVG + drawdown SVG
- Trades table
- History sidebar

### New: Strategy-Specific Parameters

When a strategy is selected in the dropdown:

1. Call `GET /api/strategies/<id>/parameters` to fetch parameter definitions
2. Render dynamic form fields below the main form (one row per parameter):
   - `int` / `float` → number input
   - `bool` → toggle/checkbox
   - `str` → text input or dropdown (if `options` available)
3. Each field shows: parameter name, control, default value
4. On "Run Backtest", send `strategy_params` alongside base parameters in the POST body

### Backend Change

`POST /api/backtest/run` must accept a `strategy_params` JSON field and pass it to the strategy constructor during backtest execution.

## Module 2: Live Trading (Single-Page Scroll)

### Area 1: Position Overview (`PositionOverview`)

**Top summary row** — 4 metric cards:
- Total NAV
- Unrealized P&L
- Realized P&L
- Total P&L

**Tab switch below**:

| Tab | Content | API |
|-----|---------|-----|
| Securities Holdings | Table: symbol, name, qty, avg cost, market value, unrealized P&L, P&L % | `/api/portfolio` |
| Strategy NAV Distribution | Table: strategy name, status, weight %, allocated capital, strategy NAV, strategy P&L | `/api/strategy-pool` |

### Area 2: Strategy Management (`StrategyManagement`)

**Top half: CIO Assessment Panel** (reuse existing component)
- Market environment label, score (0-100), sentiment, VIX, trend strength
- Strategy weight allocation bar (`StrategyWeightBar`)
- Refresh button (calls `POST /api/cio/refresh`)

**Bottom half: Strategy Activation & List**
- Dropdown to select active strategy (calls `POST /api/strategies/select`)
- Strategy table with columns: name, status (toggle switch), weight, allocated capital, P&L summary
- Click a strategy row → open `StrategyDetailModal`

### Area 3: Strategy Detail Modal (`StrategyDetailModal`)

Modal with 3 internal tabs:

| Tab | Content | API | Empty State |
|-----|---------|-----|-------------|
| README | Rendered markdown: strategy logic, parameters, applicable market conditions | `GET /api/strategies/<id>/readme` | "README not available" |
| Backtest Performance | Equity curve SVG + metric cards if backtest history exists | `GET /api/strategies/backtest/<id>` | "暂无回测记录，请先在回测模块运行" |
| Live Performance | P&L curve SVG + recent trades table | `GET /api/strategies/performance/<id>` | "该策略尚未在实盘中运行" |

### Area 4: Recent Orders

Compact table showing latest 10 orders from `/api/orders`. Columns: time, symbol, side, quantity, price, status.

## Backend API Changes

| Change | Details |
|--------|---------|
| `POST /api/backtest/run` | Accept `strategy_params` field, pass to strategy constructor |
| Existing endpoints | All other endpoints (`/api/portfolio`, `/api/strategy-pool`, `/api/strategies/performance/<id>`, `/api/strategies/backtest/<id>`, `/api/strategies/<id>/readme`) remain as-is |

## Data Flow

```
App.js
├── Tab: Backtest → BacktestDashboard
│   ├── Strategy selector → GET /api/strategies
│   ├── Parameter form → GET /api/strategies/<id>/parameters
│   ├── Run → POST /api/backtest/run {strategy_id, params, strategy_params}
│   └── Poll → GET /api/backtest/result/<id>
│
└── Tab: Live → LiveTradingPage
    ├── PositionOverview
    │   ├── Summary cards → GET /api/portfolio
    │   ├── Holdings tab → GET /api/portfolio
    │   └── Strategy NAV tab → GET /api/strategy-pool
    ├── StrategyManagement
    │   ├── CIO panel → GET /api/cio/assessment
    │   ├── Active selector → POST /api/strategies/select
    │   └── Strategy table → GET /api/strategy-pool
    ├── StrategyDetailModal (on row click)
    │   ├── README tab → GET /api/strategies/<id>/readme
    │   ├── Backtest tab → GET /api/strategies/backtest/<id>
    │   └── Live tab → GET /api/strategies/performance/<id>
    └── Recent Orders → GET /api/orders
```

## Styling

- Keep existing dark theme: `--bg-primary: #0a0a0f`, cyan/green/red accents, Outfit + JetBrains Mono
- New components follow existing panel-card pattern with 12px border-radius
- Reuse existing CSS custom properties and animation keyframes
