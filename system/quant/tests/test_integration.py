"""End-to-end integration test: load config -> create strategy -> run backtest -> verify results."""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path

from quant.core.backtester import Backtester
from quant.core.engine import Engine, Context
from quant.core.events import EventBus
from quant.core.portfolio import Portfolio
from quant.core.risk import RiskEngine
from quant.strategies.implementations.volatility_regime import VolatilityRegime
from quant.strategies.implementations.simple_momentum import SimpleMomentum


def _generate_test_data(symbols, days=120, start_date=None):
    """Generate synthetic OHLCV data for testing."""
    if start_date is None:
        start_date = datetime(2024, 1, 2)
    
    records = []
    np.random.seed(42)
    
    for symbol in symbols:
        price = 150.0 if symbol != "^VIX" else 18.0
        for i in range(days):
            date = start_date + timedelta(days=i)
            if date.weekday() >= 5:
                continue
            
            if symbol == "^VIX":
                change = np.random.normal(0, 0.5)
                price = max(10.0, min(35.0, price + change))
            else:
                change = np.random.normal(0.0005, 0.02)
                price = max(10.0, price * (1 + change))
            
            records.append({
                'timestamp': date,
                'symbol': symbol,
                'open': price * (1 + np.random.uniform(-0.005, 0.005)),
                'high': price * (1 + np.random.uniform(0, 0.02)),
                'low': price * (1 - np.random.uniform(0, 0.02)),
                'close': round(price, 2),
                'volume': np.random.randint(100000, 10000000),
            })
    
    return pd.DataFrame(records)


