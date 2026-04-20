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
from quant.strategies.volatility_regime.strategy import VolatilityRegime
from quant.strategies.simple_momentum.strategy import SimpleMomentum
from quant.strategies.cross_sectional_mr.strategy import CrossSectionalMeanReversion


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
        
        commission = backtester._calculate_commission_breakdown(100.0, 100, "US", "BUY")["commission"]
        assert commission == 0.5, f"Expected 100 * 0.005 = 0.5, got {commission}"
        
        commission_small = backtester._calculate_commission_breakdown(100.0, 10, "US", "BUY")["commission"]
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

    def test_backtest_simple_momentum_produces_trades(self):
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

        assert len(result.trades) >= 1
        for trade in result.trades:
            assert trade.entry_price > 0
            assert trade.exit_price > 0
            commission = max(trade.quantity * 0.005, 1.0)
            expected_pnl = (trade.exit_price - trade.entry_price) * trade.quantity - commission
            assert abs(trade.pnl - expected_pnl) < 0.5, f"P&L mismatch: expected ~{expected_pnl}, got {trade.pnl}"

    def test_backtest_all_four_strategies_run(self):
        symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]
        all_symbols = symbols + ["^VIX"]
        data = _generate_test_data(all_symbols, days=120)

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

        strategies = [
            SimpleMomentum(symbols=symbols, momentum_lookback=20, holding_period=21, max_position_pct=0.10),
            VolatilityRegime(symbols=symbols, vix_symbol="^VIX", vix_lookback=20, momentum_top_n=3, max_position_pct=0.10),
            CrossSectionalMeanReversion(symbols=symbols, lookback_days=5, holding_days=5, max_position_pct=0.10),
        ]

        for strategy in strategies:
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

            assert result is not None, f"{strategy.name} returned None"
            assert result.final_nav > 0, f"{strategy.name} final_nav={result.final_nav}"

    def test_backtest_metrics_are_reasonable(self):
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

        assert -3 < result.sharpe_ratio < 3
        assert -1 < result.max_drawdown_pct < 0
        assert 0 <= result.win_rate <= 1

    def test_api_backtest_endpoints(self):
        import sys
        import os
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
            from api_server import app
        except ImportError:
            pytest.skip("api_server not available")

        client = app.test_client()

        resp = client.get('/api/backtest/list')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'backtests' in data

        resp = client.get('/api/backtest/result/nonexistent')
        assert resp.status_code == 404

        resp = client.get('/api/strategies')
        assert resp.status_code == 200
        strategy_names = [s['name'] for s in resp.get_json()['strategies']]
        assert 'CrossSectionalMeanReversion' in strategy_names
        assert 'SimpleMomentum' in strategy_names


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
