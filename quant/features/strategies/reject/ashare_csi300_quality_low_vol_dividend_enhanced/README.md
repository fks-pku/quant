# A-share CSI300 Quality Low-Vol Dividend Enhanced

Research-only A-share index-enhancement candidate for the CSI 300 proxy large-cap universe. The optimized thesis is that large-cap A-shares need a broader factor blend: momentum and valuation keep beta and upside participation, while profitability, low volatility, and dividend yield act as soft quality controls.

Default construction:

- Universe: historical daily top-market-cap A-share proxy pool, then ordinary-account permission filter excluding `300`, `301`, `688`, and `689`; `000300` is retained only as benchmark/timing data.
- Index-enhancement posture: no market timing gate by default, so the portfolio keeps equity beta instead of moving to cash when the index trend weakens.
- Filters: price at least 5, average turnover at least 200,000, positive PB/PE/PS, loose extreme-valuation guards, annualized 120-day volatility no higher than 120%, and recent drawdown no deeper than -60%.
- Ranking: one-year momentum, recent momentum, high ROE, low volatility, low PB, low PE, low turnover, and dividend yield.
- Portfolio: rebalance every 20 trading days, hold up to 40 equal-weight stocks, keep 95% target exposure, and limit each rebalance to at most 10 replacements.
- Risk exit: enabled by default with 20% stop loss and a 55% profit trigger followed by a 16% trailing stop.

This is a candidate strategy. Promotion requires strict full-report evidence to pass the current production checklist and residual risks such as proxy-universe drift and missing official CSI 300 point-in-time constituents must remain explicit.
