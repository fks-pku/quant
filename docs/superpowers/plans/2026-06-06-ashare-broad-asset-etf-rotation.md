# A-share Broad Asset ETF Rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reject-zone domestic broad-asset ETF rotation candidate that excludes cross-border ETFs, includes CSI1000, and treats gold, cash, and bond ETFs as ranked candidates instead of fixed fallback legs.

**Architecture:** Add one focused strategy under `quant/features/strategies/reject/ashare_broad_asset_etf_rotation/` and one audited universe helper in `quant/infrastructure/research/cn_etf_universe.py`. The strategy consumes a category-symbol map, ranks eligible symbols with current daily data only, and exposes diagnostics for universe and ranking review.

**Tech Stack:** Python 3.10+, pytest, existing `DailyBarStrategy`, existing audited ETF registry and DuckDB-backed ETF universe helpers.

---

## File Map

- Create `quant/tests/test_ashare_broad_asset_etf_rotation.py`: focused TDD coverage for strategy defaults, ranking, no hidden fallback, stale-bar rejection, volatility floor, and category cap.
- Modify `quant/tests/test_research_pipeline.py`: focused coverage for the new broad-asset audited universe helper.
- Modify `quant/infrastructure/research/cn_etf_universe.py`: add broad-asset category order and `build_broad_asset_etf_pit_universe()`.
- Create `quant/features/strategies/reject/ashare_broad_asset_etf_rotation/strategy.py`: new reject-zone strategy implementation.
- Create `quant/features/strategies/reject/ashare_broad_asset_etf_rotation/config.yaml`: disabled candidate config with locked default parameters.
- Create `quant/features/strategies/reject/ashare_broad_asset_etf_rotation/README.md`: strategy thesis, universe, risk controls, and research status.
- Modify `quant/features/strategies/AGENTS.md`: only if implementation introduces a reusable rule that should be documented for future ETF strategies.

## Task 1: Broad-Asset Universe Helper

**Files:**
- Modify: `quant/tests/test_research_pipeline.py`
- Modify: `quant/infrastructure/research/cn_etf_universe.py`

- [ ] **Step 1: Write the failing helper test**

Append this test near the other ETF universe tests in `quant/tests/test_research_pipeline.py`:

```python
def test_broad_asset_etf_pit_universe_uses_domestic_audited_categories(tmp_path):
    import duckdb

    from quant.infrastructure.research.cn_etf_universe import build_broad_asset_etf_pit_universe

    meta_path = tmp_path / "fund_meta.duckdb"
    etf_path = tmp_path / "cn_etf_o'clock.duckdb"
    nav_path = tmp_path / "cn_nav_o'clock.duckdb"

    conn = duckdb.connect(str(meta_path))
    try:
        conn.execute(
            """
            CREATE TABLE cn_fund_instruments (
                symbol VARCHAR,
                name VARCHAR,
                index_name VARCHAR,
                index_code VARCHAR,
                list_date DATE,
                delist_date DATE,
                instrument_type VARCHAR
            )
            """
        )
    finally:
        conn.close()

    domestic_symbols = ["510050", "510300", "512100", "159915", "159949", "510880", "518880", "511990", "511010"]
    excluded_cross_border = ["513100", "513050", "159920", "510900"]

    conn = duckdb.connect(str(etf_path))
    try:
        conn.execute("CREATE TABLE daily_cn_ochl (timestamp TIMESTAMP, symbol VARCHAR)")
        values = ", ".join(f"(TIMESTAMP '2020-01-02', '{symbol}')" for symbol in [*domestic_symbols, *excluded_cross_border])
        conn.execute(f"INSERT INTO daily_cn_ochl VALUES {values}")
    finally:
        conn.close()

    conn = duckdb.connect(str(nav_path))
    try:
        conn.execute(
            """
            CREATE TABLE cn_fund_nav (
                symbol VARCHAR,
                nav_date DATE,
                total_netasset DOUBLE,
                net_asset DOUBLE
            )
            """
        )
        values = ", ".join(f"('{symbol}', DATE '2020-01-02', 100000000.0, NULL)" for symbol in domestic_symbols)
        conn.execute(f"INSERT INTO cn_fund_nav VALUES {values}")
    finally:
        conn.close()

    universe = build_broad_asset_etf_pit_universe(
        fund_meta_db_path=str(meta_path),
        etf_db_path=str(etf_path),
        fund_nav_db_path=str(nav_path),
        universe_start="2020-01-01",
        universe_end="2020-12-31",
    )

    assert universe["universe_selection_policy"] == "audited_stable_etf_registry"
    assert universe["universe_registry_version"] == "audited_stable_etf_registry_v1"
    assert universe["category_symbols"]["csi1000"] == ["512100"]
    assert "512100" in universe["symbols"]
    assert not set(excluded_cross_border).intersection(universe["symbols"])
    assert universe["registered_universe_counts"]["registered_symbol_count"] == 9
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest quant/tests/test_research_pipeline.py::test_broad_asset_etf_pit_universe_uses_domestic_audited_categories -q
```

