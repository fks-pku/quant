#!/usr/bin/env python3
"""API server for Quant Trading System Dashboard."""

import sys
import json
import threading
import subprocess
from pathlib import Path
from datetime import datetime
from flask import Flask, jsonify, request
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
        'positions': positions_data
    })


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


@app.route('/api/portfolio', methods=['GET'])
def get_portfolio():
    return jsonify(portfolio_data)


@app.route('/api/logs', methods=['GET'])
def get_logs():
    return jsonify([])


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
