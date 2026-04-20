from flask import Blueprint, jsonify, request

from quant.api.state import (
    AVAILABLE_STRATEGIES, MOCK_PRICES, portfolio_data,
    STRATEGY_PARAMETERS, _get_cio_engine,
)

cio_bp = Blueprint('cio', __name__)


@cio_bp.route('/api/cio/assessment', methods=['GET'])
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


@cio_bp.route('/api/cio/refresh', methods=['POST'])
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


@cio_bp.route('/api/strategy-pool', methods=['GET'])
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

        params = STRATEGY_PARAMETERS.get(strat_id, {})
        param_values = {k: v['default'] for k, v in params.items()}

        pool.append({
            "id": strat_id,
            "name": info["name"],
            "enabled": info.get("status", "paused") == "active",
            "weight": weight,
            "allocated_capital": round(allocated, 2),
            "current_pnl": round(pnl, 2),
            "backtest": info.get("backtest", {}),
            "backtest_sharpe": info.get("backtest", {}).get("test_sharpe", 0.0),
            "has_readme": info.get("doc_file") is not None,
            "parameters": param_values,
        })

    return jsonify({
        "total_capital": total_nav,
        "strategies": pool,
    })


@cio_bp.route('/api/strategy-pool/weights', methods=['POST'])
def update_strategy_weights():
    global portfolio_data

    data = request.get_json()
    manual_weights = data.get("weights", {})

    total = sum(manual_weights.values())
    if abs(total - 1.0) > 0.001:
        return jsonify({"error": "Weights must sum to 1.0"}), 400

    return jsonify({"success": True, "weights": manual_weights})