Expected: FAIL because `build_broad_asset_etf_pit_universe` is not defined.

- [ ] **Step 3: Implement the helper**

In `quant/infrastructure/research/cn_etf_universe.py`, add:

```python
_BROAD_ASSET_CATEGORY_ORDER = ("sse50", "csi300", "csi1000", "chinext", "chinext50", "dividend", "gold", "cash", "bond_rate")
```

Add this function near the other universe builders:

```python
def build_broad_asset_etf_pit_universe(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    fund_meta_db_path: str = _DEFAULT_FUND_META_DB,
    etf_db_path: str = _DEFAULT_ETF_DB,
    fund_nav_db_path: str = _DEFAULT_FUND_NAV_DB,
    universe_as_of: Optional[Any] = None,
    min_history_days_as_of: int = 0,
    max_symbols_per_category: int = 0,
    universe_start: Optional[Any] = None,
    universe_end: Optional[Any] = None,
) -> Dict[str, Any]:
    entries = [
        entry
        for entry in _REGISTERED_ETFS
        if entry.category_group in _BROAD_ASSET_CATEGORY_ORDER
    ]
    rows = _load_registered_etf_rows(
        entries,
        start=start,
        end=end,
        fund_meta_db_path=fund_meta_db_path,
        etf_db_path=etf_db_path,
        fund_nav_db_path=fund_nav_db_path,
        universe_as_of=universe_as_of,
        min_history_days_as_of=min_history_days_as_of,
        universe_start=universe_start,
        universe_end=universe_end,
    )
    buckets = {category: [] for category in _BROAD_ASSET_CATEGORY_ORDER}
    for row in rows:
        category = str(row.get("category_group") or "")
        if category in buckets:
            buckets[category].append(row)
    category_cap = max(0, int(max_symbols_per_category or 0))
    category_symbols = _category_symbol_map(buckets, category_cap)
    as_of = _parse_datetime(universe_as_of)
    window_start = _parse_datetime(universe_start) or start
    window_end = _parse_datetime(universe_end) or end
    return {
        "category_symbols": category_symbols,
        "symbols": flatten_category_symbols(category_symbols),
        "audit": rows,
        "universe_as_of": as_of.date().isoformat() if as_of else "",
        "universe_start": window_start.date().isoformat() if window_start else "",
        "universe_end": window_end.date().isoformat() if window_end else "",
        "universe_min_history_days_as_of": max(0, int(min_history_days_as_of or 0)),
        "universe_max_symbols_per_category": category_cap,
        "universe_selection_policy": "audited_stable_etf_registry",
        "universe_registry_version": REGISTERED_ETF_UNIVERSE_VERSION,
        "registered_universe_counts": _registered_universe_counts(entries, rows),
    }
```

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest quant/tests/test_research_pipeline.py::test_broad_asset_etf_pit_universe_uses_domestic_audited_categories -q
```

Expected: PASS.

## Task 2: Strategy RED Tests

**Files:**
- Create: `quant/tests/test_ashare_broad_asset_etf_rotation.py`

- [ ] **Step 1: Add strategy behavior tests**

Create `quant/tests/test_ashare_broad_asset_etf_rotation.py` with:

```python
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from quant.features.strategies.reject.ashare_broad_asset_etf_rotation.strategy import (
    AShareBroadAssetEtfRotationStrategy,
    DEFAULT_CATEGORY_SYMBOLS,
)


class _Portfolio:
    nav = 100000.0


class _Context:
    def __init__(self):
        self.portfolio = _Portfolio()
        self.orders = []

    def submit_order(self, symbol, quantity, side, order_type, price, strategy_name):
        self.orders.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "side": side,
                "order_type": order_type,
                "price": price,
                "strategy_name": strategy_name,
            }
        )
        return f"order-{len(self.orders)}"


