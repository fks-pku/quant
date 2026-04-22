import random
from datetime import datetime
from flask import Blueprint, jsonify, request

from quant.api.state.runtime import (
    portfolio_data, positions_data, MOCK_PRICES, orders_data,
)
from quant.features.portfolio.tracker import get_tracker

positions_bp = Blueprint('positions', __name__)


@positions_bp.route('/api/portfolio', methods=['GET'])
def get_portfolio():
    return jsonify(portfolio_data)


@positions_bp.route('/api/orders', methods=['POST'])
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


@positions_bp.route('/api/orders', methods=['GET'])
def get_orders():
    return jsonify(orders_data[-20:])


@positions_bp.route('/api/market', methods=['GET'])
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


@positions_bp.route('/api/data/symbols', methods=['GET'])
def list_data_symbols():
    try:
        from quant.infrastructure.data.storage_duckdb import DuckDBStorage
        db = DuckDBStorage(read_only=True)
        result = {}
        for market in ("hk", "us", "cn"):
            for freq in ("daily", "minute"):
                table = f"{freq}_{market}"
                symbols = db.get_symbols(freq, market)
                for sym in symbols:
                    key = f"{table}:{sym}"
                    r = db.get_date_range(sym, freq)
                    result[key] = {
                        "symbol": sym,
                        "table": table,
                        "frequency": freq,
                        "market": market,
                        "start": r["start"].isoformat() if r else None,
                        "end": r["end"].isoformat() if r else None,
                        "rows": db.table_row_count(table),
                    }
        db.close()
        return jsonify({"symbols": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@positions_bp.route('/api/strategy-positions', methods=['GET'])
def strategy_positions():
    return jsonify(get_tracker().get_breakdown())


@positions_bp.route('/api/strategy/<name>/history', methods=['GET'])
def strategy_history(name):
    try:
        from quant.infrastructure.data.storage_duckdb import DuckDBStorage
        db = DuckDBStorage(read_only=True)
        snapshots = db.get_strategy_snapshots(strategy_name=name)
        return jsonify({"strategy": name, "snapshots": snapshots})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@positions_bp.route('/api/strategy/all-history', methods=['GET'])
def all_strategy_history():
    try:
        from quant.infrastructure.data.storage_duckdb import DuckDBStorage
        db = DuckDBStorage(read_only=True)
        snapshots = db.get_strategy_snapshots()
        by_strategy = {}
        for s in snapshots:
            by_strategy.setdefault(s["strategy_name"], []).append(s)
        return jsonify(by_strategy)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
