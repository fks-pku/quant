from flask import Blueprint, jsonify, request

from quant.api.state.runtime import (
    AVAILABLE_STRATEGIES, selected_strategy, positions_data, system_status,
    _init_default_symbols, _STRATEGY_DEFAULT_SYMBOLS, STRATEGY_PARAMETERS,
    _save_strategy_state, _backtest_results, DOCS_DIR, STRATEGIES_DIR,
    _STRATEGY_DIR_MAP,
)

strategies_bp = Blueprint('strategies', __name__)


@strategies_bp.route('/api/strategies', methods=['GET'])
def get_strategies():
    _init_default_symbols()
    strategies_list = []
    for name, info in AVAILABLE_STRATEGIES.items():
        strategies_list.append({
            'id': info['id'],
            'name': info['name'],
            'description': info['description'],
            'status': info.get('status', 'paused'),
            'enabled': info.get('status', 'paused') == 'active',
            'priority': info['priority'],
            'has_docs': info['doc_file'] is not None,
            'backtest': info['backtest'],
            'default_symbols': _STRATEGY_DEFAULT_SYMBOLS.get(info['id'], 'HK.00700'),
        })
    strategies_list.sort(key=lambda s: s['name'].lower())
    return jsonify({
        'strategies': strategies_list,
        'selected': selected_strategy
    })


@strategies_bp.route('/api/strategies/<strategy_id>', methods=['GET'])
def get_strategy(strategy_id):
    for name, info in AVAILABLE_STRATEGIES.items():
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


@strategies_bp.route('/api/strategies/backtest/<strategy_id>', methods=['GET'])
def get_strategy_backtest(strategy_id):
    for name, info in AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            return jsonify({
                'strategy_id': info['id'],
                'strategy_name': info['name'],
                'backtest': info['backtest']
            })
    return jsonify({'error': 'Strategy not found'}), 404


@strategies_bp.route('/api/strategies/performance/<strategy_id>', methods=['GET'])
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


@strategies_bp.route('/api/strategies/select', methods=['POST'])
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


@strategies_bp.route('/api/strategies/<strategy_id>/pause', methods=['POST'])
def pause_strategy(strategy_id):
    global AVAILABLE_STRATEGIES, _backtest_results
    data = request.get_json() or {}
    flatten = data.get('flatten', True)
    for name, info in AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            info['status'] = 'paused'
            _save_strategy_state()
            msg = f"Strategy {info['name']} paused"
            if flatten:
                msg += " — positions will be flattened"
            return jsonify({'success': True, 'strategy_id': strategy_id, 'status': 'paused', 'message': msg})
    return jsonify({'error': 'Strategy not found'}), 404


@strategies_bp.route('/api/strategies/<strategy_id>/resume', methods=['POST'])
def resume_strategy(strategy_id):
    global AVAILABLE_STRATEGIES
    for name, info in AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            if info.get('status') == 'retired':
                return jsonify({'error': 'Retired strategy must be restored first'}), 400
            info['status'] = 'active'
            _save_strategy_state()
            return jsonify({'success': True, 'strategy_id': strategy_id, 'status': 'active',
                            'message': f"Strategy {info['name']} resumed"})
    return jsonify({'error': 'Strategy not found'}), 404


@strategies_bp.route('/api/strategies/<strategy_id>/retire', methods=['POST'])
def retire_strategy(strategy_id):
    global AVAILABLE_STRATEGIES
    for name, info in AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            info['status'] = 'retired'
            _save_strategy_state()
            return jsonify({'success': True, 'strategy_id': strategy_id, 'status': 'retired',
                            'message': f"Strategy {info['name']} retired"})
    return jsonify({'error': 'Strategy not found'}), 404


@strategies_bp.route('/api/strategies/<strategy_id>/restore', methods=['POST'])
def restore_strategy(strategy_id):
    global AVAILABLE_STRATEGIES
    for name, info in AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            info['status'] = 'paused'
            _save_strategy_state()
            return jsonify({'success': True, 'strategy_id': strategy_id, 'status': 'paused',
                            'message': f"Strategy {info['name']} restored to paused"})
    return jsonify({'error': 'Strategy not found'}), 404


@strategies_bp.route('/api/strategies/<strategy_id>', methods=['DELETE'])
def delete_strategy(strategy_id):
    global AVAILABLE_STRATEGIES, STRATEGY_ID_TO_REGISTRY, STRATEGY_PARAMETERS, _STRATEGY_DEFAULT_SYMBOLS, _backtest_results
    to_delete = None
    for name, info in AVAILABLE_STRATEGIES.items():
        if info['id'] == strategy_id:
            if info.get('status') != 'retired':
                return jsonify({'error': 'Only retired strategies can be permanently deleted'}), 400
            to_delete = name
            break
    if to_delete is None:
        return jsonify({'error': 'Strategy not found'}), 404
    del AVAILABLE_STRATEGIES[to_delete]
    STRATEGY_ID_TO_REGISTRY.pop(strategy_id, None)
    STRATEGY_PARAMETERS.pop(strategy_id, None)
    _STRATEGY_DEFAULT_SYMBOLS.pop(strategy_id, None)
    to_remove = [bid for bid, r in _backtest_results.items() if r.get('strategy_id') == strategy_id]
    for bid in to_remove:
        del _backtest_results[bid]
    _save_strategy_state()
    return jsonify({'success': True, 'deleted': strategy_id, 'backtests_removed': len(to_remove)})


@strategies_bp.route('/api/strategies/<strategy_id>/parameters', methods=['GET'])
def get_strategy_parameters(strategy_id):
    params = STRATEGY_PARAMETERS.get(strategy_id, {})
    return jsonify({
        'strategy_id': strategy_id,
        'parameters': params
    })


@strategies_bp.route('/api/strategies/<strategy_id>/readme', methods=['GET'])
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
