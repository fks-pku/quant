import json
import random
import threading
import time
from pathlib import Path

from quant.execution.strategy_position_tracker import get_tracker, DEFAULT_STRATEGY

system_process = None
system_thread = None
system_status = 'stopped'
portfolio_data = {
    'nav': 100000.0,
    'total_unrealized_pnl': 0.0,
    'total_realized_pnl': 0.0
}
strategies_data = []
positions_data = []
selected_strategy = 'VolatilityRegime'
simulation_running = False
_backtest_results = {}
_backtest_lock = threading.Lock()
_futu_lock = threading.Lock()
_futu_broker = None

MOCK_SYMBOLS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'SPY', 'QQQ']
MOCK_PRICES = {
    'AAPL': 178.50, 'MSFT': 378.25, 'GOOGL': 141.80,
    'AMZN': 185.60, 'TSLA': 245.20, 'SPY': 512.30, 'QQQ': 438.50, 'VIX': 14.5
}

_STRATEGY_DEFAULT_SYMBOLS = {}

_STRATEGY_DIR_MAP = {
    'volatility_regime': 'volatility_regime',
    'simple_momentum': 'simple_momentum',
    'momentum_eod': 'momentum_eod',
    'mean_reversion_1m': 'mean_reversion',
    'dual_thrust': 'dual_thrust',
    'cross_sectional_mean_reversion': 'cross_sectional_mr',
    'dual_momentum': 'dual_momentum',
}

STRATEGIES_DIR = Path(__file__).resolve().parent.parent.parent / 'quant' / 'strategies'
DOCS_DIR = STRATEGIES_DIR / 'docs'

AVAILABLE_STRATEGIES = {
    'VolatilityRegime': {
        'id': 'volatility_regime',
        'name': 'Volatility Regime',
        'description': 'Regime-based strategy switching based on VIX levels. Switches between momentum and mean reversion sub-strategies depending on market volatility.',
        'status': 'active',
        'priority': 1,
        'doc_file': 'volatility_regime.md',
        'backtest': {
            'sharpe': 0.97,
            'max_dd': 18.2,
            'cagr': 14.5,
            'win_rate': 62.5,
            'period': '2015-2024 OOS',
            'train_sharpe': 1.18,
            'test_sharpe': 0.97,
            'sharpe_degradation': 17.8,
            'pct_profitable': 94.7
        }
    },
    'SimpleMomentum': {
        'id': 'simple_momentum',
        'name': 'Cross-Sectional Momentum',
        'description': 'Long top decile, short bottom decile by momentum score. Monthly rebalancing.',
        'status': 'paused',
        'priority': 2,
        'doc_file': 'simple_momentum.md',
        'backtest': {
            'sharpe': 0.78,
            'max_dd': 28.5,
            'cagr': 11.2,
            'win_rate': 55.0,
            'period': '2015-2024 OOS',
            'train_sharpe': 1.05,
            'test_sharpe': 0.78,
            'sharpe_degradation': 25.7,
            'pct_profitable': 85.0
        }
    },
    'MomentumEOD': {
        'id': 'momentum_eod',
        'name': 'Momentum EOD',
        'description': 'Buy top-N S&P 500 gainers at market open, sell at market close. Educational example.',
        'status': 'active',
        'priority': 3,
        'doc_file': 'momentum_eod.md',
        'backtest': {
            'sharpe': 0.45,
            'max_dd': 35.0,
            'cagr': 6.8,
            'win_rate': 52.0,
            'period': '2015-2024 OOS',
            'train_sharpe': 0.85,
            'test_sharpe': 0.45,
            'sharpe_degradation': 47.1,
            'pct_profitable': 65.0
        }
    },
    'MeanReversion1m': {
        'id': 'mean_reversion_1m',
        'name': 'Mean Reversion (1m)',
        'description': 'RSI-based mean reversion on 1-minute data. Educational example.',
        'status': 'active',
        'priority': 4,
        'doc_file': 'mean_reversion.md',
        'backtest': {
            'sharpe': 0.35,
            'max_dd': 42.0,
            'cagr': 4.5,
            'win_rate': 48.0,
            'period': '2015-2024 OOS',
            'train_sharpe': 0.72,
            'test_sharpe': 0.35,
            'sharpe_degradation': 51.4,
            'pct_profitable': 55.0
        }
    },
    'DualThrust': {
        'id': 'dual_thrust',
        'name': 'Dual Thrust',
        'description': 'Classic break-out system adapted for futures. Reference implementation.',
        'status': 'paused',
        'priority': 5,
        'doc_file': 'dual_thrust.md',
        'backtest': {
            'sharpe': 0.55,
            'max_dd': 22.0,
            'cagr': 8.5,
            'win_rate': 58.0,
            'period': '2015-2024 OOS',
            'train_sharpe': 0.95,
            'test_sharpe': 0.55,
            'sharpe_degradation': 42.1,
            'pct_profitable': 72.0
        }
    },
    'CrossSectionalMeanReversion': {
        'id': 'cross_sectional_mean_reversion',
        'name': 'Cross-Sectional Mean Reversion',
        'description': 'Short-term mean reversion strategy based on cross-sectional z-scores.',
        'status': 'paused',
        'priority': 6,
        'doc_file': 'cross_sectional_mean_reversion.md',
        'backtest': {
            'sharpe': 0.65,
            'max_dd': 25.0,
            'cagr': 9.0,
            'win_rate': 54.0,
            'period': '2015-2024 OOS',
            'train_sharpe': 0.90,
            'test_sharpe': 0.65,
            'sharpe_degradation': 27.8,
            'pct_profitable': 78.0
        }
    },
    'DualMomentum': {
        'id': 'dual_momentum',
        'name': 'Dual Momentum',
        'description': 'Absolute and relative momentum for tactical asset allocation.',
        'status': 'paused',
        'priority': 7,
        'doc_file': 'dual_momentum.md',
        'backtest': {
            'sharpe': 0.72,
            'max_dd': 20.0,
            'cagr': 10.5,
            'win_rate': 60.0,
            'period': '2015-2024 OOS',
            'train_sharpe': 1.00,
            'test_sharpe': 0.72,
            'sharpe_degradation': 28.0,
            'pct_profitable': 80.0
        }
    },
    'TencentMomentum': {
        'id': 'TencentMomentum',
        'name': 'Tencent SMA Crossover',
        'description': 'Single-stock SMA crossover strategy for HK equities. Buy when fast SMA > slow SMA, sell on death cross.',
        'status': 'active',
        'priority': 8,
        'doc_file': None,
        'backtest': {
            'sharpe': 0.31,
            'max_dd': 54.48,
            'cagr': 5.9,
            'win_rate': 40.91,
            'period': '2016-2026 HK.00700',
        }
    }
}

