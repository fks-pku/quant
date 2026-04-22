import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

system_process = None
system_thread = None
system_status = 'stopped'
portfolio_data: Dict[str, float] = {
    'nav': 100000.0,
    'total_unrealized_pnl': 0.0,
    'total_realized_pnl': 0.0
}
strategies_data: list = []
positions_data: list = []
orders_data: list = []
selected_strategy = 'VolatilityRegime'
simulation_running = False
_backtest_results: Dict[str, Any] = {}
_backtest_lock = threading.Lock()
_futu_lock = threading.Lock()
_futu_broker: Optional[Any] = None

MOCK_SYMBOLS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'SPY', 'QQQ']
MOCK_PRICES = {
    'AAPL': 178.50, 'MSFT': 378.25, 'GOOGL': 141.80,
    'AMZN': 185.60, 'TSLA': 245.20, 'SPY': 512.30, 'QQQ': 438.50, 'VIX': 14.5
}


def _get_futu_broker():
    global _futu_broker
    return _futu_broker


# Strategy metadata (kept here for API backward compatibility)
_STRATEGY_DEFAULT_SYMBOLS: Dict[str, str] = {}

_STRATEGY_DIR_MAP = {
    'volatility_regime': 'volatility_regime',
    'simple_momentum': 'simple_momentum',
    'cross_sectional_mean_reversion': 'cross_sectional_mr',
}

STRATEGIES_DIR = Path(__file__).resolve().parent.parent.parent / 'features' / 'strategies'
DOCS_DIR = STRATEGIES_DIR / 'docs'

AVAILABLE_STRATEGIES = {
    'VolatilityRegime': {
        'id': 'volatility_regime',
        'name': 'Volatility Regime',
        'description': 'Regime-based strategy switching based on VIX levels.',
        'status': 'active',
        'priority': 1,
        'doc_file': 'volatility_regime.md',
        'backtest': {'sharpe': 0.97, 'max_dd': 18.2, 'cagr': 14.5, 'win_rate': 62.5, 'period': '2015-2024 OOS', 'train_sharpe': 1.18, 'test_sharpe': 0.97, 'sharpe_degradation': 17.8, 'pct_profitable': 94.7}
    },
    'SimpleMomentum': {
        'id': 'simple_momentum',
        'name': 'Cross-Sectional Momentum',
        'description': 'Long top decile, short bottom decile by momentum score.',
        'status': 'paused',
        'priority': 2,
        'doc_file': 'simple_momentum.md',
        'backtest': {'sharpe': 0.78, 'max_dd': 28.5, 'cagr': 11.2, 'win_rate': 55.0, 'period': '2015-2024 OOS', 'train_sharpe': 1.05, 'test_sharpe': 0.78, 'sharpe_degradation': 25.7, 'pct_profitable': 85.0}
    },
    'CrossSectionalMeanReversion': {
        'id': 'cross_sectional_mean_reversion',
        'name': 'Cross-Sectional Mean Reversion',
        'description': 'Short-term mean reversion strategy based on cross-sectional z-scores.',
        'status': 'paused',
        'priority': 3,
        'doc_file': 'cross_sectional_mean_reversion.md',
        'backtest': {'sharpe': 0.65, 'max_dd': 25.0, 'cagr': 9.0, 'win_rate': 54.0, 'period': '2015-2024 OOS', 'train_sharpe': 0.90, 'test_sharpe': 0.65, 'sharpe_degradation': 27.8, 'pct_profitable': 78.0}
    },
}

_STATE_FILE = Path(__file__).resolve().parent.parent.parent / 'var' / 'strategy_state.json'

STRATEGY_ID_TO_REGISTRY = {
    'volatility_regime': 'VolatilityRegime',
    'simple_momentum': 'SimpleMomentum',
    'cross_sectional_mean_reversion': 'CrossSectionalMeanReversion',
}

