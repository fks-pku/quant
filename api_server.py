#!/usr/bin/env python3
"""API server for Quant Trading System Dashboard."""

import sys
import os
import json
import uuid
import random
import threading
import time
import subprocess
from pathlib import Path
from datetime import datetime
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

sys.path.insert(0, str(Path(__file__).parent / 'system'))

BUILD_DIR = str(Path(__file__).parent / 'frontend' / 'build')
app = Flask(__name__, static_folder=str(Path(__file__).parent / 'frontend' / 'build' / 'static'), static_url_path='/static')
CORS(app)

system_process = None
system_thread = None
system_status = 'stopped'
portfolio_data = {
    'nav': 100000.0,
    'total_unrealized_pnl': 0.0,
    'total_realized_pnl': 0.0
}
strategies_data = []
positions_data = []
selected_strategy = 'VolatilityRegime'
simulation_running = False
_backtest_results = {}
_backtest_lock = threading.Lock()

MOCK_SYMBOLS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'SPY', 'QQQ']
MOCK_PRICES = {
    'AAPL': 178.50, 'MSFT': 378.25, 'GOOGL': 141.80,
    'AMZN': 185.60, 'TSLA': 245.20, 'SPY': 512.30, 'QQQ': 438.50, 'VIX': 14.5
}

_STRATEGY_DIR_MAP = {
    'volatility_regime': 'volatility_regime',
    'simple_momentum': 'simple_momentum',
    'momentum_eod': 'momentum_eod',
    'mean_reversion_1m': 'mean_reversion',
    'dual_thrust': 'dual_thrust',
    'cross_sectional_mean_reversion': 'cross_sectional_mr',
    'dual_momentum': 'dual_momentum',
}

STRATEGIES_DIR = Path(__file__).parent / 'system' / 'quant' / 'strategies'
DOCS_DIR = STRATEGIES_DIR / 'docs'

AVAILABLE_STRATEGIES = {
    'VolatilityRegime': {
        'id': 'volatility_regime',
        'name': 'Volatility Regime',
        'description': 'Regime-based strategy switching based on VIX levels. Switches between momentum and mean reversion sub-strategies depending on market volatility.',
        'enabled': True,
        'priority': 1,
        'doc_file': 'volatility_regime.md',
        'backtest': {
            'sharpe': 0.97,
            'max_dd': 18.2,
            'cagr': 14.5,
            'win_rate': 62.5,
            'period': '2015-2024 OOS',
            'train_sharpe': 1.18,
            'test_sharpe': 0.97,
            'sharpe_degradation': 17.8,
            'pct_profitable': 94.7
        }
    },
    'SimpleMomentum': {
        'id': 'simple_momentum',
        'name': 'Cross-Sectional Momentum',
        'description': 'Long top decile, short bottom decile by momentum score. Monthly rebalancing.',
        'enabled': False,
        'priority': 2,
        'doc_file': 'simple_momentum.md',
        'backtest': {
            'sharpe': 0.78,
            'max_dd': 28.5,
            'cagr': 11.2,
            'win_rate': 55.0,
            'period': '2015-2024 OOS',
            'train_sharpe': 1.05,
            'test_sharpe': 0.78,
            'sharpe_degradation': 25.7,
            'pct_profitable': 85.0
        }
    },
    'MomentumEOD': {
        'id': 'momentum_eod',
        'name': 'Momentum EOD',
        'description': 'Buy top-N S&P 500 gainers at market open, sell at market close. Educational example.',
        'enabled': True,
        'priority': 3,
        'doc_file': 'momentum_eod.md',
        'backtest': {
            'sharpe': 0.45,
            'max_dd': 35.0,
            'cagr': 6.8,
            'win_rate': 52.0,
            'period': '2015-2024 OOS',
            'train_sharpe': 0.85,
            'test_sharpe': 0.45,
            'sharpe_degradation': 47.1,
            'pct_profitable': 65.0
        }
    },
    'MeanReversion1m': {
        'id': 'mean_reversion_1m',
        'name': 'Mean Reversion (1m)',
        'description': 'RSI-based mean reversion on 1-minute data. Educational example.',
        'enabled': True,
        'priority': 4,
        'doc_file': 'mean_reversion.md',
        'backtest': {
            'sharpe': 0.35,
            'max_dd': 42.0,
            'cagr': 4.5,
            'win_rate': 48.0,
            'period': '2015-2024 OOS',
            'train_sharpe': 0.72,
            'test_sharpe': 0.35,
            'sharpe_degradation': 51.4,
            'pct_profitable': 55.0
        }
    },
    'DualThrust': {
        'id': 'dual_thrust',
        'name': 'Dual Thrust',
        'description': 'Classic break-out system adapted for futures. Reference implementation.',
        'enabled': False,
        'priority': 5,
        'doc_file': 'dual_thrust.md',
        'backtest': {
            'sharpe': 0.55,
            'max_dd': 22.0,
            'cagr': 8.5,
            'win_rate': 58.0,
            'period': '2015-2024 OOS',
            'train_sharpe': 0.95,
            'test_sharpe': 0.55,
            'sharpe_degradation': 42.1,
            'pct_profitable': 72.0
        }
    },
    'CrossSectionalMeanReversion': {
        'id': 'cross_sectional_mean_reversion',
        'name': 'Cross-Sectional Mean Reversion',
        'description': 'Short-term mean reversion strategy based on cross-sectional z-scores.',
        'enabled': False,
        'priority': 6,
        'doc_file': 'cross_sectional_mean_reversion.md',
        'backtest': {
            'sharpe': 0.65,
            'max_dd': 25.0,
            'cagr': 9.0,
            'win_rate': 54.0,
            'period': '2015-2024 OOS',
            'train_sharpe': 0.90,
            'test_sharpe': 0.65,
            'sharpe_degradation': 27.8,
            'pct_profitable': 78.0
        }
    },
    'DualMomentum': {
        'id': 'dual_momentum',
        'name': 'Dual Momentum',
        'description': 'Absolute and relative momentum for tactical asset allocation.',
        'enabled': False,
        'priority': 7,
        'doc_file': 'dual_momentum.md',
        'backtest': {
            'sharpe': 0.72,
            'max_dd': 20.0,
            'cagr': 10.5,
            'win_rate': 60.0,
            'period': '2015-2024 OOS',
            'train_sharpe': 1.00,
            'test_sharpe': 0.72,
            'sharpe_degradation': 28.0,
            'pct_profitable': 80.0
        }
    }
}