_STATE_FILE = Path(__file__).resolve().parent.parent.parent / 'data' / 'strategy_state.json'

STRATEGY_ID_TO_REGISTRY = {
    'volatility_regime': 'VolatilityRegime',
    'simple_momentum': 'SimpleMomentum',
    'momentum_eod': 'MomentumEOD',
    'mean_reversion_1m': 'MeanReversion1m',
    'dual_thrust': 'DualThrust',
    'cross_sectional_mean_reversion': 'CrossSectionalMeanReversion',
    'dual_momentum': 'DualMomentum',
    'TencentMomentum': 'TencentMomentum',
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
    'momentum_eod': {
        'top_n': {'type': 'int', 'default': 5, 'description': 'Number of top gainers to buy'},
        'max_position_pct': {'type': 'float', 'default': 0.1, 'description': 'Max position size per trade'},
    },
    'mean_reversion_1m': {
        'rsi_period': {'type': 'int', 'default': 14, 'description': 'RSI calculation period'},
        'rsi_oversold': {'type': 'float', 'default': 30.0, 'description': 'RSI oversold threshold'},
        'rsi_overbought': {'type': 'float', 'default': 70.0, 'description': 'RSI overbought threshold'},
        'lookback': {'type': 'int', 'default': 20, 'description': 'Mean reversion lookback'},
    },
    'dual_thrust': {
        'n_periods': {'type': 'int', 'default': 20, 'description': 'Lookback period for range calculation'},
        'k1': {'type': 'float', 'default': 0.5, 'description': 'Upper bound multiplier'},
        'k2': {'type': 'float', 'default': 0.5, 'description': 'Lower bound multiplier'},
    },
    'cross_sectional_mean_reversion': {
        'lookback': {'type': 'int', 'default': 5, 'description': 'Return lookback period'},
        'zscore_threshold': {'type': 'float', 'default': 1.5, 'description': 'Z-score entry threshold'},
    },
    'dual_momentum': {
        'lookback_months': {'type': 'int', 'default': 12, 'description': 'Momentum lookback period'},
        'risk_free_rate': {'type': 'float', 'default': 0.02, 'description': 'Risk-free rate for excess return'},
    },
    'TencentMomentum': {
        'fast_period': {'type': 'int', 'default': 20, 'description': 'Fast SMA period'},
        'slow_period': {'type': 'int', 'default': 60, 'description': 'Slow SMA period'},
        'position_pct': {'type': 'float', 'default': 0.95, 'description': 'Position size as % of NAV'},
    },
}

