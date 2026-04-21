#!/usr/bin/env python3
"""API server for Quant Trading System Dashboard."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, send_file
from flask_cors import CORS

from quant.api.state.runtime import _load_strategy_state
from quant.api.system_bp import system_bp
from quant.api.strategies_bp import strategies_bp
from quant.api.backtest_bp import backtest_bp
from quant.api.cio_bp import cio_bp
from quant.api.futu_bp import futu_bp
from quant.api.positions_bp import positions_bp

_HERE = Path(__file__).parent
BUILD_DIR = str(_HERE / 'frontend' / 'build')
app = Flask(__name__, static_folder=str(_HERE / 'frontend' / 'build' / 'static'), static_url_path='/static')
CORS(app)

_load_strategy_state()

app.register_blueprint(system_bp)
app.register_blueprint(strategies_bp)
app.register_blueprint(backtest_bp)
app.register_blueprint(cio_bp)
app.register_blueprint(futu_bp)
app.register_blueprint(positions_bp)


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_frontend(path):
    build_dir = _HERE / 'frontend' / 'build'
    if path and (build_dir / path).exists():
        return send_file(str(build_dir / path))
    return send_file(str(build_dir / 'index.html'))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
