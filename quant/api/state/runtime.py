import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

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


_STRATEGY_DEFAULT_SYMBOLS: Dict[str, str] = {}

STRATEGIES_DIR = Path(__file__).resolve().parent.parent.parent / 'features' / 'strategies'
DOCS_DIR = STRATEGIES_DIR / 'docs'

_STATE_FILE = Path(__file__).resolve().parent.parent.parent / 'var' / 'strategy_state.json'

_EMPTY_BACKTEST = {
    'sharpe': 0.0, 'max_dd': 0.0, 'cagr': 0.0, 'win_rate': 0.0,
    'period': 'Pending', 'train_sharpe': 0.0, 'test_sharpe': 0.0,
    'sharpe_degradation': 0.0, 'pct_profitable': 0.0,
}


def _to_snake_case(name: str) -> str:
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def _get_first_docstring(strategy_file: Path) -> str:
    try:
        content = strategy_file.read_text(encoding='utf-8')
        match = re.search(r'"""(.*?)"""', content, re.DOTALL)
        if match:
            lines = match.group(1).strip().splitlines()
            return lines[0] if lines else ''
    except Exception:
        pass
    return ''


def _extract_param_metadata(strategy_file: Path, config_params: dict) -> dict:
    result = {}
    for pname, pdefault in config_params.items():
        if pname == 'symbols':
            continue
        ptype = type(pdefault).__name__
        type_map = {'int': 'int', 'float': 'float', 'str': 'str', 'bool': 'bool'}
        result[pname] = {
            'type': type_map.get(ptype, 'str'),
            'default': pdefault,
        }
    try:
        content = strategy_file.read_text(encoding='utf-8')
        param_docs = re.findall(r'(\w+):\s*(?:int|float|str|bool)\s*=.*?"""?(.*?)(?:"""|$)', content)
    except Exception:
        pass
    return result


def _discover_strategies_dynamic() -> tuple:
    from quant.features.strategies.registry import StrategyRegistry

    available: Dict[str, dict] = {}
    id_to_registry: Dict[str, str] = {}
    dir_map: Dict[str, str] = {}
    parameters: Dict[str, dict] = {}

    for name in StrategyRegistry.list_strategies():
        sid = _to_snake_case(name)

        strategy_dir = STRATEGIES_DIR / sid
        if not strategy_dir.is_dir():
            for item in STRATEGIES_DIR.iterdir():
                if item.is_dir() and not item.name.startswith(('_' or '.')):
                    cfg = item / 'config.yaml'
                    if cfg.exists():
                        try:
                            import yaml
                            with open(cfg) as f:
                                data = yaml.safe_load(f)
                            if data and data.get('strategy', {}).get('name') == name:
                                strategy_dir = item
                                break
                        except Exception:
                            pass

        description = _get_first_docstring(strategy_dir / 'strategy.py') if strategy_dir.is_dir() else ''

        dir_name = strategy_dir.name if strategy_dir.is_dir() else sid
        doc_file = f'{sid}.md'

        backtest = dict(_EMPTY_BACKTEST)
        bt_file = STRATEGIES_DIR.parent.parent / 'infrastructure' / 'var' / 'research' / f'{name}_*_metrics.json'
        bt_files = sorted(Path(str(bt_file).rsplit('*', 1)[0]).parent.glob(f'{name}_*_metrics.json'))
        if bt_files:
            try:
                with open(bt_files[-1]) as f:
                    m = json.load(f)
                backtest = {
                    'sharpe': m.get('sharpe_ratio', 0.0),
                    'max_dd': abs(m.get('max_drawdown_pct', 0.0)),
                    'cagr': m.get('total_return_pct', 0.0) / 10.0 if m.get('total_return_pct') else 0.0,
                    'win_rate': m.get('win_rate_pct', 0.0),
                    'period': m.get('start', 'Pending') + ' ~ ' + m.get('end', '') if m.get('start') else 'Pending',
                    'train_sharpe': 0.0,
                    'test_sharpe': m.get('sharpe_ratio', 0.0),
                    'sharpe_degradation': 0.0,
                    'pct_profitable': m.get('win_rate_pct', 0.0),
                }
            except Exception:
                pass

        config_params = {}
        cfg_path = strategy_dir / 'config.yaml' if strategy_dir.is_dir() else None
        if cfg_path and cfg_path.exists():
            try:
                import yaml
                with open(cfg_path) as f:
                    cfg_data = yaml.safe_load(f)
                config_params = cfg_data.get('parameters', {}) if cfg_data else {}
            except Exception:
                pass

        params = _extract_param_metadata(
            strategy_dir / 'strategy.py' if strategy_dir.is_dir() else None,
            config_params,
        )

        available[name] = {
            'id': sid,
            'name': name,
            'description': description,
            'status': 'active',
            'priority': len(available) + 1,
            'doc_file': doc_file,
            'backtest': backtest,
        }
        id_to_registry[sid] = name
        dir_map[sid] = dir_name
        parameters[sid] = params

    return available, id_to_registry, dir_map, parameters


AVAILABLE_STRATEGIES: Dict[str, dict] = {}
STRATEGY_ID_TO_REGISTRY: Dict[str, str] = {}
_STRATEGY_DIR_MAP: Dict[str, str] = {}
STRATEGY_PARAMETERS: Dict[str, dict] = {}


def _build_strategy_state():
    global AVAILABLE_STRATEGIES, STRATEGY_ID_TO_REGISTRY, _STRATEGY_DIR_MAP, STRATEGY_PARAMETERS
    avail, id2reg, dmap, params = _discover_strategies_dynamic()
    AVAILABLE_STRATEGIES = avail
    STRATEGY_ID_TO_REGISTRY = id2reg
    _STRATEGY_DIR_MAP = dmap
    STRATEGY_PARAMETERS = params
    _load_strategy_state()


_cio_engine = None
_last_snapshot_date = None


def _init_default_symbols():
    global _STRATEGY_DEFAULT_SYMBOLS
    if _STRATEGY_DEFAULT_SYMBOLS:
        return
    try:
        from quant.domain.ports.storage import Storage
        from quant.infrastructure.data.storage_duckdb import DuckDBStorage
        _db_tmp: Storage = DuckDBStorage(read_only=True)
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
        from quant.features.cio.cioengine import CIOEngine
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


_build_strategy_state()
