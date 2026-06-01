import time
import threading
from flask import Blueprint, jsonify

from quant.api.state import runtime as state

system_bp = Blueprint('system', __name__)


@system_bp.route('/api/status', methods=['GET'])
def get_status():
    return jsonify({
        'status': state.system_status,
        'portfolio': state.portfolio_data,
        'strategies': state.strategies_data,
        'positions': state.positions_data,
        'selected_strategy': state.selected_strategy
    })


@system_bp.route('/api/start', methods=['POST'])
def start_system():
    if state.system_status == 'running':
        return jsonify({'error': 'System already running'}), 400

    state.system_status = 'starting'

    try:
        state.system_thread = threading.Thread(target=state.run_quant_system, daemon=True)
        state.system_thread.start()
        time.sleep(0.5)

        return jsonify({'message': 'System started', 'status': state.system_status})
    except Exception as e:
        state.system_status = 'stopped'
        return jsonify({'error': str(e)}), 500


@system_bp.route('/api/stop', methods=['POST'])
def stop_system():
    if state.system_status != 'running':
        return jsonify({'error': 'System not running'}), 400

    state.system_status = 'stopping'
    state.simulation_running = False

    try:
        if state.system_process:
            state.system_process.terminate()

        return jsonify({'message': 'System stopping', 'status': 'stopped'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        state.system_status = 'stopped'


@system_bp.route('/api/logs', methods=['GET'])
def get_logs():
    return jsonify([])