STRATEGY_PARAMETERS = {
    'volatility_regime': {
        'vix_lookback': {'type': 'int', 'default': 20, 'description': 'VIX lookback period for SMA calculation'},
        'vix_bull_threshold': {'type': 'float', 'default': 15.0, 'description': 'VIX threshold for bull regime'},
        'vix_bear_threshold': {'type': 'float', 'default': 25.0, 'description': 'VIX threshold for bear regime'},
        'momentum_lookback': {'type': 'int', 'default': 20, 'description': 'Momentum calculation lookback'},
        'momentum_top_n': {'type': 'int', 'default': 5, 'description': 'Number of top momentum stocks'},
        'rsi_period': {'type': 'int', 'default': 14, 'description': 'RSI calculation period'},
        'rsi_oversold': {'type': 'float', 'default': 30.0, 'description': 'RSI oversold threshold'},
        'rsi_overbought': {'type': 'float', 'default': 70.0, 'description': 'RSI overbought threshold'},
        'max_position_pct': {'type': 'float', 'default': 0.05, 'description': 'Max position size as % of NAV'},
        'reduce_exposure_bear': {'type': 'float', 'default': 0.3, 'description': 'Exposure reduction in bear regime'},
    },
    'simple_momentum': {
        'lookback_days': {'type': 'int', 'default': 20, 'description': 'Momentum lookback period'},
        'top_n': {'type': 'int', 'default': 10, 'description': 'Number of top performers to long'},
        'bottom_n': {'type': 'int', 'default': 10, 'description': 'Number of bottom performers to short'},
        'rebalance_freq': {'type': 'str', 'default': 'monthly', 'description': 'Rebalancing frequency'},
    },
    'cross_sectional_mean_reversion': {
        'lookback': {'type': 'int', 'default': 5, 'description': 'Return lookback period'},
        'zscore_threshold': {'type': 'float', 'default': 1.5, 'description': 'Z-score entry threshold'},
    },
}

_cio_engine = None
_last_snapshot_date = None


def _init_default_symbols():
    global _STRATEGY_DEFAULT_SYMBOLS
    if _STRATEGY_DEFAULT_SYMBOLS:
        return
    try:
        from quant.infrastructure.data.storage_duckdb import DuckDBStorage
        _db_tmp = DuckDBStorage(read_only=True)
        _all_syms = _db_tmp.get_symbols('daily', 'hk') + _db_tmp.get_symbols('daily', 'us') + _db_tmp.get_symbols('daily', 'cn')
        _db_tmp.close()
        _default_sym_str = ','.join(_all_syms) if _all_syms else 'HK.00700'
    except Exception:
        _default_sym_str = 'HK.00700'
    for _sid in list(STRATEGY_ID_TO_REGISTRY.keys()):
        _STRATEGY_DEFAULT_SYMBOLS[_sid] = _default_sym_str


def _load_strategy_state():
    if not _STATE_FILE.exists():
        return
    try:
        with open(_STATE_FILE, 'r', encoding='utf-8') as f:
            state = json.load(f)
        deleted = set(state.get('deleted', []))
        statuses = state.get('statuses', {})
        for sid in deleted:
            for name in list(AVAILABLE_STRATEGIES.keys()):
                if AVAILABLE_STRATEGIES[name]['id'] == sid:
                    del AVAILABLE_STRATEGIES[name]
                    break
            STRATEGY_ID_TO_REGISTRY.pop(sid, None)
            STRATEGY_PARAMETERS.pop(sid, None)
            _STRATEGY_DEFAULT_SYMBOLS.pop(sid, None)
        for name, info in AVAILABLE_STRATEGIES.items():
            if info['id'] in statuses:
                info['status'] = statuses[info['id']]
    except Exception as e:
        print(f"Warning: failed to load strategy state: {e}")