_cio_engine = None


def _init_default_symbols():
    global _STRATEGY_DEFAULT_SYMBOLS
    if _STRATEGY_DEFAULT_SYMBOLS:
        return
    try:
        from quant.data.storage_duckdb import DuckDBStorage
        _db_tmp = DuckDBStorage()
        _all_syms = _db_tmp.get_symbols('daily', 'hk') + _db_tmp.get_symbols('daily', 'us')
        _db_tmp.close()
        if _all_syms:
            _default_sym_str = ','.join(_all_syms)
        else:
            _default_sym_str = 'HK.00700'
    except Exception:
        _default_sym_str = 'HK.00700'
    for _sid in ['TencentMomentum', 'volatility_regime', 'simple_momentum',
                  'momentum_eod', 'mean_reversion_1m', 'dual_thrust',
                  'cross_sectional_mean_reversion', 'dual_momentum']:
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
        print(f"Loaded strategy state: {len(deleted)} deleted, {len(statuses)} status overrides")
    except Exception as e:
        print(f"Warning: failed to load strategy state: {e}")


def _save_strategy_state():
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    all_ids = {'volatility_regime', 'simple_momentum', 'momentum_eod', 'mean_reversion_1m',
               'dual_thrust', 'cross_sectional_mean_reversion', 'dual_momentum', 'TencentMomentum'}
    remaining = {info['id'] for info in AVAILABLE_STRATEGIES.values()}
    deleted = list(all_ids - remaining)
    statuses = {info['id']: info.get('status', 'paused') for info in AVAILABLE_STRATEGIES.values()}
    with open(_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump({'deleted': deleted, 'statuses': statuses}, f, indent=2)


def simulation_loop():
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
            holdings.append({
                'symbol': sym,
                'quantity': pos['quantity'],
                'avg_price': pos['avg_price'],
                'current_price': current,
                'pnl': round(pnl, 2)
            })

        portfolio_data = {
            'nav': round(100000.0 + unrealized_pnl, 2),
            'total_unrealized_pnl': round(unrealized_pnl, 2),
            'total_realized_pnl': 0.0
        }
        positions_data = holdings

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
        sim_thread = threading.Thread(target=simulation_loop, daemon=True)
        sim_thread.start()
        try:
            while simulation_running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        simulation_running = False
        system_status = 'stopped'
        positions_data = []
        strategies_data = []
        portfolio_data = {
            'nav': 100000.0,
            'total_unrealized_pnl': 0.0,
            'total_realized_pnl': 0.0
        }


def _get_cio_engine():
    global _cio_engine
    if _cio_engine is None:
        from quant.cio.cioengine import CIOEngine
        from quant.cio.market_assessor import MarketAssessor
        from quant.cio.news_analyzer import NewsAnalyzer
        from quant.cio.weight_allocator import WeightAllocator

        assessor = MarketAssessor()
        news_analyzer = NewsAnalyzer(provider="openai")
        allocator = WeightAllocator()
        _cio_engine = CIOEngine(
            assessor=assessor,
            news_analyzer=news_analyzer,
            allocator=allocator,
        )
    return _cio_engine


def _get_futu_broker():
    global _futu_broker
    return _futu_broker


_last_snapshot_date = None


def _maybe_snapshot(tracker, total_nav):
    global _last_snapshot_date
    from datetime import date as date_type
    today = date_type.today().isoformat()
    if _last_snapshot_date == today:
        return
    try:
        from quant.data.storage_duckdb import DuckDBStorage
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
