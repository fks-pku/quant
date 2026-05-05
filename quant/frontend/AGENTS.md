# Frontend Layer

## 职责

React SPA dashboard. Displays backtest results, live trading status, strategy management.

## 对外契约

Pages (src/):
- `BacktestDashboard.js` — backtest configuration and results
- `LiveTradingPage.js` — real-time trading status
- `StrategyManagement.js` — strategy lifecycle
- `ResearchPanel.js` — research pipeline control
- `CIOAssessmentPanel.js` — CIO weight allocation
- `AccountOverview.js`, `PositionOverview.js` — account/position display
- `StrategyDetailModal.js`, `StrategyPositionCards.js`, `StrategyWeightBar.js` — strategy components

State:
- `api/state/runtime.py` holds `AVAILABLE_STRATEGIES`, `STRATEGY_PARAMETERS` — these drive frontend strategy list

## 依赖

- React 18+
- Flask API (`quant/api_server.py`)
- No business logic — pure presentation

## 不变量

- Frontend strategy list is driven by `AVAILABLE_STRATEGIES` from `api/state/runtime.py`
- Only strategies with implementations in `features/strategies/*/strategy.py` appear in the frontend
- No direct database access — all data via Flask API

## 修改守则

- Change UI: edit files in `quant/frontend/src/`
- Change API integration: edit `App.js` API calls
- Change strategy display: edit `StrategyManagement.js`

## Known Pitfalls

- Build output goes to `quant/frontend/build/` — served by API server
- API endpoint changes must be reflected in frontend fetch calls
- Strategy list is cached in runtime.py at startup — restart API server after adding strategies
