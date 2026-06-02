from datetime import date

from flask import Blueprint, jsonify, request

from quant.api.state import runtime as state
from quant.infrastructure.execution.live_recorder import get_live_recorder

strategies_bp = Blueprint('strategies', __name__)


@strategies_bp.route('/api/strategies', methods=['GET'])
def get_strategies():
    state._init_default_symbols()
    strategies_list = []
    for name, info in state.AVAILABLE_STRATEGIES.items():
        strategies_list.append({
            'id': info['id'],
            'name': info['name'],
            'description': info['description'],
            'status': info.get('status', 'paused'),
            'enabled': info.get('status', 'paused') == 'active',
            'priority': info['priority'],
            'has_docs': info['doc_file'] is not None,
            'backtest': info['backtest'],
            'default_symbols': state._STRATEGY_DEFAULT_SYMBOLS.get(info['id'], 'HK.00700'),
        })
    strategies_list.sort(key=lambda s: s['name'].lower())
    return jsonify({
        'strategies': strategies_list,
        'selected': state.selected_strategy
    })


@strategies_bp.route('/api/strategies/<strategy_id>', methods=['GET'])
def get_strategy(strategy_id):
    for name, info in state.AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            return jsonify({
                'id': info['id'],
                'name': info['name'],
                'description': info['description'],
                'status': info.get('status', 'paused'),
                'enabled': info.get('status', 'paused') == 'active',
                'priority': info['priority'],
                'has_docs': info['doc_file'] is not None,
                'backtest': info['backtest']
            })
    return jsonify({'error': 'Strategy not found'}), 404


@strategies_bp.route('/api/strategies/docs/<strategy_id>', methods=['GET'])
def get_strategy_docs(strategy_id):
    for name, info in state.AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            if info['doc_file'] is None:
                return jsonify({'error': 'No documentation available for this strategy'}), 404

            doc_path = state.DOCS_DIR / info['doc_file']
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


@strategies_bp.route('/api/strategies/backtest/<strategy_id>', methods=['GET'])
def get_strategy_backtest(strategy_id):
    for name, info in state.AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            return jsonify({
                'strategy_id': info['id'],
                'strategy_name': info['name'],
                'backtest': info['backtest']
            })
    return jsonify({'error': 'Strategy not found'}), 404


@strategies_bp.route('/api/strategies/performance/<strategy_id>', methods=['GET'])
def get_strategy_performance(strategy_id):
    for name, info in state.AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            bt = info['backtest']
            live_perf = get_live_recorder().get_strategy_performance(info['name'])
            has_live_data = bool(live_perf['pnl_curve']) or live_perf['total_trades'] > 0
            return jsonify({
                'strategy_id': info['id'],
                'strategy_name': info['name'],
                'description': info['description'],
                'backtest': bt,
                'performance': {
                    'sharpe_ratio': live_perf['sharpe_ratio'] if has_live_data else bt['test_sharpe'],
                    'max_drawdown': live_perf['max_drawdown'] if has_live_data else bt['max_dd'],
                    'cagr': bt['cagr'],
                    'win_rate': live_perf['win_rate'] if has_live_data else bt['win_rate'],
                    'total_pnl': live_perf['total_pnl'],
                    'realized_pnl': live_perf['realized_pnl'],
                    'unrealized_pnl': live_perf['unrealized_pnl'],
                    'total_trades': live_perf['total_trades'],
                    'profit_factor': live_perf['profit_factor'],
                },
                'pnl_curve': live_perf['pnl_curve'],
                'recent_trades': live_perf['recent_trades'],
                'latest_snapshot': live_perf['latest_snapshot'],
                'positions': [p for p in state.positions_data if p.get('symbol') in info.get('symbols', [])] if state.system_status == 'running' else []
            })
    return jsonify({'error': 'Strategy not found'}), 404


@strategies_bp.route('/api/strategies/live-records/<kind>', methods=['GET'])
def get_strategy_live_records(kind):
    trading_date = request.args.get('date') or date.today().isoformat()
    strategy_name = request.args.get('strategy_name')
    strategy_id = request.args.get('strategy_id')
    if strategy_id and not strategy_name:
        for _name, info in state.AVAILABLE_STRATEGIES.items():
            if info['id'] == strategy_id:
                strategy_name = info['name']
                break
    try:
        records = get_live_recorder().read_day(kind, trading_date, strategy_name=strategy_name)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({
        'date': trading_date,
        'kind': kind,
        'strategy_name': strategy_name,
        'records': records,
    })


