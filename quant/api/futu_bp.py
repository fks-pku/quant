import yaml
from pathlib import Path
from flask import Blueprint, jsonify

from quant.api.state import (
    _futu_lock, _futu_broker, _get_futu_broker, _maybe_snapshot,
)
from quant.execution.strategy_position_tracker import get_tracker, DEFAULT_STRATEGY

futu_bp = Blueprint('futu', __name__)


@futu_bp.route('/api/futu/connect', methods=['POST'])
def futu_connect():
    global _futu_broker
    with _futu_lock:
        if _futu_broker is not None and _futu_broker.is_connected():
            return jsonify({'connected': True, 'message': 'Already connected'})
    try:
        from quant.execution.brokers.futu import FutuBroker
        config_path = str(Path(__file__).resolve().parent.parent.parent / 'quant' / 'config' / 'brokers.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        futu_config = config.get('futu', {})
        broker = FutuBroker(
            host=futu_config.get('host', '127.0.0.1'),
            port=futu_config.get('port', 11111),
        )
        broker.connect()
        with _futu_lock:
            _futu_broker = broker
        return jsonify({'connected': True})
    except Exception as e:
        return jsonify({'connected': False, 'error': str(e)}), 500


@futu_bp.route('/api/futu/disconnect', methods=['POST'])
def futu_disconnect():
    global _futu_broker
    with _futu_lock:
        if _futu_broker:
            try:
                _futu_broker.disconnect()
            except Exception:
                pass
            _futu_broker = None
    return jsonify({'disconnected': True})


@futu_bp.route('/api/futu/unlock', methods=['POST'])
def futu_unlock():
    with _futu_lock:
        broker = _get_futu_broker()
    if broker is None:
        return jsonify({'error': 'Not connected'}), 400
    try:
        broker.unlock_trade('')
        return jsonify({'unlocked': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@futu_bp.route('/api/futu/status', methods=['GET'])
def futu_status():
    with _futu_lock:
        broker = _get_futu_broker()
    if broker is None:
        return jsonify({'connected': False, 'unlocked': False})
    return jsonify({
        'connected': broker.is_connected(),
        'unlocked': broker.is_unlocked() if broker.is_connected() else False,
    })


@futu_bp.route('/api/futu/account', methods=['GET'])
def futu_account():
    with _futu_lock:
        broker = _get_futu_broker()
    if broker is None or not broker.is_connected() or not broker.is_unlocked():
        return jsonify({'error': 'Futu not connected/unlocked'}), 400
    try:
        detail = broker.get_account_detail()
        return jsonify(detail)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@futu_bp.route('/api/futu/positions', methods=['GET'])
def futu_positions():
    with _futu_lock:
        broker = _get_futu_broker()
    if broker is None or not broker.is_connected() or not broker.is_unlocked():
        return jsonify({'error': 'Futu not connected/unlocked'}), 400
    try:
        tracker = get_tracker()
        holdings = broker.get_positions_enriched()
        detail = broker.get_account_detail()
        strategy_breakdown = tracker.calibrate(
            [{"symbol": h.get("symbol", ""), "qty": h.get("quantity", 0),
              "nominal_price": h.get("current_price", h.get("nominal_price", 0)),
              "cost_price": h.get("cost_price", h.get("avg_cost", 0))}
             for h in holdings]
        )
        nav = detail.get('total_assets', 0)
        _maybe_snapshot(tracker, nav)
        return jsonify({
            'nav': nav,
            'total_unrealized_pnl': detail.get('unrealized_pl', 0),
            'total_realized_pnl': detail.get('realized_pl', 0),
            'account': detail,
            'hk': detail.get('hk', {}),
            'us': detail.get('us', {}),
            'holdings': holdings,
            'strategy_breakdown': strategy_breakdown,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@futu_bp.route('/api/futu/orders', methods=['GET'])
def futu_orders():
    with _futu_lock:
        broker = _get_futu_broker()
    if broker is None or not broker.is_connected() or not broker.is_unlocked():
        return jsonify({'error': 'Futu not connected/unlocked'}), 400
    try:
        tracker = get_tracker()
        orders = []
        order_list = broker.get_order_list()
        for o in order_list:
            orders.append({
                'order_id': o.order_id,
                'symbol': o.symbol,
                'side': o.side,
                'quantity': o.quantity,
                'price': o.price,
                'status': o.status.name if hasattr(o.status, 'name') else str(o.status),
                'filled_qty': o.filled_qty,
                'avg_fill_price': o.avg_fill_price,
                'time': o.update_time.isoformat() if o.update_time else None,
                'strategy': tracker.get_strategy_for_order(o.order_id),
            })
        try:
            deals_df = broker.get_today_deals()
            if deals_df is not None and not deals_df.empty:
                for _, r in deals_df.iterrows():
                    orders.append({
                        'order_id': str(r.get('deal_id', '')),
                        'symbol': r.get('code', ''),
                        'side': r.get('trd_side', ''),
                        'quantity': float(r.get('qty', 0)),
                        'price': float(r.get('price', 0)),
                        'status': 'DEAL',
                        'filled_qty': float(r.get('qty', 0)),
                        'avg_fill_price': float(r.get('price', 0)),
                        'time': str(r.get('create_time', '')),
                        'strategy': DEFAULT_STRATEGY,
                    })
        except Exception:
            pass
        orders.sort(key=lambda x: x.get('time') or '', reverse=True)
        return jsonify({'orders': orders})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
