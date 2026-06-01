import uuid
import threading
from datetime import datetime
from flask import Blueprint, jsonify, request

from quant.api.state import runtime as state

backtest_bp = Blueprint('backtest', __name__)


@backtest_bp.route('/api/backtest/run', methods=['POST'])
def run_backtest():
    data = request.json or {}
    strategy_id = data.get('strategy_id')
    start_date = data.get('start_date', '2020-01-01')
    end_date = data.get('end_date', '2024-12-31')
    symbols = data.get('symbols', ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'SPY'])
    initial_cash = data.get('initial_cash', 100000)
    slippage_bps = data.get('slippage_bps', 5)
    strategy_params = data.get('strategy_params', {})
    risk_config = data.get('risk_config', {})

    if not strategy_id:
        return jsonify({'error': 'strategy_id is required. No built-in strategies are currently registered.'}), 400

    backtest_id = str(uuid.uuid4())[:8]
    with state._backtest_lock:
        state._backtest_results[backtest_id] = {"status": "running", "backtest_id": backtest_id}

    def _run():
        def detect_benchmark_symbol(symbols):
            from quant.domain.models.market import is_cn_symbol
            if any(is_cn_symbol(s) for s in symbols):
                return "000300"
            return None

        benchmark_provider = None
        benchmark_symbol = detect_benchmark_symbol(symbols)

        try:
            from quant.features.backtest.engine import Backtester
            from quant.features.strategies.registry import StrategyRegistry
            from quant.domain.ports.storage import Storage
            from quant.infrastructure.data.storage_duckdb import DuckDBStorage
            from quant.features.trading.portfolio import Portfolio
            from quant.features.trading.risk import RiskEngine
            from quant.features.trading.sub_portfolio import SubPortfolio

            db: Storage = DuckDBStorage(read_only=True, use_security_status=True)
            try:
                start_dt = datetime.strptime(start_date, '%Y-%m-%d')
                end_dt = datetime.strptime(end_date, '%Y-%m-%d')
                data_df = db.get_bars_for_symbols(symbols, start_dt, end_dt, "1d")
                loaded_symbols = set(data_df["symbol"].unique().tolist()) if not data_df.empty else set()
                missing_symbols = [symbol for symbol in symbols if symbol not in loaded_symbols]

                if data_df.empty:
                    available_hk = db.get_symbols('daily', 'hk')
                    available_us = db.get_symbols('daily', 'us')
                    available_cn = db.get_symbols('daily', 'cn')
                    with state._backtest_lock:
                        state._backtest_results[backtest_id] = {
                            "status": "error",
                            "error": f"No data found in DuckDB for symbols: {missing_symbols}. "
                                     f"Available: {available_hk + available_us + available_cn}",
                            "backtest_id": backtest_id,
                        }
                    return

                from quant.features.backtest.walkforward import DataFrameProvider
                from quant.features.backtest.data_validator import DataValidator

                data_provider = DataFrameProvider(data_df)

                validation_report = DataValidator.validate(data_df)
                if not validation_report.ok:
                    with state._backtest_lock:
                        state._backtest_results[backtest_id] = {
                            "status": "error",
                            "error": f"Data validation failed: {'; '.join(validation_report.errors)}",
                            "backtest_id": backtest_id,
                        }
                    return

                import inspect as _inspect

                registry = StrategyRegistry()
                registry_key = state.STRATEGY_ID_TO_REGISTRY.get(strategy_id, strategy_id)
                strategy_class = registry.get(registry_key)
                if strategy_class is None:
                    sid_norm = strategy_id.lower().replace('_', '').replace('-', '')
                    for name in registry.list_strategies():
                        if name.lower().replace('_', '').replace('-', '') == sid_norm:
                            strategy_class = registry.get(name)
                            break
                if strategy_class is None:
                    with state._backtest_lock:
                        state._backtest_results[backtest_id] = {"status": "error", "error": f"Strategy {strategy_id} not found", "backtest_id": backtest_id}
                    return
                sig = _inspect.signature(strategy_class.__init__)
                accepted = set(list(sig.parameters.keys())[1:])
                strategy_kwargs = {"symbols": symbols}
                if strategy_params:
                    for k, v in strategy_params.items():
                        if k in accepted:
                            strategy_kwargs[k] = v
                strategy = strategy_class(**strategy_kwargs)

                if benchmark_symbol:
                    try:
                        bench_df = db.get_bars(
                            benchmark_symbol,
                            datetime.strptime(start_date, '%Y-%m-%d'),
                            datetime.strptime(end_date, '%Y-%m-%d'),
                            "1d",
                        )
                        if not bench_df.empty:
                            from quant.features.backtest.benchmark import BenchmarkProvider
                            benchmark_provider = BenchmarkProvider(bench_df)
                    except Exception:
                        benchmark_provider = None

                config = {
                    "backtest": {"slippage_bps": slippage_bps},
                    "execution": {"commission": {
                        "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
                        "HK": {"type": "hk_realistic"},
                        "CN": {"type": "cn_realistic"},
                    }},
                    "data": {"default_timeframe": "1d"},
                    "risk": {
                        "max_position_pct": risk_config.get("max_position_pct", 1.0),
                        "max_sector_pct": 1.0,
                        "max_daily_loss_pct": risk_config.get("max_daily_loss_pct", 1.0),
                        "max_leverage": risk_config.get("max_leverage", 10.0),
                        "max_orders_minute": 100,
                    },
                }

                lot_sizes = {s: db.get_lot_size(s) for s in symbols}
            finally:
                db.close()

            backtester = Backtester(config, portfolio_class=Portfolio, risk_engine_class=RiskEngine, sub_portfolio_class=SubPortfolio, lot_sizes=lot_sizes, benchmark_provider=benchmark_provider)
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

            benchmark_curve = []
            if benchmark_provider is not None:
                start_dt = datetime.strptime(start_date, '%Y-%m-%d')
                end_dt = datetime.strptime(end_date, '%Y-%m-%d')
                bench_eq = benchmark_provider.get_benchmark_equity(start_dt, end_dt, initial_cash)
                if not bench_eq.empty:
                    benchmark_curve = [[str(bench_eq.index[0]), float(initial_cash)]]
                    benchmark_curve += [[str(idx), float(v)] for idx, v in bench_eq.items()]

            trades_list = []
            for t in result.trades:
                if t.side != "SELL":
                    continue
                trades_list.append({
                    "entry_time": str(t.entry_time),
                    "exit_time": str(t.exit_time),
                    "symbol": t.symbol,
                    "side": t.side,
                    "status": "closed",
                    "entry_price": float(t.entry_price),
                    "exit_price": float(t.exit_price),
                    "quantity": int(t.quantity),
                    "pnl": float(t.pnl),
                })

            for pos in result.open_positions:
                trades_list.append({
                    "entry_time": str(pos["entry_time"]) if pos.get("entry_time") else "",
                    "exit_time": None,
                    "symbol": pos["symbol"],
                    "side": "BUY",
                    "status": "open",
                    "entry_price": float(pos["entry_price"]),
                    "exit_price": float(pos["current_price"]),
                    "quantity": int(pos["quantity"]),
                    "pnl": float(pos["unrealized_pnl"]),
                })

            timeline_list = []
            position_tracker = {}
            for t in sorted(result.trades, key=lambda x: x.fill_date or x.entry_time):
                if t.side == "BUY":
                    position_tracker[t.symbol] = position_tracker.get(t.symbol, 0) + t.quantity
                else:
                    position_tracker[t.symbol] = position_tracker.get(t.symbol, 0) - t.quantity
                timeline_list.append({
                    "date": str(t.fill_date or t.entry_time),
                    "action": t.side,
                    "symbol": t.symbol,
                    "quantity": int(t.quantity),
                    "price": float(t.fill_price if t.fill_price else (t.entry_price if t.side == "BUY" else t.exit_price)),
                    "position": int(position_tracker.get(t.symbol, 0)),
                    "pnl": float(t.pnl) if t.side == "SELL" else None,
                })

            sell_trades = [t for t in result.trades if t.side == "SELL"]
            winning = [t for t in sell_trades if t.pnl > 0]
            losing = [t for t in sell_trades if t.pnl < 0]
            days = max(1, (datetime.strptime(end_date, '%Y-%m-%d') - datetime.strptime(start_date, '%Y-%m-%d')).days)
            years = days / 365.25
            cagr = float(((1 + result.total_return) ** (1.0 / max(years, 1e-9)) - 1) * 100)

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
                "benchmark_return": float(result.metrics.benchmark_return) if result.metrics.benchmark_return is not None else None,
                "benchmark_return_pct": float(result.metrics.benchmark_return * 100) if result.metrics.benchmark_return is not None else None,
                "alpha": float(result.metrics.alpha) if result.metrics.alpha is not None else None,
                "alpha_pct": float(result.metrics.alpha * 100) if result.metrics.alpha is not None else None,
                "beta": float(result.metrics.beta) if result.metrics.beta is not None else None,
                "information_ratio": float(result.metrics.information_ratio) if result.metrics.information_ratio is not None else None,
            }

            with state._backtest_lock:
                state._backtest_results[backtest_id] = {
                    "status": "completed",
                    "backtest_id": backtest_id,
                    "strategy_id": strategy_id,
                    "metrics": metrics,
                    "equity_curve": equity_serializable,
                    "benchmark_equity_curve": benchmark_curve,
                    "trades": trades_list,
                    "trade_timeline": timeline_list,
                    "description": f"{strategy_id} backtest from {start_date} to {end_date} on {', '.join(symbols)}",
                }
        except Exception as e:
            with state._backtest_lock:
                state._backtest_results[backtest_id] = {"status": "error", "error": str(e), "backtest_id": backtest_id}

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return jsonify({"backtest_id": backtest_id, "status": "running"})


@backtest_bp.route('/api/backtest/result/<backtest_id>')
def get_backtest_result(backtest_id):
    with state._backtest_lock:
        result = state._backtest_results.get(backtest_id)
    if result is None:
        return jsonify({"error": "Backtest not found"}), 404
    return jsonify(result)


@backtest_bp.route('/api/backtest/list')
def list_backtests():
    results = []
    with state._backtest_lock:
        for bid, r in state._backtest_results.items():
            results.append({
                "backtest_id": bid,
                "status": r.get("status"),
                "strategy_id": r.get("strategy_id"),
                "total_return_pct": r.get("metrics", {}).get("total_return_pct"),
                "sharpe_ratio": r.get("metrics", {}).get("sharpe_ratio"),
            })
    return jsonify({"backtests": results})