def _save_strategy_state():
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    all_ids = set(STRATEGY_ID_TO_REGISTRY.keys())
    remaining = {info['id'] for info in AVAILABLE_STRATEGIES.values()}
    deleted = list(all_ids - remaining)
    statuses = {info['id']: info.get('status', 'paused') for info in AVAILABLE_STRATEGIES.values()}
    with open(_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump({'deleted': deleted, 'statuses': statuses}, f, indent=2)


def simulation_loop():
    import random
    global simulation_running, portfolio_data, positions_data
    positions = {}
    symbols_to_trade = ['AAPL', 'MSFT', 'GOOGL']
    while simulation_running:
        for sym in list(MOCK_PRICES.keys()):
            change_pct = random.gauss(0, 0.3) / 100
            MOCK_PRICES[sym] = round(MOCK_PRICES[sym] * (1 + change_pct), 2)
        if random.random() < 0.15 and len(positions) < 5:
            sym = random.choice(symbols_to_trade)
            if sym not in positions:
                qty = random.randint(10, 100)
                price = MOCK_PRICES.get(sym, 100)
                positions[sym] = {'symbol': sym, 'quantity': qty, 'avg_price': price}
        unrealized_pnl = 0.0
        holdings = []
        for sym, pos in positions.items():
            current = MOCK_PRICES.get(sym, pos['avg_price'])
            pnl = (current - pos['avg_price']) * pos['quantity']
            unrealized_pnl += pnl
            holdings.append({'symbol': sym, 'quantity': pos['quantity'], 'avg_price': pos['avg_price'], 'current_price': current, 'pnl': round(pnl, 2)})
        portfolio_data = {'nav': round(100000.0 + unrealized_pnl, 2), 'total_unrealized_pnl': round(unrealized_pnl, 2), 'total_realized_pnl': 0.0}
        positions_data = holdings
        import time
        time.sleep(3)


def run_quant_system():
    global system_status, portfolio_data, strategies_data, positions_data, simulation_running
    system_status = 'running'
    simulation_running = True
    strategies_data = [
        {'name': 'VolatilityRegime', 'enabled': True, 'symbols': ['AAPL', 'MSFT', 'GOOGL']},
        {'name': 'MomentumEOD', 'enabled': True, 'symbols': ['AAPL', 'MSFT', 'GOOGL']},
        {'name': 'MeanReversion1m', 'enabled': True, 'symbols': ['SPY', 'QQQ']},
        {'name': 'DualThrust', 'enabled': False, 'symbols': ['ES']},
    ]
    try:
        from quant.quant_system import QuantSystem
        quant = QuantSystem()
        quant.initialize()
        quant.run('paper')
    except Exception as e:
        print(f"Quant system unavailable ({e}), running in simulation mode")
        import threading
        sim_thread = threading.Thread(target=simulation_loop, daemon=True)
        sim_thread.start()
        try:
            while simulation_running:
                import time
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        simulation_running = False
        system_status = 'stopped'
        positions_data = []
        strategies_data = []
        portfolio_data = {'nav': 100000.0, 'total_unrealized_pnl': 0.0, 'total_realized_pnl': 0.0}


def _get_cio_engine():
    global _cio_engine
    if _cio_engine is None:
        from quant.features.cio.cio_engine import CIOEngine
        from quant.features.cio.market_assessor import MarketAssessor
        from quant.features.cio.news_analyzer import NewsAnalyzer
        from quant.features.cio.weight_allocator import WeightAllocator
        assessor = MarketAssessor()
        news_analyzer = NewsAnalyzer(provider="openai")
        allocator = WeightAllocator()
        _cio_engine = CIOEngine(assessor=assessor, news_analyzer=news_analyzer, allocator=allocator)
    return _cio_engine


def _maybe_snapshot(tracker, total_nav):
    global _last_snapshot_date
    from datetime import date as date_type
    today = date_type.today().isoformat()
    if _last_snapshot_date == today:
        return
    try:
        from quant.infrastructure.data.storage_duckdb import DuckDBStorage
        db = DuckDBStorage()
        snapshots = tracker.snapshot_all(total_nav)
        for snap in snapshots:
            d = {"date": snap.date, "strategy_name": snap.strategy_name,
                 "nav": snap.nav, "market_value": snap.market_value,
                 "cash": snap.cash, "unrealized_pnl": snap.unrealized_pnl,
                 "realized_pnl": snap.realized_pnl}
            db.save_strategy_snapshot(d)
        _last_snapshot_date = today
    except Exception:
        pass
