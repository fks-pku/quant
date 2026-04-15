#!/usr/bin/env python3
"""API server for Quant Trading System Dashboard."""

import sys
import json
import threading
import subprocess
from pathlib import Path
from datetime import datetime
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

sys.path.insert(0, str(Path(__file__).parent / 'system'))

app = Flask(__name__)
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
        'doc_file': None,
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
        'doc_file': None,
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
        'doc_file': None,
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
    }
}


class MockPortfolio:
    def to_dict(self):
        return {
            'nav': 100000.0,
            'total_unrealized_pnl': 0.0,
            'total_realized_pnl': 0.0
        }


def run_quant_system():
    global system_status, portfolio_data, strategies_data, positions_data
    
    system_status = 'running'
    
    try:
        from quant.quant_system import QuantSystem
        
        quant = QuantSystem()
        quant.initialize()
        
        strategies_data = [
            {'name': 'MomentumEOD', 'enabled': True, 'symbols': ['AAPL', 'MSFT', 'GOOGL']},
            {'name': 'MeanReversion1m', 'enabled': True, 'symbols': ['SPY', 'QQQ']},
            {'name': 'DualThrust', 'enabled': False, 'symbols': ['ES']},
        ]
        
        quant.run('paper')
        
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"System error: {e}")
    finally:
        system_status = 'stopped'


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
        
        strategies_data = [
            {'name': 'MomentumEOD', 'enabled': True, 'symbols': ['AAPL', 'MSFT', 'GOOGL']},
            {'name': 'MeanReversion1m', 'enabled': True, 'symbols': ['SPY', 'QQQ']},
            {'name': 'DualThrust', 'enabled': False, 'symbols': ['ES']},
        ]
        
        return jsonify({'message': 'System started', 'status': 'running'})
    except Exception as e:
        system_status = 'stopped'
        return jsonify({'error': str(e)}), 500


@app.route('/api/stop', methods=['POST'])
def stop_system():
    global system_process, system_status
    
    if system_status != 'running':
        return jsonify({'error': 'System not running'}), 400
    
    system_status = 'stopping'
    
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
    global orders_data
    
    data = request.get_json()
    order = {
        'id': len(orders_data) + 1,
        'symbol': data.get('symbol'),
        'quantity': data.get('quantity'),
        'side': data.get('side'),
        'broker': data.get('broker', 'paper'),
        'status': 'FILLED',
        'time': datetime.now().isoformat()
    }
    orders_data.append(order)
    return jsonify(order)


@app.route('/api/orders', methods=['GET'])
def get_orders():
    return jsonify(orders_data[-20:])


market_data = [
    {'symbol': 'AAPL', 'price': 178.50, 'change': 1.2},
    {'symbol': 'MSFT', 'price': 378.25, 'change': -0.5},
    {'symbol': 'VIX', 'price': 14.5, 'sma': 16.2},
]

@app.route('/api/market', methods=['GET'])
def get_market_data():
    return jsonify(market_data)


@app.route('/api/portfolio', methods=['GET'])
def get_portfolio():
    return jsonify(portfolio_data)


@app.route('/api/logs', methods=['GET'])
def get_logs():
    return jsonify([])


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