def simulation_loop():
    global simulation_running, portfolio_data, positions_data

    positions = {}
    symbols_to_trade = ['AAPL', 'MSFT', 'GOOGL']

    while simulation_running:
        for sym in list(MOCK_PRICES.keys()):
            change_pct = random.gauss(0, 0.3) / 100
            MOCK_PRICES[sym] = round(MOCK_PRICES[sym] * (1 + change_pct), 2)

        if random.random() < 0.15 and len(positions) < 5:
            sym = random.choice(symbols_to_trade)
            if sym not in positions:
                qty = random.randint(10, 100)
                price = MOCK_PRICES.get(sym, 100)
                positions[sym] = {'symbol': sym, 'quantity': qty, 'avg_price': price}

        unrealized_pnl = 0.0
        holdings = []
        for sym, pos in positions.items():
            current = MOCK_PRICES.get(sym, pos['avg_price'])
            pnl = (current - pos['avg_price']) * pos['quantity']
            unrealized_pnl += pnl
            holdings.append({
                'symbol': sym,
                'quantity': pos['quantity'],
                'avg_price': pos['avg_price'],
                'current_price': current,
                'pnl': round(pnl, 2)
            })

        portfolio_data = {
            'nav': round(100000.0 + unrealized_pnl, 2),
            'total_unrealized_pnl': round(unrealized_pnl, 2),
            'total_realized_pnl': 0.0
        }
        positions_data = holdings

        time.sleep(3)


def run_quant_system():
    global system_status, portfolio_data, strategies_data, positions_data, simulation_running

    system_status = 'running'
    simulation_running = True

    strategies_data = [
        {'name': 'VolatilityRegime', 'enabled': True, 'symbols': ['AAPL', 'MSFT', 'GOOGL']},
        {'name': 'MomentumEOD', 'enabled': True, 'symbols': ['AAPL', 'MSFT', 'GOOGL']},
        {'name': 'MeanReversion1m', 'enabled': True, 'symbols': ['SPY', 'QQQ']},
        {'name': 'DualThrust', 'enabled': False, 'symbols': ['ES']},
    ]

    try:
        from quant.quant_system import QuantSystem
        quant = QuantSystem()
        quant.initialize()
        quant.run('paper')
    except Exception as e:
        print(f"Quant system unavailable ({e}), running in simulation mode")
        sim_thread = threading.Thread(target=simulation_loop, daemon=True)
        sim_thread.start()
        try:
            while simulation_running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        simulation_running = False
        system_status = 'stopped'
        positions_data = []
        strategies_data = []
        portfolio_data = {
            'nav': 100000.0,
            'total_unrealized_pnl': 0.0,
            'total_realized_pnl': 0.0
        }


