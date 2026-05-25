# Rejected Strategy Archive

This folder stores research candidates that are not eligible for the top-level strategy registry.

Promotion rule:

- A strategy may live directly under `quant/features/strategies/<strategy_id>/` only when a strict local backtest report shows `CAGR > 10%`.
- Strategies with `CAGR <= 10%`, no strict report, or failed validation must stay under `quant/features/strategies/reject/<strategy_id>/`.
- Rejected strategies can still be imported manually for audit, regression tests, and historical reproduction, but they are not auto-discovered by `StrategyRegistry`.

Promotion checklist:

1. Produce a strict backtest report under `quant/infrastructure/var/research/reports/<strategy_id>/`.
2. Verify the report has `metrics.cagr > 0.10` or `grid_result.best.cagr > 0.10`.
3. Move the strategy directory from `reject/` to the top-level `strategies/` folder.
4. Update imports in tests or runner scripts and run the strategy invariant tests.
