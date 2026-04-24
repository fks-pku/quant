# Deprecated Import Paths

The following old import paths have been migrated. Do not use them — they will be removed.

| Old Path | New Path |
|----------|----------|
| `quant.core.*` | `quant.features.trading.*` / `quant.features.backtest.*` |
| `quant.data.*` | `quant.infrastructure.data.*` |
| `quant.execution.*` | `quant.infrastructure.execution.*` |
| `quant.models.*` | `quant.domain.models.*` |
| `quant.utils.*` | `quant.shared.utils.*` |
| `quant.strategies.*` | `quant.features.strategies.*` |
| `quant.cio.*` | `quant.features.cio.*` |
| `quant.config.*` | `quant.shared.config.*` |
| `DuckDBProvider` | Use `DuckDBStorage` directly (no longer a provider) |
| `quant.shared.models.trade` (file) | `quant.domain.models.trade` (shared/models/ only re-exports) |
| `quant.shared.models.order` (file) | `quant.domain.models.order` |
| `quant.shared.models.fill` (file) | `quant.domain.models.fill` |
| `quant.shared.models.position` (file) | `quant.domain.models.position` |
| `from quant.features.trading.risk import RiskCheckResult` | `from quant.domain.models.risk_check import RiskCheckResult` |

## Deprecated Event Types

| Old | New |
|-----|-----|
| `EventType.ORDER_SUBMIT` | `EventType.ORDER_SUBMITTED` |
| `EventType.ORDER_FILL` | `EventType.ORDER_FILLED` |
| `EventType.ORDER_CANCEL` | `EventType.ORDER_CANCELLED` |
| `EventType.ORDER_REJECT` | `EventType.ORDER_REJECTED` |
