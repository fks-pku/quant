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

## 依赖

- `features/` — only calls feature orchestrators
- `shared/utils` — logger, config_loader
- `shared/models` — re-exports from domain

## 不变量

- API endpoints use `DuckDBStorage(read_only=True)` — never write to storage
- All state is in `api/state/runtime.py` — not in global variables
- Blueprints do not contain business logic — they only serialize/deserialize

## 修改守则

- Add route: add to appropriate `*_bp.py`
- Change state management: edit `api/state/runtime.py`

## Known Pitfalls

- `runtime.py` uses threading.RLock for thread-safe state access
- Blueprint functions must not raise — return error dicts with appropriate HTTP status codes
- Do not cache DuckDB connections across requests
