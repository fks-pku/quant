import uuid
import threading
from datetime import datetime
from flask import Blueprint, jsonify, request

from quant.api.state import (
    _backtest_results, _backtest_lock, STRATEGY_ID_TO_REGISTRY,
)

backtest_bp = Blueprint('backtest', __name__)


@backtest_bp.route('/api/backtest/run', methods=['POST'])
def run_backtest():
    data = request.json
    strategy_id = data.get('strategy_id', 'SimpleMomentum')
    start_date = data.get('start_date', '2020-01-01')
    end_date = data.get('end_date', '2024-12-31')
    symbols = data.get('symbols', ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'SPY'])
    initial_cash = data.get('initial_cash', 100000)
    slippage_bps = data.get('slippage_bps', 5)
    strategy_params = data.get('strategy_params', {})

    backtest_id = str(uuid.uuid4())[:8]
    with _backtest_lock:
        _backtest_results[backtest_id] = {"status": "running", "backtest_id": backtest_id}

    def _run():
        try:
            import pandas as pd
            from quant.core.backtester import Backtester
            from quant.strategies.registry import StrategyRegistry
            from quant.data.providers.duckdb_provider import DuckDBProvider
            registry = StrategyRegistry()
            registry_key = STRATEGY_ID_TO_REGISTRY.get(strategy_id, strategy_id)
            strategy_class = registry.get(registry_key)

            db_provider = DuckDBProvider()
            db_provider.connect()

            all_data = []
            missing_symbols = []

            for symbol in symbols:
                bars = db_provider.get_bars(
                    symbol,
                    datetime.strptime(start_date, '%Y-%m-%d'),
                    datetime.strptime(end_date, '%Y-%m-%d'),
                    "1d",
                )
                if not bars.empty:
                    all_data.append(bars)
                else:
                    missing_symbols.append(symbol)

            if not all_data:
                db_provider.disconnect()
                with _backtest_lock:
                    _backtest_results[backtest_id] = {
                        "status": "error",
                        "error": f"No data found in DuckDB for symbols: {missing_symbols}. "
                                 f"Available: {db_provider.list_available_symbols('daily', 'hk') + db_provider.list_available_symbols('daily', 'us')}",
                        "backtest_id": backtest_id,
                    }
                return

            from quant.core.walkforward import DataFrameProvider

            data_df = pd.concat(all_data, ignore_index=True)
            data_provider = DataFrameProvider(data_df)

            import inspect as _inspect

            registry = StrategyRegistry()
            registry_key = STRATEGY_ID_TO_REGISTRY.get(strategy_id, strategy_id)
            strategy_class = registry.get(registry_key)
            if strategy_class is None:
                with _backtest_lock:
                    _backtest_results[backtest_id] = {"status": "error", "error": f"Strategy {strategy_id} not found", "backtest_id": backtest_id}
                return
            sig = _inspect.signature(strategy_class.__init__)
            accepted = set(list(sig.parameters.keys())[1:])
            strategy_kwargs = {"symbols": symbols}
            if strategy_params:
                for k, v in strategy_params.items():
                    if k in accepted:
                        strategy_kwargs[k] = v
            strategy = strategy_class(**strategy_kwargs)

            config = {
                "backtest": {"slippage_bps": slippage_bps},
                "execution": {"commission": {
                    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
                    "HK": {"type": "percent", "percent": 0.001, "min_per_order": 2.0},
                }},
                "data": {"default_timeframe": "1d"},
                "risk": {"max_position_pct": 0.20, "max_sector_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 2.0, "max_orders_minute": 100},
            }

            backtester = Backtester(config)
            result = backtester.run(
                start=datetime.strptime(start_date, '%Y-%m-%d'),
                end=datetime.strptime(end_date, '%Y-%m-%d'),
                strategies=[strategy],
                initial_cash=initial_cash,
                data_provider=data_provider,
                symbols=data_df['symbol'].unique().tolist(),
            )

            equity_list = result.equity_curve.reset_index().values.tolist()
            equity_serializable = [[str(r[0]), float(r[1])] for r in equity_list]

            trades_list = []
            for t in result.trades:
                trades_list.append({
                    "entry_time": str(t.entry_time),
                    "exit_time": str(t.exit_time),
                    "symbol": t.symbol,
                    "side": t.side,
                    "entry_price": float(t.entry_price),
                    "exit_price": float(t.exit_price),
                    "quantity": int(t.quantity),
                    "pnl": float(t.pnl),
                })

            winning = [t for t in result.trades if t.pnl > 0]
            losing = [t for t in result.trades if t.pnl < 0]
            days = max(1, (datetime.strptime(end_date, '%Y-%m-%d') - datetime.strptime(start_date, '%Y-%m-%d')).days)
            cagr = float(result.total_return * 100 / max(1, days / 365.25))

            metrics = {
                "final_nav": float(result.final_nav),
                "total_return": float(result.total_return),
                "total_return_pct": float(result.total_return * 100),
                "sharpe_ratio": float(result.sharpe_ratio),
                "sortino_ratio": float(result.sortino_ratio),
                "max_drawdown": float(result.max_drawdown),
                "max_drawdown_pct": float(result.max_drawdown_pct * 100),
                "win_rate": float(result.win_rate * 100),
                "profit_factor": float(result.profit_factor),
                "total_trades": result.metrics.total_trades,
                "cagr": cagr,
                "calmar_ratio": float(result.metrics.calmar_ratio),
                "payoff_ratio": float(result.metrics.payoff_ratio),
                "expectancy": float(result.metrics.expectancy),
                "avg_win": float(sum(t.pnl for t in winning) / max(1, len(winning))),
                "avg_loss": float(sum(t.pnl for t in losing) / max(1, len(losing))),
            }

            with _backtest_lock:
                _backtest_results[backtest_id] = {
                    "status": "completed",
                    "backtest_id": backtest_id,
                    "strategy_id": strategy_id,
                    "metrics": metrics,
                    "equity_curve": equity_serializable,
                    "trades": trades_list,
                    "description": f"{strategy_id} backtest from {start_date} to {end_date} on {', '.join(symbols)}",
                }
        except Exception as e:
            with _backtest_lock:
                _backtest_results[backtest_id] = {"status": "error", "error": str(e), "backtest_id": backtest_id}

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return jsonify({"backtest_id": backtest_id, "status": "running"})


@backtest_bp.route('/api/backtest/result/<backtest_id>')
def get_backtest_result(backtest_id):
    with _backtest_lock:
        result = _backtest_results.get(backtest_id)
    if result is None:
        return jsonify({"error": "Backtest not found"}), 404
    return jsonify(result)


@backtest_bp.route('/api/backtest/list')
def list_backtests():
    results = []
    with _backtest_lock:
        for bid, r in _backtest_results.items():
            results.append({
                "backtest_id": bid,
                "status": r.get("status"),
                "strategy_id": r.get("strategy_id"),
                "total_return_pct": r.get("metrics", {}).get("total_return_pct"),
                "sharpe_ratio": r.get("metrics", {}).get("sharpe_ratio"),
            })
    return jsonify({"backtests": results})
