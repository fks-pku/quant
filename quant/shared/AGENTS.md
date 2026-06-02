# Shared Layer

## 职责

Cross-feature pure utilities. No business semantics.

## 对外契约

### utils/
- `setup_logger(name)` — configure logging
- `ConfigLoader` — load YAML configs
- `datetime_utils` — timezone and market calendar helpers

### models/
- Re-exports from `domain/models/` for backward compatibility
- No independent model class definitions

### config/
- `config.yaml` — main system configuration
- `brokers.yaml` — broker credentials and adapter paths, including QMT `userdata_mini_path` and optional `xtquant_path`
- `strategies.yaml` — strategy parameters

## 依赖

None — shared has no business logic, only utilities.

## 不变量

- `shared/models/` only re-exports from domain — never defines new model classes
- No feature-to-feature coupling via shared/
- All imports from domain or stdlib

## 修改守则

- Change logging: edit `shared/utils/logger.py`
- Change config: edit `shared/config/*.yaml`
- Change datetime utils: edit `shared/utils/datetime_utils.py`

## Known Pitfalls

- Adding business logic to shared/ creates hidden coupling — keep shared/ purely mechanical
- ConfigLoader uses YAML and reads configs as explicit UTF-8; config files may contain Chinese comments and must not depend on the OS default encoding
