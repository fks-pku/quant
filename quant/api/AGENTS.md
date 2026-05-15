# API Layer

## 职责

Flask thin routing layer. Exposes features via REST endpoints. Read-only storage access.

## 对外契约

Blueprints:
- `backtest_bp` — backtest execution and results
- `strategies_bp` — strategy registry and parameters
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

## 修改守则

- Add route: add to appropriate `*_bp.py`
- Change state management: edit `api/state/runtime.py`
- Change research report access: edit `api/research_bp.py` helpers around the three stage reports.

## Known Pitfalls

- `runtime.py` uses threading.RLock for thread-safe state access
- Blueprint functions must not raise — return error dicts with appropriate HTTP status codes
- Do not cache DuckDB connections across requests

## Research Report Endpoints

- `/api/research/run` accepts `mode=fast|strict|walkforward` for independently runnable research stages.
- `/api/research/report` returns latest report metadata, including stage report URLs.
- `/api/research/report/latest` intentionally returns 410; full reports are no longer generated.
- `/api/research/report/stage/<stage_key>` serves `fast_research`, `strict_backtest`, or `walkforward_strict_audit` stage HTML.
