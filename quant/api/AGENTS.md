# API Layer

## 职责

Flask thin routing layer. Exposes features via REST endpoints. Read-only storage access.

## 对外契约

Blueprints:
- `backtest_bp` — backtest execution and results
- `strategies_bp` — strategy registry, parameters, live strategy performance (including cash, total NAV, and slippage metrics), and daily live record reads
- `positions_bp` — position queries
- `orders_bp` — order management
- `system_bp` — system status and control
- `cio_bp` — CIO assessment endpoints
- `research_bp` — research pipeline control
- `futu_bp` — Futu broker integration
- `research_bp._make_validation_components(cfg)` — composition-root wiring for research statistical validation

## 依赖

- `features/` — only calls feature orchestrators
- `infrastructure/research` — research_bp composes market data, factor data, PIT data, stores, and sources behind domain ports
- `shared/utils` — logger, config_loader
- `shared/models` — re-exports from domain

## 不变量

- API endpoints use `DuckDBStorage(read_only=True)` — never write to storage
- All state is in `api/state/runtime.py` — not in global variables
- Blueprints do not contain business logic — they only serialize/deserialize
- Research validation is injected into `ResearchEngine`; do not import infrastructure from `features/research`
- The API root (`/`) returns service metadata JSON. The old React/static UI is removed; local strategy management uses `quant/scripts/strategy_dashboard_server.py`.

## 修改守则

- Add route: add to appropriate `*_bp.py`
- Change state management: edit `api/state/runtime.py`
- Change research report access: edit `api/research_bp.py` helpers around the three stage reports.
- Change local strategy/research dashboard behavior: edit `quant/scripts/strategy_dashboard_server.py`, not `api_server.py`.

## Known Pitfalls

- `runtime.py` uses threading.RLock for thread-safe state access
- Blueprint functions must not raise — return error dicts with appropriate HTTP status codes
- Live record endpoints must validate record kind and return HTTP 400 for unsupported kinds.
- Do not cache DuckDB connections across requests
- Large-universe strict backtests may reuse `_DuckDBDailyDateProvider` chunk caches under `quant/infrastructure/var/research/cache/daily_date_provider/`; keys include source DuckDB file size/mtime, including ETF fund NAV when ETF symbols are present, so data rebuilds naturally invalidate the cache
- `_DuckDBDailyDateProvider` must split stock, ETF, and index symbols before loading bars: stocks use status-enriched `daily_cn_ochl`, ETFs use `cn_etf.daily_cn_ochl`, and indexes use `cn_index.daily_cn_ochl`.
- `_DuckDBDailyDateProvider` must expose `get_dividend_for_date()` from `cn_corporate_actions.duckdb::cn_dividends` when available, so strict stock reports include cash dividends and stock dividends.
- `_DuckDBDailyDateProvider` must normalize ETF/LOF bars with `cn_fund_nav.adj_nav / unit_nav` when fund NAV data is available; preserve `raw_*` fields for audit and turnover-unit inference.

## Research Report Endpoints

- `/api/research/run` defaults to `mode=full`; `full` and `formal` run end-to-end full-report research with strict Backtester and walk-forward wiring by default.
- `/api/research/run` accepts `mode=fast|strict|walkforward` for explicitly single-stage research runs.
- `/api/research/report` returns latest report metadata, including full-report and stage-report URLs.
- `/api/research/report/latest` serves the latest `full_research_report.html` when available.
- `/api/research/report/stage/<stage_key>` serves `fast_research`, `strict_backtest`, or `walkforward_strict_audit` stage HTML.
- Local `/research` and `/api/research/dashboard` are served by `quant/scripts/strategy_dashboard_server.py` for source-grouped idea review.
