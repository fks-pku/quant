# AShare CSI300 Low-Turnover Multifactor

Candidate daily A-share large-cap strategy inspired by JoinQuant CSI300 low-turnover multi-factor discussions.

- Universe proxy: daily full A-share cross-section, top market-cap band.
- Signal: momentum, low volatility, value, ROE, dividend yield, and low turnover.
- Turnover control: rebalance every 20 trading days and replace at most one holding per rebalance.
- Execution: strict Backtester T+1, CN commission, lot size, status filters, price-limit filters, liquidity impact.