@strategies_bp.route('/api/strategies/select', methods=['POST'])
def select_strategy():
    data = request.get_json() or {}
    strategy_id = data.get('strategy_id')

    for name, info in state.AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            state.selected_strategy = name
            return jsonify({
                'success': True,
                'selected_strategy': name,
                'message': f'Strategy changed to {info["name"]}'
            })

    return jsonify({'error': 'Strategy not found'}), 404


@strategies_bp.route('/api/strategies/<strategy_id>/pause', methods=['POST'])
def pause_strategy(strategy_id):
    data = request.get_json() or {}
    flatten = data.get('flatten', True)
    for name, info in state.AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            info['status'] = 'paused'
            state._save_strategy_state()
            msg = f"Strategy {info['name']} paused"
            if flatten:
                msg += " — positions will be flattened"
            return jsonify({'success': True, 'strategy_id': strategy_id, 'status': 'paused', 'message': msg})
    return jsonify({'error': 'Strategy not found'}), 404


@strategies_bp.route('/api/strategies/<strategy_id>/resume', methods=['POST'])
def resume_strategy(strategy_id):
    for name, info in state.AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            if info.get('status') == 'retired':
                return jsonify({'error': 'Retired strategy must be restored first'}), 400
            info['status'] = 'active'
            state._save_strategy_state()
            return jsonify({'success': True, 'strategy_id': strategy_id, 'status': 'active',
                            'message': f"Strategy {info['name']} resumed"})
    return jsonify({'error': 'Strategy not found'}), 404


@strategies_bp.route('/api/strategies/<strategy_id>/retire', methods=['POST'])
def retire_strategy(strategy_id):
    for name, info in state.AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            info['status'] = 'retired'
            state._save_strategy_state()
            return jsonify({'success': True, 'strategy_id': strategy_id, 'status': 'retired',
                            'message': f"Strategy {info['name']} retired"})
    return jsonify({'error': 'Strategy not found'}), 404


@strategies_bp.route('/api/strategies/<strategy_id>/restore', methods=['POST'])
def restore_strategy(strategy_id):
    for name, info in state.AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            info['status'] = 'paused'
            state._save_strategy_state()
            return jsonify({'success': True, 'strategy_id': strategy_id, 'status': 'paused',
                            'message': f"Strategy {info['name']} restored to paused"})
    return jsonify({'error': 'Strategy not found'}), 404


@strategies_bp.route('/api/strategies/<strategy_id>', methods=['DELETE'])
def delete_strategy(strategy_id):
    to_delete = None
    for name, info in state.AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            if info.get('status') != 'retired':
                return jsonify({'error': 'Only retired strategies can be permanently deleted'}), 400
            to_delete = name
            break
    if to_delete is None:
        return jsonify({'error': 'Strategy not found'}), 404
    del state.AVAILABLE_STRATEGIES[to_delete]
    state.STRATEGY_ID_TO_REGISTRY.pop(strategy_id, None)
    state.STRATEGY_PARAMETERS.pop(strategy_id, None)
    state._STRATEGY_DEFAULT_SYMBOLS.pop(strategy_id, None)
    to_remove = [bid for bid, r in state._backtest_results.items() if r.get('strategy_id') == strategy_id]
    for bid in to_remove:
        del state._backtest_results[bid]
    state._save_strategy_state()
    return jsonify({'success': True, 'deleted': strategy_id, 'backtests_removed': len(to_remove)})


@strategies_bp.route('/api/strategies/<strategy_id>/parameters', methods=['GET'])
def get_strategy_parameters(strategy_id):
    params = state.STRATEGY_PARAMETERS.get(strategy_id, {})
    return jsonify({
        'strategy_id': strategy_id,
        'parameters': params
    })


@strategies_bp.route('/api/strategies/<strategy_id>/readme', methods=['GET'])
def get_strategy_readme(strategy_id):
    dir_name = state._STRATEGY_DIR_MAP.get(strategy_id)
    if dir_name:
        new_readme = state.STRATEGIES_DIR / dir_name / 'README.md'
        if new_readme.exists():
            for name, info in state.AVAILABLE_STRATEGIES.items():
                if info["id"] == strategy_id:
                    with open(new_readme, "r") as f:
                        content = f.read()
                    return jsonify({
                        "strategy_id": info["id"],
                        "strategy_name": info["name"],
                        "content": content,
                        "format": "markdown",
                    })

    for name, info in state.AVAILABLE_STRATEGIES.items():
        if info["id"] == strategy_id:
            if info.get("doc_file") is not None:
                doc_path = state.DOCS_DIR / info["doc_file"]
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