@app.route('/api/status', methods=['GET'])
def get_status():
    return jsonify({
        'status': system_status,
        'portfolio': portfolio_data,
        'strategies': strategies_data,
        'positions': positions_data,
        'selected_strategy': selected_strategy
    })


@app.route('/api/strategies', methods=['GET'])
def get_strategies():
    strategies_list = []
    for name, info in AVAILABLE_STRATEGIES.items():
        strategies_list.append({
            'id': info['id'],
            'name': info['name'],
            'description': info['description'],
            'enabled': info['enabled'],
            'priority': info['priority'],
            'has_docs': info['doc_file'] is not None,
            'backtest': info['backtest']
        })
    return jsonify({
        'strategies': strategies_list,
        'selected': selected_strategy
    })


@app.route('/api/strategies/<strategy_id>', methods=['GET'])
def get_strategy(strategy_id):
    for name, info in AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            return jsonify({
                'id': info['id'],
                'name': info['name'],
                'description': info['description'],
                'enabled': info['enabled'],
                'priority': info['priority'],
                'has_docs': info['doc_file'] is not None,
                'backtest': info['backtest']
            })
    return jsonify({'error': 'Strategy not found'}), 404


@app.route('/api/strategies/docs/<strategy_id>', methods=['GET'])
def get_strategy_docs(strategy_id):
    for name, info in AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            if info['doc_file'] is None:
                return jsonify({'error': 'No documentation available for this strategy'}), 404

            doc_path = DOCS_DIR / info['doc_file']
            if not doc_path.exists():
                return jsonify({'error': 'Documentation file not found'}), 404

            with open(doc_path, 'r') as f:
                content = f.read()

            return jsonify({
                'strategy_id': info['id'],
                'strategy_name': info['name'],
                'content': content,
                'format': 'markdown'
            })
    return jsonify({'error': 'Strategy not found'}), 404


@app.route('/api/strategies/backtest/<strategy_id>', methods=['GET'])
def get_strategy_backtest(strategy_id):
    for name, info in AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            return jsonify({
                'strategy_id': info['id'],
                'strategy_name': info['name'],
                'backtest': info['backtest']
            })
    return jsonify({'error': 'Strategy not found'}), 404


@app.route('/api/strategies/performance/<strategy_id>', methods=['GET'])
def get_strategy_performance(strategy_id):
    for name, info in AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            bt = info['backtest']
            return jsonify({
                'strategy_id': info['id'],
                'strategy_name': info['name'],
                'description': info['description'],
                'backtest': bt,
                'performance': {
                    'sharpe_ratio': bt['test_sharpe'],
                    'max_drawdown': bt['max_dd'],
                    'cagr': bt['cagr'],
                    'win_rate': bt['win_rate'],
                },
                'pnl_curve': [],
                'recent_trades': [],
                'positions': [p for p in positions_data if p.get('symbol') in info.get('symbols', [])] if system_status == 'running' else []
            })
    return jsonify({'error': 'Strategy not found'}), 404


@app.route('/api/strategies/select', methods=['POST'])
def select_strategy():
    global selected_strategy

    data = request.get_json()
    strategy_id = data.get('strategy_id')

    for name, info in AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            selected_strategy = name
            return jsonify({
                'success': True,
                'selected_strategy': name,
                'message': f'Strategy changed to {info["name"]}'
            })

    return jsonify({'error': 'Strategy not found'}), 404


@app.route('/api/start', methods=['POST'])
def start_system():
    global system_process, system_thread, system_status

    if system_status == 'running':
        return jsonify({'error': 'System already running'}), 400

    system_status = 'starting'

    try:
        system_thread = threading.Thread(target=run_quant_system, daemon=True)
        system_thread.start()
        time.sleep(0.5)

        return jsonify({'message': 'System started', 'status': system_status})
    except Exception as e:
        system_status = 'stopped'
        return jsonify({'error': str(e)}), 500