def _feed(strategy, symbol, closes, *, last_date=date(2026, 5, 20), turnover=50000000.0, volume=100000, total_netasset=100000000.0):
    first_date = last_date - timedelta(days=len(closes) - 1)
    for index, close in enumerate(closes):
        bar = {
            "symbol": symbol,
            "timestamp": first_date + timedelta(days=index),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "adj_open": close,
            "adj_high": close,
            "adj_low": close,
            "adj_close": close,
            "adj_factor": 1.0,
            "volume": volume,
            "turnover": turnover,
            "total_netasset": total_netasset,
            "net_asset": total_netasset,
        }
        strategy.on_data(None, bar)


def test_default_universe_includes_csi1000_and_excludes_cross_border_etfs():
    assert DEFAULT_CATEGORY_SYMBOLS["csi1000"] == ["512100"]
    symbols = {symbol for values in DEFAULT_CATEGORY_SYMBOLS.values() for symbol in values}
    assert "512100" in symbols
    assert not {"513100", "513050", "159920", "510900"}.intersection(symbols)


def test_gold_cash_and_bond_are_ranked_candidates_not_forced_fallbacks():
    strategy = AShareBroadAssetEtfRotationStrategy(
        category_symbols={"gold": ["518880"], "cash": ["511990"], "bond_rate": ["511010"]},
        momentum_lookback=6,
        momentum_skip=1,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        max_positions=1,
        target_exposure=0.90,
        holding_days=1,
        require_pit_size=True,
        volatility_floor=0.01,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "518880", [10.0, 10.01, 10.02, 10.03, 10.04, 10.05, 10.06, 10.07])
    _feed(strategy, "511990", [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0])
    _feed(strategy, "511010", [10.0, 10.2, 10.4, 10.6, 10.8, 11.0, 11.2, 11.4])

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert [order["symbol"] for order in context.orders] == ["511010"]
    assert strategy.get_guard_diagnostics()["last_selected"] == ["511010"]


def test_no_candidate_sells_existing_position_and_keeps_actual_cash():
    strategy = AShareBroadAssetEtfRotationStrategy(
        category_symbols={"csi300": ["510300"], "gold": ["518880"]},
        momentum_lookback=6,
        momentum_skip=1,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        max_positions=2,
        holding_days=1,
    )
    context = _Context()
    strategy.on_start(context)
    strategy.on_fill(context, SimpleNamespace(symbol="510300", quantity=1000, side="BUY"))

    _feed(strategy, "510300", [10.0, 9.95, 9.9, 9.85, 9.8, 9.75, 9.7, 9.65])
    _feed(strategy, "518880", [5.0, 4.99, 4.98, 4.97, 4.96, 4.95, 4.94, 4.93])

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert context.orders == [
        {
            "symbol": "510300",
            "quantity": 1000,
            "side": "SELL",
            "order_type": "MARKET",
            "price": pytest.approx(9.65),
            "strategy_name": "ashare_broad_asset_etf_rotation",
        }
    ]
    assert strategy.get_guard_diagnostics()["last_selected"] == []


