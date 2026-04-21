import time
import threading
from flask import Blueprint, jsonify

from quant.api.state.runtime import (
    system_status, portfolio_data, strategies_data, positions_data,
    selected_strategy, system_process, system_thread, simulation_running,
    run_quant_system,
)

system_bp = Blueprint('system', __name__)


@system_bp.route('/api/status', methods=['GET'])
def get_status():
    return jsonify({
        'status': system_status,
        'portfolio': portfolio_data,
        'strategies': strategies_data,
        'positions': positions_data,
        'selected_strategy': selected_strategy
    })


@system_bp.route('/api/start', methods=['POST'])
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


@system_bp.route('/api/stop', methods=['POST'])
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


@system_bp.route('/api/logs', methods=['GET'])
def get_logs():
    return jsonify([])
