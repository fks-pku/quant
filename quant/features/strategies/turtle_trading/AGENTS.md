# TurtleTrading Strategy

## Overview

Simplified Donchian Channel breakout strategy for A-share markets.

## Logic

- **Entry**: Close > highest high of past `entry_period` days (default 20)
- **Exit**: Close < lowest low of past `exit_period` days (default 10)
- **Position sizing**: Fixed `max_position_pct` of NAV per symbol (default 5%)
- **ATR**: Calculated for logging/diagnostics only (not used for sizing in simplified version)

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `symbols` | `["600519", "000858", "601318"]` | A-share symbols to trade |
| `entry_period` | 20 | Donchian channel lookback for entry |
| `exit_period` | 10 | Donchian channel lookback for exit |
| `max_position_pct` | 0.05 | Max NAV% per position |
| `atr_period` | 20 | ATR calculation period (diagnostic) |

## Files

- `strategy.py` — Strategy implementation
- `config.yaml` — Default parameters
