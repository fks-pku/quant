#!/usr/bin/env python3
"""API server for Quant Trading System."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, jsonify
try:
    from flask_cors import CORS
except ImportError:
    def CORS(app):
        return app

from quant.api.state.runtime import _load_strategy_state
from quant.api.system_bp import system_bp
from quant.api.strategies_bp import strategies_bp
from quant.api.backtest_bp import backtest_bp
from quant.api.cio_bp import cio_bp
from quant.api.futu_bp import futu_bp
from quant.api.positions_bp import positions_bp
from quant.api.research_bp import research_bp

_HERE = Path(__file__).parent
app = Flask(__name__)
CORS(app)

_load_strategy_state()

app.register_blueprint(system_bp)
app.register_blueprint(strategies_bp)
app.register_blueprint(backtest_bp)
app.register_blueprint(cio_bp)
app.register_blueprint(futu_bp)
app.register_blueprint(positions_bp)
app.register_blueprint(research_bp)


@app.get("/")
def index():
    return jsonify({
        "service": "quant-api",
        "ui": "strategy_dashboard_server",
        "dashboard_command": "python quant/scripts/strategy_dashboard_server.py",
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