def test_stale_symbol_is_not_visible_even_when_score_is_best():
    strategy = AShareBroadAssetEtfRotationStrategy(
        category_symbols={"csi300": ["510300", "515300"]},
        momentum_lookback=6,
        momentum_skip=1,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        max_positions=1,
        holding_days=1,
        require_pit_size=True,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510300", [10.0, 10.1, 10.2, 10.4, 10.6, 10.8, 11.0, 11.2])
    _feed(strategy, "515300", [10.0, 10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5], last_date=date(2026, 5, 19))

    strategy.on_after_trading(context, date(2026, 5, 20))

    assert [order["symbol"] for order in context.orders] == ["510300"]
    assert "515300" not in strategy.get_guard_diagnostics()["last_visible_by_category"]["csi300"]


def test_category_cap_keeps_one_representative_per_bucket():
    strategy = AShareBroadAssetEtfRotationStrategy(
        category_symbols={"csi300": ["510300", "159919"], "gold": ["518880"]},
        momentum_lookback=6,
        momentum_skip=1,
        trend_window=5,
        volatility_window=5,
        liquidity_window=3,
        min_avg_turnover=1000.0,
        max_positions=2,
        max_positions_per_category=1,
        target_exposure=0.90,
        holding_days=1,
        require_pit_size=True,
    )
    context = _Context()
    strategy.on_start(context)

    _feed(strategy, "510300", [10.0, 10.3, 10.6, 10.9, 11.2, 11.5, 11.8, 12.1])
    _feed(strategy, "159919", [10.0, 10.25, 10.5, 10.75, 11.0, 11.25, 11.5, 11.75])
    _feed(strategy, "518880", [5.0, 5.04, 5.08, 5.12, 5.16, 5.2, 5.24, 5.28])

    strategy.on_after_trading(context, date(2026, 5, 20))

    symbols = [order["symbol"] for order in context.orders]
    assert len([symbol for symbol in symbols if symbol in {"510300", "159919"}]) == 1
    assert "518880" in symbols
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest quant/tests/test_ashare_broad_asset_etf_rotation.py -q
```

Expected: collection FAIL because `quant.features.strategies.reject.ashare_broad_asset_etf_rotation` does not exist.

## Task 3: Strategy GREEN Implementation

**Files:**
- Create: `quant/features/strategies/reject/ashare_broad_asset_etf_rotation/strategy.py`

- [ ] **Step 1: Implement the strategy**

Create `quant/features/strategies/reject/ashare_broad_asset_etf_rotation/strategy.py` based on the existing reject-zone ETF strategies, with:

```python
STRATEGY_NAME = "ashare_broad_asset_etf_rotation"
DEFAULT_CATEGORY_SYMBOLS = {
    "sse50": ["510050"],
    "csi300": ["510300"],
    "csi1000": ["512100"],
    "chinext": ["159915"],
    "chinext50": ["159949"],
    "dividend": ["510880"],
    "gold": ["518880"],
    "cash": ["511990"],
    "bond_rate": ["511010"],
}
```

Implement constructor parameters:

```python
category_symbols: Optional[Dict[str, List[str]]] = None
momentum_lookback: int = 126
momentum_skip: int = 1
trend_window: int = 120
volatility_window: int = 60
liquidity_window: int = 20
min_avg_turnover: float = 20_000_000.0
max_positions: int = 3
max_positions_per_category: int = 1
target_exposure: float = 0.98
holding_days: int = 20
lot_size: int = 100
require_pit_size: bool = True
volatility_floor: float = 0.01
```

Core methods to include:

```python
def _select_targets(self, trading_date: date) -> List[str]:
    selected = []
    category_counts = {}
    scored = []
    visible = self._visible_symbols_by_category(trading_date)
    for category, symbols in visible.items():
        for symbol in symbols:
            reason = self._candidate_rejection(symbol, trading_date)
            if reason:
                self._count("entry_rejections", reason)
                continue
            score = self._risk_adjusted_momentum(symbol)
            if score is None or score <= 0:
                self._count("entry_rejections", "non_positive_score")
                continue
            scored.append((score, category, symbol))
    self._last_scores = {symbol: score for score, _, symbol in scored}
    for _, category, symbol in sorted(scored, key=lambda item: (-item[0], item[2])):
        if len(selected) >= self.max_positions:
            break
        if category_counts.get(category, 0) >= self.max_positions_per_category:
            self._count("entry_rejections", "category_cap")
            continue
        selected.append(symbol)
        category_counts[category] = category_counts.get(category, 0) + 1
    return selected
```

Use helper methods consistent with neighboring ETF strategies:

```python
_execute_rebalance()
_candidate_rejection()
_risk_adjusted_momentum()
_momentum()
_above_trend()
_volatility()
_avg_turnover()
_cash_turnover()
_valid_adj_closes()
_pit_size()
_round_lot()
get_guard_diagnostics()
_get_parameters()
```

- [ ] **Step 2: Verify GREEN**

Run:

```powershell
python -m pytest quant/tests/test_ashare_broad_asset_etf_rotation.py -q
```

Expected: PASS.

## Task 4: Config and README

**Files:**
- Create: `quant/features/strategies/reject/ashare_broad_asset_etf_rotation/config.yaml`
- Create: `quant/features/strategies/reject/ashare_broad_asset_etf_rotation/README.md`

- [ ] **Step 1: Add candidate config**

Create `config.yaml`:

```yaml
strategy:
  name: ashare_broad_asset_etf_rotation
  enabled: false
  status: candidate
  priority: 30

parameters:
  pit_universe_enabled: true
  universe_selection_policy: audited_stable_etf_registry
  universe_registry_version: audited_stable_etf_registry_v1
  universe_start: "2016-01-01"
  universe_end: "2026-05-31"
  universe_min_history_days_as_of: 0
  universe_max_symbols_per_category: 0
  momentum_lookback: 126
  momentum_skip: 1
  trend_window: 120
  volatility_window: 60
  volatility_floor: 0.01
  liquidity_window: 20
  min_avg_turnover: 20000000.0
  max_positions: 3
  max_positions_per_category: 1
  target_exposure: 0.98
  holding_days: 20
  lot_size: 100
  require_pit_size: true
```

- [ ] **Step 2: Add README**

Create `README.md` with sections:

```markdown
# A-share Broad Asset ETF Rotation

Reject-zone daily ETF candidate for domestic broad-asset rotation.

## Universe

Default audited categories: SSE50, CSI300, CSI1000, ChiNext, ChiNext50, dividend, gold, cash ETF, and rate bond ETF. Cross-border ETFs and sector/theme ETFs are excluded from the default pool.

## Signal

Every 20 trading days, rank visible ETFs by skipped 126-day momentum divided by 60-day realized volatility with a volatility floor. Candidates must have current bars, enough lookback history, positive momentum, close above the 120-day average, sufficient average turnover, and PIT NAV or size evidence when required.

## Portfolio

Hold at most 3 ETFs, with at most 1 ETF per category. Selected ETFs are equal weighted up to the configured target exposure. If no ETF qualifies, the strategy holds actual cash rather than buying a hidden defensive proxy.

## Research Status

This is a candidate strategy. It needs strict backtest, walk-forward validation, ETF metadata survivorship audit, capacity audit, and return contribution attribution before promotion.
```

- [ ] **Step 3: Verify files are readable**

Run:

```powershell
Get-Content quant/features/strategies/reject/ashare_broad_asset_etf_rotation/config.yaml
Get-Content quant/features/strategies/reject/ashare_broad_asset_etf_rotation/README.md
```

Expected: both files print without encoding errors.

## Task 5: Documentation and Contract Checks

**Files:**
- Modify: `quant/features/strategies/AGENTS.md`, only if new strategy-layer invariant is added

- [ ] **Step 1: Decide whether invariant docs changed**

If tests only cover this one candidate's business logic, do not edit invariant docs. If a reusable invariant is added, append one sentence to `quant/features/strategies/AGENTS.md` saying broad ETF rotation candidates must keep cross-border ETF categories out of defaults unless explicitly configured.

- [ ] **Step 2: Run text encoding check after doc rewrites**

Run:

```powershell
python scripts/check_text_encoding.py
```

Expected: exit 0. If the script path is absent, run:

```powershell
python quant/scripts/check_text_encoding.py
```

Expected: exit 0.

## Task 6: Final Verification

**Files:**
- All files touched in prior tasks

- [ ] **Step 1: Run focused tests**

Run:

```powershell
python -m pytest quant/tests/test_ashare_broad_asset_etf_rotation.py quant/tests/test_research_pipeline.py::test_broad_asset_etf_pit_universe_uses_domestic_audited_categories -q
```

Expected: PASS.

- [ ] **Step 2: Run strategy invariant tests if invariant docs changed**

Run only if `quant/features/strategies/AGENTS.md` or strategy invariant docs changed:

```powershell
python -m pytest quant/tests/test_strategies_invariants.py quant/tests/test_invariant_docs_contract.py -q
```

Expected: PASS.

- [ ] **Step 3: Inspect diff**

Run:

```powershell
git diff -- quant/features/strategies/reject/ashare_broad_asset_etf_rotation quant/infrastructure/research/cn_etf_universe.py quant/tests/test_ashare_broad_asset_etf_rotation.py quant/tests/test_research_pipeline.py quant/features/strategies/AGENTS.md
```

Expected: diff contains only the broad-asset ETF rotation work and no unrelated dashboard or command-file changes.

## Self-Review

- Spec coverage: domestic-only pool, CSI1000 inclusion, no cross-border defaults, gold/cash/bond as ranked candidates, no hidden fallback, PIT/current-bar gates, category cap, and diagnostics are covered.
- Placeholder scan: no TBD or TODO entries are present.
- Type consistency: the plan uses `category_symbols`, `DEFAULT_CATEGORY_SYMBOLS`, `build_broad_asset_etf_pit_universe`, and `AShareBroadAssetEtfRotationStrategy` consistently.
