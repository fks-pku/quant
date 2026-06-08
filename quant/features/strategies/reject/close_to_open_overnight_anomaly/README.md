# Close-to-Open Overnight Anomaly

Candidate strategy for the close-to-next-open anomaly described in arXiv:2201.00223.

## Thesis

Hold exposure only overnight: buy selected names at the same trading day's close and sell the filled quantity at the next trading day's open.

## Execution Contract

- Entry: `SAME_CLOSE` MARKET buy, filled from the current day's raw close with BUY slippage upward.
- Exit: order is submitted from the entry fill callback and uses default `NEXT_OPEN`, filled from the next trading day's raw open with SELL slippage downward.
- Quantity: equal target slots using current NAV, rounded by `lot_size`.
- Candidate status: this strategy is under `strategies/reject/` until a strict full research report promotes it.

## Bias Notes

- The strategy ranks symbols only by historical overnight returns known by the current close: `open[t] / close[t-1] - 1`.
- It must not use same-day close-to-next-open outcomes for selection.
- A `SAME_CLOSE` fill assumes a close-auction or pre-close order can be placed. For live use, scheduling must be implemented explicitly; the current `SAME_CLOSE` timing is a backtest-only execution mode.