@app.route('/api/stop', methods=['POST'])
def stop_system():
    global system_process, system_status, simulation_running

    if system_status != 'running':
        return jsonify({'error': 'System not running'}), 400

    system_status = 'stopping'
    simulation_running = False

    try:
        if system_process:
            system_process.terminate()

        return jsonify({'message': 'System stopping', 'status': 'stopped'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        system_status = 'stopped'


orders_data = []


@app.route('/api/orders', methods=['POST'])
def create_order():
    global orders_data, portfolio_data, positions_data

    data = request.get_json()
    symbol = data.get('symbol', '').upper()
    quantity = data.get('quantity', 0)
    side = data.get('side', 'BUY')
    broker = data.get('broker', 'paper')

    price = MOCK_PRICES.get(symbol, round(random.uniform(50, 500), 2))
    status = 'FILLED'

    order = {
        'id': len(orders_data) + 1,
        'symbol': symbol,
        'quantity': quantity,
        'side': side,
        'price': price,
        'broker': broker,
        'status': status,
        'time': datetime.now().isoformat()
    }
    orders_data.append(order)

    return jsonify(order)


@app.route('/api/orders', methods=['GET'])
def get_orders():
    return jsonify(orders_data[-20:])


@app.route('/api/market', methods=['GET'])
def get_market_data():
    data = []
    for sym in ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'VIX']:
        price = MOCK_PRICES.get(sym, 100)
        change = round(random.uniform(-2, 2), 1)
        entry = {'symbol': sym, 'price': price, 'change': change}
        if sym == 'VIX':
            entry['sma'] = 16.2
        data.append(entry)
    return jsonify(data)


@app.route('/api/portfolio', methods=['GET'])
def get_portfolio():
    return jsonify(portfolio_data)


@app.route('/api/logs', methods=['GET'])
def get_logs():
    return jsonify([])


@app.route('/api/backtest/run', methods=['POST'])
def run_backtest():
    data = request.json
    strategy_id = data.get('strategy_id', 'SimpleMomentum')
    start_date = data.get('start_date', '2020-01-01')
    end_date = data.get('end_date', '2024-12-31')
    symbols = data.get('symbols', ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'SPY'])
    initial_cash = data.get('initial_cash', 100000)
    slippage_bps = data.get('slippage_bps', 5)

    backtest_id = str(uuid.uuid4())[:8]
    with _backtest_lock:
        _backtest_results[backtest_id] = {"status": "running", "backtest_id": backtest_id}

    def _run():
        try:
            import pandas as pd
            from quant.data.providers.yfinance_provider import YfinanceProvider
            from quant.core.backtester import Backtester
            from quant.strategies.registry import StrategyRegistry
            from quant.strategies.implementations import (
                VolatilityRegime, SimpleMomentum,
                CrossSectionalMeanReversion, DualMomentum,
            )
            from quant.core.walkforward import _DataFrameProvider

            provider = YfinanceProvider()
            provider.connect()

            all_data = []
            for symbol in symbols:
                bars = provider.get_bars(
                    symbol,
                    datetime.strptime(start_date, '%Y-%m-%d'),
                    datetime.strptime(end_date, '%Y-%m-%d'),
                    "1d",
                )
                if not bars.empty:
                    bars = bars.reset_index()
                    bars['symbol'] = symbol
                    col_map = {'Date': 'timestamp', 'index': 'timestamp'}
                    bars = bars.rename(columns=col_map)
                    cols = [c for c in ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'symbol'] if c in bars.columns]
                    all_data.append(bars[cols])

            if not all_data:
                with _backtest_lock:
                    _backtest_results[backtest_id] = {"status": "error", "error": "No data downloaded", "backtest_id": backtest_id}
                return

            data_df = pd.concat(all_data, ignore_index=True)
            data_provider = _DataFrameProvider(data_df)

            registry = StrategyRegistry()
            strategy_class = registry.get(strategy_id)
            if strategy_class is None:
                with _backtest_lock:
                    _backtest_results[backtest_id] = {"status": "error", "error": f"Strategy {strategy_id} not found", "backtest_id": backtest_id}
                return
            strategy = strategy_class(symbols=symbols)

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


@app.route('/api/backtest/result/<backtest_id>')
def get_backtest_result(backtest_id):
    with _backtest_lock:
        result = _backtest_results.get(backtest_id)
    if result is None:
        return jsonify({"error": "Backtest not found"}), 404
    return jsonify(result)


@app.route('/api/backtest/list')
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


# ─── CIO Module ───────────────────────────────────────────────────────────────

_cio_engine = None


