# A-share Turtle Price Breakout

Long-only A-share Donchian/Turtle breakout candidate.

- Entry: buy stocks with raw close above 10 CNY when adjusted high breaks the prior 20-day adjusted high.
- Exit: sell when adjusted low breaks the prior 10-day adjusted low, raw close falls to 10 CNY or below, listing/ST status becomes risky, or the 2 ATR stop is hit.
- Signal price: HFQ adjusted OHLC when available.
- Sizing price: raw close, rounded to A-share lots.
- Portfolio: up to 20 names, equal target value.