class TestBacktesterIntegration:
    """Integration test: backtest with real strategy and synthetic data."""

    def test_backtest_runs_without_error(self):
        """Backtest should run from start to end without raising."""
        symbols = ["AAPL", "MSFT", "GOOGL"]
        all_symbols = symbols + ["^VIX"]
        data = _generate_test_data(all_symbols, days=120)
        
        config = {
            "backtest": {"slippage_bps": 5, "speed": "1x"},
            "execution": {
                "commission": {
                    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
                    "HK": {"type": "percent", "percent": 0.001, "min_per_order": 2.0}
                }
            },
            "data": {"default_timeframe": "1d"},
            "risk": {
                "max_position_pct": 0.20,
                "max_sector_pct": 0.50,
                "max_daily_loss_pct": 0.05,
                "max_leverage": 2.0,
                "max_orders_minute": 100,
            }
        }
        
        strategy = VolatilityRegime(
            symbols=symbols,
            vix_symbol="^VIX",
            vix_lookback=20,
            momentum_top_n=3,
            max_position_pct=0.10,
        )
        
        backtester = Backtester(config)
        
        start = data['timestamp'].min()
        end = data['timestamp'].max()
        
        result = backtester.run(
            start=start,
            end=end,
            strategies=[strategy],
            initial_cash=100000,
            data_provider=_InMemoryProvider(data),
            symbols=all_symbols,
        )
        
        assert result is not None
        assert result.final_nav > 0
        assert isinstance(result.sharpe_ratio, float)
        assert isinstance(result.max_drawdown, float)
        assert isinstance(result.equity_curve, pd.Series)

    def test_backtest_commission_calculated_correctly(self):
        """Commission should be qty * per_share, not price * qty * per_share."""
        from quant.core.backtester import CommissionConfig
        
        config = {
            "backtest": {"slippage_bps": 0, "speed": "1x"},
            "execution": {
                "commission": {
                    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 0.0},
                }
            },
            "data": {"default_timeframe": "1d"},
            "risk": {
                "max_position_pct": 1.0,
                "max_sector_pct": 1.0,
                "max_daily_loss_pct": 1.0,
                "max_leverage": 10.0,
                "max_orders_minute": 1000,
            }
        }
        
        backtester = Backtester(config)
        
        commission = backtester._calculate_commission(100.0, 100, "US")
        assert commission == 0.5, f"Expected 100 * 0.005 = 0.5, got {commission}"
        
        commission_small = backtester._calculate_commission(100.0, 10, "US")
        assert commission_small == 0.05, f"Expected 10 * 0.005 = 0.05, got {commission_small}"

    def test_backtest_pnl_correct_for_sell(self):
        """P&L for closing a long position should be (exit - entry) * qty."""
        config = {
            "backtest": {"slippage_bps": 0, "speed": "1x"},
            "execution": {
                "commission": {
                    "US": {"type": "per_share", "per_share": 0.0, "min_per_order": 0.0},
                }
            },
            "data": {"default_timeframe": "1d"},
            "risk": {
                "max_position_pct": 1.0,
                "max_sector_pct": 1.0,
                "max_daily_loss_pct": 1.0,
                "max_leverage": 10.0,
                "max_orders_minute": 1000,
            }
        }
        
        symbols = ["AAPL"]
        data = _generate_test_data(symbols, days=60)
        
        class BuyThenSell(VolatilityRegime):
            """Test strategy: buy on day 10, sell on day 30."""
            def __init__(self):
                super().__init__(symbols=symbols, max_position_pct=0.5)
                self._day_count = 0
                self._bought = False
                self._sold = False
            
            def on_data(self, context, data):
                self._day_count += 1
                if isinstance(data, dict):
                    symbol = data.get("symbol", "")
                    close = data.get("close", 0)
                else:
                    return
                
                if symbol != "AAPL":
                    return
                
                if self._day_count == 10 and not self._bought:
                    self.buy("AAPL", 10)
                    self._bought = True
                elif self._day_count == 30 and self._bought and not self._sold:
                    self.sell("AAPL", 10)
                    self._sold = True
            
            def on_after_trading(self, context, trading_date):
                pass
        
        strategy = BuyThenSell()
        backtester = Backtester(config)
        
        start = data['timestamp'].min()
        end = data['timestamp'].max()
        
        result = backtester.run(
            start=start,
            end=end,
            strategies=[strategy],
            initial_cash=100000,
            data_provider=_InMemoryProvider(data),
            symbols=symbols,
        )
        
        assert len(result.trades) > 0, "Expected at least one trade"
        
        if result.trades:
            trade = result.trades[0]
            assert trade.symbol == "AAPL"
            assert trade.entry_price > 0
            assert trade.exit_price > 0
            expected_pnl = (trade.exit_price - trade.entry_price) * trade.quantity
            assert abs(trade.pnl - expected_pnl) < 1.0, f"P&L mismatch: expected ~{expected_pnl}, got {trade.pnl}"

    def test_engine_order_manager_wired(self):
        """Engine should wire OrderManager to strategy context."""
        config = {
            "system": {"mode": "paper", "initial_cash": 100000},
            "markets": {"US": {"timezone": "America/New_York", "open_hour": 9, "open_minute": 30, "close_hour": 16, "close_minute": 0}},
            "risk": {"max_position_pct": 0.05, "max_sector_pct": 0.25, "max_daily_loss_pct": 0.02, "max_leverage": 1.5, "max_orders_minute": 30},
        }
        
        engine = Engine(config)
        
        from quant.execution.order_manager import OrderManager
        om = OrderManager(
            portfolio=engine.portfolio,
            risk_engine=engine.risk_engine,
            event_bus=engine.event_bus,
            config=config,
        )
        engine.set_order_manager(om)
        
        strategy = VolatilityRegime(symbols=["AAPL"])
        engine.add_strategy(strategy)
        
        assert strategy.context is not None
        assert strategy.context.order_manager is not None

    def test_simple_momentum_backtest(self):
        """SimpleMomentum strategy should run in backtest without error."""
        symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]
        data = _generate_test_data(symbols, days=120)
        
        config = {
            "backtest": {"slippage_bps": 5, "speed": "1x"},
            "execution": {
                "commission": {
                    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
                }
            },
            "data": {"default_timeframe": "1d"},
            "risk": {
                "max_position_pct": 0.20,
                "max_sector_pct": 1.0,
                "max_daily_loss_pct": 0.10,
                "max_leverage": 2.0,
                "max_orders_minute": 100,
            }
        }
        
        strategy = SimpleMomentum(
            symbols=symbols,
            momentum_lookback=20,
            holding_period=21,
            max_position_pct=0.10,
        )
        
        backtester = Backtester(config)
        start = data['timestamp'].min()
        end = data['timestamp'].max()
        
        result = backtester.run(
            start=start,
            end=end,
            strategies=[strategy],
            initial_cash=100000,
            data_provider=_InMemoryProvider(data),
            symbols=symbols,
        )
        
        assert result is not None
        assert result.final_nav > 0
        assert isinstance(result.sharpe_ratio, float)


class _InMemoryProvider:
    """Data provider backed by a DataFrame."""
    
    def __init__(self, data: pd.DataFrame):
        self.data = data
    
    def get_bars(self, symbol, start, end, timeframe="1d"):
        mask = (
            (self.data['symbol'] == symbol) &
            (self.data['timestamp'] >= start) &
            (self.data['timestamp'] < end)
        )
        result = self.data[mask].copy()
        if not result.empty:
            result = result.set_index('timestamp')
        return result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