def _get_cio_engine():
    global _cio_engine
    if _cio_engine is None:
        from quant.cio.cio_engine import CIOEngine
        from quant.cio.market_assessor import MarketAssessor
        from quant.cio.news_analyzer import NewsAnalyzer
        from quant.cio.weight_allocator import WeightAllocator

        assessor = MarketAssessor()
        news_analyzer = NewsAnalyzer(provider="openai")
        allocator = WeightAllocator()
        _cio_engine = CIOEngine(
            assessor=assessor,
            news_analyzer=news_analyzer,
            allocator=allocator,
        )
    return _cio_engine


@app.route('/api/cio/assessment', methods=['GET'])
def get_cio_assessment():
    engine = _get_cio_engine()

    indicators = {
        "vix": MOCK_PRICES.get("VIX", 14.5),
        "vix_percentile": 22.0,
        "trend_strength": 0.72,
        "market_breadth": 0.65,
    }

    strategy_ids = [info["id"] for info in AVAILABLE_STRATEGIES.values()]

    result = engine.assess(indicators=indicators, enabled_strategies=strategy_ids)
    return jsonify(result)


@app.route('/api/cio/refresh', methods=['POST'])
def refresh_cio_assessment():
    engine = _get_cio_engine()
    data = request.get_json() or {}
    news_text = data.get("news_text") if data else None

    indicators = {
        "vix": MOCK_PRICES.get("VIX", 14.5),
        "vix_percentile": 22.0,
        "trend_strength": 0.72,
        "market_breadth": 0.65,
    }

    strategy_ids = [info["id"] for info in AVAILABLE_STRATEGIES.values()]

    result = engine.assess(indicators=indicators, news_text=news_text, enabled_strategies=strategy_ids)
    return jsonify({"success": True, "assessment": result})


# ─── Strategy Pool ────────────────────────────────────────────────────────────

@app.route('/api/strategy-pool', methods=['GET'])
def get_strategy_pool():
    total_nav = portfolio_data.get("nav", 100000.0)

    engine = _get_cio_engine()
    cached = engine.get_cached()

    pool = []
    for name, info in AVAILABLE_STRATEGIES.items():
        strat_id = info["id"]
        weight = 0.0
        pnl = 0.0

        if cached and "weights" in cached:
            weight = cached["weights"].get(strat_id, 0.0)

        allocated = total_nav * weight

        pool.append({
            "id": strat_id,
            "name": info["name"],
            "enabled": info["enabled"],
            "weight": weight,
            "allocated_capital": round(allocated, 2),
            "current_pnl": round(pnl, 2),
            "backtest_sharpe": info.get("backtest", {}).get("test_sharpe", 0.0),
            "has_readme": info.get("doc_file") is not None,
        })

    return jsonify({
        "total_capital": total_nav,
        "strategies": pool,
    })


@app.route('/api/strategy-pool/weights', methods=['POST'])
def update_strategy_weights():
    global portfolio_data

    data = request.get_json()
    manual_weights = data.get("weights", {})

    total = sum(manual_weights.values())
    if abs(total - 1.0) > 0.001:
        return jsonify({"error": "Weights must sum to 1.0"}), 400

    return jsonify({"success": True, "weights": manual_weights})


@app.route('/api/strategies/<strategy_id>/readme', methods=['GET'])
def get_strategy_readme(strategy_id):
    dir_name = _STRATEGY_DIR_MAP.get(strategy_id)
    if dir_name:
        new_readme = STRATEGIES_DIR / dir_name / 'README.md'
        if new_readme.exists():
            for name, info in AVAILABLE_STRATEGIES.items():
                if info["id"] == strategy_id:
                    with open(new_readme, "r") as f:
                        content = f.read()
                    return jsonify({
                        "strategy_id": info["id"],
                        "strategy_name": info["name"],
                        "content": content,
                        "format": "markdown",
                    })

    for name, info in AVAILABLE_STRATEGIES.items():
        if info["id"] == strategy_id:
            if info.get("doc_file") is not None:
                doc_path = DOCS_DIR / info["doc_file"]
                if doc_path.exists():
                    with open(doc_path, "r") as f:
                        content = f.read()
                    return jsonify({
                        "strategy_id": info["id"],
                        "strategy_name": info["name"],
                        "content": content,
                        "format": "markdown",
                    })
            return jsonify({"error": "No documentation available"}), 404

    return jsonify({"error": "Strategy not found"}), 404


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_frontend(path):
    build_dir = Path(__file__).parent / 'frontend' / 'build'
    if path and (build_dir / path).exists():
        return send_file(str(build_dir / path))
    return send_file(str(build_dir / 'index.html'))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
