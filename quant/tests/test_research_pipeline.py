import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import pandas as pd

from quant.features.research.evaluator import StrategyEvaluator
from quant.features.research.models import DEFAULT_A_SHARE_SYMBOLS, EvaluationReport, RawStrategy, ResearchConfig, ResearchResult, StrategySpec, ValidationReport
from quant.features.research.pool import CandidatePool
from quant.features.research.research_engine import ResearchEngine
from quant.infrastructure.research.repository import FileResearchStore


def _raw_strategy() -> RawStrategy:
    return RawStrategy(
        title="Daily Momentum Breakout",
        description="Ranks liquid stocks by 20 day momentum and buys breakouts using daily OHLCV.",
        source="arxiv",
        source_url="https://example.test/paper",
        authors="Researcher",
        published_date="2026-04-01",
    )


def _evaluation_report() -> EvaluationReport:
    return EvaluationReport(
        suitability_score=7.5,
        complexity_score=3.0,
        data_requirement="low",
        daily_adaptable=True,
        estimated_edge=0.08,
        recommended_symbols=["SPY", "QQQ"],
        strategy_type="momentum",
        summary="Daily OHLCV momentum breakout with clear behavioral rationale.",
        economic_rationale_score=2.0,
        factor_uniqueness_score=1.0,
        data_availability_score=2.0,
        implementation_score=2.0,
        overfit_risk_score=1.0,
        cost_capacity_score=1.0,
        regime_robustness_score=1.0,
        risk_flags=["survivorship_bias"],
        rejection_reason="",
    )


def _test_root() -> Path:
    root = Path(__file__).resolve().parents[1] / "infrastructure" / "var" / "test_research_pipeline" / str(uuid.uuid4())
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_research_defaults_and_spec_universe_are_a_share_only():
    from quant.features.research.validation.strategy_spec_builder import StrategySpecBuilder
    from quant.scripts.run_research import HeuristicEvaluator

    assert ResearchConfig().default_symbols == DEFAULT_A_SHARE_SYMBOLS

    report = _evaluation_report()
    spec = StrategySpecBuilder().build(_raw_strategy(), report)

    assert spec.universe == DEFAULT_A_SHARE_SYMBOLS

    full_universe = ["000001", "000002", "600519", "AAPL"]
    full_spec = StrategySpecBuilder({"default_universe": full_universe}).build(_raw_strategy(), report)

    assert full_spec.universe == ["000001", "000002", "600519"]

    cli_report = HeuristicEvaluator().evaluate(_raw_strategy())

    assert cli_report.recommended_symbols == DEFAULT_A_SHARE_SYMBOLS


def test_cn_strict_backtests_always_attach_family_appropriate_execution_cost_model():
    from quant.api.research_bp import _strict_execution_cost_model

    default_model = _strict_execution_cost_model(
        "ashare_value_momentum_filter",
        {
            "name": "ashare_value_momentum_filter",
            "research_meta": {
                "strategy_spec": {
                    "strategy_id": "ashare_value_momentum_filter",
                    "signal_formula_key": "ashare_value_momentum_filter",
                    "universe": ["000001", "600519"],
                }
            },
        },
        True,
    )
    assert default_model["enabled"] is True
    assert default_model["name"] == "cn_daily_liquidity_impact"
    assert default_model["max_participation_rate"] == 0.02
    assert default_model["impact_coefficient"] == 0.35

    small_cap_model = _strict_execution_cost_model(
        "joinquant_small_cap_low_price",
        {
            "name": "joinquant_small_cap_low_price",
            "research_meta": {
                "strategy_spec": {
                    "signal_formula_key": "joinquant_small_cap_low_price_factor",
                    "required_fields": ["close", "market_cap", "turnover"],
                    "universe": ["000001", "600519"],
                }
            },
        },
        True,
    )
    assert small_cap_model["name"] == "small_cap_realistic"
    assert small_cap_model["max_participation_rate"] == 0.01
    assert small_cap_model["impact_coefficient"] == 0.5

    etf_model = _strict_execution_cost_model(
        "joinquant_qixing_daily_etf_rotation",
        {
            "name": "JoinQuant ETF rotation",
            "parameters": {"symbols": ["510300", "510500", "159915", "511880"]},
            "research_meta": {
                "strategy_spec": {
                    "signal_formula_key": "joinquant_qixing_daily_etf_rotation",
                    "universe": ["510300", "510500", "159915", "511880"],
                }
            },
        },
        True,
    )
    assert etf_model["name"] == "cn_etf_liquidity_impact"
    assert etf_model["max_participation_rate"] == 0.05
    assert etf_model["impact_coefficient"] == 0.15

    assert _strict_execution_cost_model("us_strategy", {"name": "us_strategy"}, False) is None


def test_persistent_candidate_merges_archived_strategy_parameters(monkeypatch):
    from quant.api import research_bp as research_module

    monkeypatch.setattr(
        research_module,
        "_archived_candidate_info",
        lambda sid: {
            "id": sid,
            "parameters": {"max_position_pct": 0.85, "holding_days": 5},
            "research_meta": {"strategy_spec": {"universe": ["600001"], "lookback_days": 1}},
        },
    )

    merged = research_module._merge_archived_candidate_defaults(
        "joinquant_small_cap_low_price",
        {
            "id": "joinquant_small_cap_low_price",
            "name": "persisted",
            "parameters": {"holding_days": 7},
            "research_meta": {"strategy_spec": {"horizon_days": 7}},
        },
    )

    assert merged["name"] == "persisted"
    assert merged["parameters"] == {"max_position_pct": 0.85, "holding_days": 7}
    assert merged["research_meta"]["strategy_spec"]["universe"] == ["600001"]
    assert merged["research_meta"]["strategy_spec"]["lookback_days"] == 1
    assert merged["research_meta"]["strategy_spec"]["horizon_days"] == 7


def test_pit_archived_candidate_overrides_stale_persisted_universe(monkeypatch):
    from quant.api import research_bp as research_module

    monkeypatch.setattr(
        research_module,
        "_archived_candidate_info",
        lambda sid: {
            "id": sid,
            "parameters": {
                "pit_universe_enabled": True,
                "timing_symbol": "000300",
                "risk_category_symbols": {"csi300": ["510300", "159919"]},
                "defensive_category_symbols": {"gold": ["518880", "159934"]},
            },
            "research_meta": {
                "strategy_spec": {
                    "signal_formula_key": "ashare_gold_equity_barbell_timing",
                    "universe": ["000300", "159919", "510300", "518880", "159934"],
                    "risk_category_symbols": {"csi300": ["510300", "159919"]},
                    "defensive_category_symbols": {"gold": ["518880", "159934"]},
                }
            },
        },
    )

    merged = research_module._merge_archived_candidate_defaults(
        "ashare_gold_equity_barbell_timing",
        {
            "id": "ashare_gold_equity_barbell_timing",
            "name": "persisted",
            "parameters": {
                "risk_symbols": ["510050", "510300"],
                "defensive_symbols": ["518880"],
                "timing_symbol": "510300",
            },
            "research_meta": {
                "strategy_spec": {
                    "universe": ["510050", "510300", "518880"],
                    "risk_symbols": ["510050", "510300"],
                }
            },
        },
    )

    assert merged["name"] == "persisted"
    assert merged["parameters"]["pit_universe_enabled"] is True
    assert "risk_symbols" not in merged["parameters"]
    assert merged["parameters"]["risk_category_symbols"] == {"csi300": ["510300", "159919"]}
    spec = merged["research_meta"]["strategy_spec"]
    assert spec["universe"] == ["000300", "159919", "510300", "518880", "159934"]
    assert "risk_symbols" not in spec
    assert spec["risk_category_symbols"] == {"csi300": ["510300", "159919"]}


def test_research_engine_uses_archived_resolver_for_strategy_symbols():
    tmp_path = _test_root()
    try:
        research_store = FileResearchStore(tmp_path / "research")
        research_store.upsert_candidate(
            {
                "id": "ashare_gold_equity_barbell_timing",
                "status": "rejected",
                "research_meta": {"strategy_spec": {"universe": ["510050", "510300", "518880"]}},
            }
        )

        def resolver(sid):
            assert sid == "ashare_gold_equity_barbell_timing"
            return {
                "id": sid,
                "parameters": {"pit_universe_enabled": True},
                "research_meta": {
                    "strategy_spec": {
                        "universe": ["000300", "159919", "510300", "518880", "159934"],
                    }
                },
            }

        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False),
            research_store=research_store,
            archived_candidate_resolver=resolver,
        )

        assert engine._strategy_symbols("ashare_gold_equity_barbell_timing") == [
            "000300",
            "159919",
            "510300",
            "518880",
            "159934",
        ]
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_update_hypothesis_backtest_refreshes_pit_strategy_spec(monkeypatch):
    from quant.api import research_bp as research_module

    tmp_path = _test_root()
    try:
        research_store = FileResearchStore(tmp_path / "research")
        research_store.upsert_candidate(
            {
                "id": "ashare_gold_equity_barbell_timing",
                "status": "rejected",
                "parameters": {"risk_symbols": ["510050", "510300"]},
                "research_meta": {"strategy_spec": {"universe": ["510050", "510300", "518880"]}},
            }
        )
        research_store.upsert_hypothesis(
            {
                "hypothesis_id": "strict_backtest_ashare_gold_equity_barbell_timing",
                "strategy_id": "ashare_gold_equity_barbell_timing",
                "title": "A-share Gold-Equity ETF Barbell Timing",
                "status": "rejected",
                "stage": "backtest",
                "source": "fixture",
                "source_url": "",
                "thesis": "fixture",
                "decision_reason": "",
                "metrics": {},
                "evidence": {
                    "strategy_spec": {
                        "universe": ["510050", "510300", "518880"],
                        "risk_symbols": ["510050", "510300"],
                    }
                },
            }
        )
        monkeypatch.setattr(
            research_module,
            "_archived_candidate_info",
            lambda sid: {
                "id": sid,
                "parameters": {
                    "pit_universe_enabled": True,
                    "risk_category_symbols": {"csi300": ["510300", "159919"]},
                    "defensive_category_symbols": {"gold": ["518880", "159934"]},
                },
                "research_meta": {
                    "strategy_spec": {
                        "universe": ["000300", "159919", "510300", "518880", "159934"],
                        "risk_category_symbols": {"csi300": ["510300", "159919"]},
                        "defensive_category_symbols": {"gold": ["518880", "159934"]},
                    }
                },
            },
        )

        strict_report = {
            "metrics": {
                "sharpe": 1.2,
                "sortino": 1.6,
                "cagr": 0.14,
                "max_drawdown_pct": -0.18,
                "calmar_ratio": 0.78,
            },
            "benchmark": {"symbol": "000300"},
        }

        pool = CandidatePool(research_store=research_store)
        research_module._update_hypothesis_backtest(
            pool,
            "ashare_gold_equity_barbell_timing",
            strict_report,
            "candidate",
            "backtest",
            "Strict Backtester Sharpe 1.20",
        )

        row = research_store.get_hypothesis("strict_backtest_ashare_gold_equity_barbell_timing")
        spec = row["evidence"]["strategy_spec"]
        assert spec["universe"] == ["000300", "159919", "510300", "518880", "159934"]
        assert "risk_symbols" not in spec
        assert spec["risk_category_symbols"] == {"csi300": ["510300", "159919"]}
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_persist_candidate_backtest_refreshes_archived_pit_candidate_metadata():
    from quant.api import research_bp as research_module

    tmp_path = _test_root()
    try:
        research_store = FileResearchStore(tmp_path / "research")
        research_store.upsert_candidate(
            {
                "id": "ashare_gold_equity_barbell_timing",
                "status": "candidate",
                "parameters": {"risk_symbols": ["510050", "510300"]},
                "research_meta": {"strategy_spec": {"universe": ["510050", "510300", "518880"]}},
            }
        )
        pool = CandidatePool(research_store=research_store)
        merged = {
            "id": "ashare_gold_equity_barbell_timing",
            "status": "candidate",
            "parameters": {
                "pit_universe_enabled": True,
                "risk_category_symbols": {"csi300": ["510300"]},
                "defensive_category_symbols": {"gold": ["518880"]},
                "universe_as_of": "2016-01-01",
            },
            "research_meta": {
                "strategy_spec": {
                    "universe": ["000300", "510300", "518880"],
                    "risk_category_symbols": {"csi300": ["510300"]},
                    "defensive_category_symbols": {"gold": ["518880"]},
                    "pit_universe_enabled": True,
                    "universe_as_of": "2016-01-01",
                }
            },
        }

        research_module._persist_candidate_backtest(
            pool,
            "ashare_gold_equity_barbell_timing",
            merged,
            {"metrics": {"sharpe": 1.2}},
        )

        stored = research_store.get_candidate("ashare_gold_equity_barbell_timing")
        assert "risk_symbols" not in stored["parameters"]
        assert stored["parameters"]["risk_category_symbols"] == {"csi300": ["510300"]}
        assert stored["parameters"]["universe_as_of"] == "2016-01-01"
        assert stored["research_meta"]["strategy_spec"]["universe"] == ["000300", "510300", "518880"]
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_archived_pit_universe_resolution_is_cached(monkeypatch):
    from quant.api import research_bp as research_module
    from quant.infrastructure.research import cn_etf_universe as universe_module

    calls = {"count": 0}

    def fake_build(
        *,
        universe_as_of=None,
        min_history_days_as_of=0,
        max_symbols_per_category=0,
        universe_start=None,
        universe_end=None,
    ):
        calls["count"] += 1
        assert universe_as_of in (None, "")
        assert min_history_days_as_of == 0
        assert max_symbols_per_category == 0
        assert universe_start == "2016-01-01"
        assert universe_end == "2026-05-31"
        return {
            "symbols": ["510300", "518880"],
            "risk_category_symbols": {"csi300": ["510300"]},
            "defensive_category_symbols": {"gold": ["518880"]},
            "universe_selection_policy": "audited_stable_etf_registry",
            "universe_as_of": "",
            "universe_start": "2016-01-01",
            "universe_end": "2026-05-31",
            "universe_min_history_days_as_of": 0,
            "universe_max_symbols_per_category": 0,
            "universe_registry_version": "audited_stable_etf_registry_v1",
            "registered_universe_counts": {"registered_symbol_count": 2, "active_symbol_count": 2, "missing_data_count": 0},
        }

    monkeypatch.setattr(universe_module, "build_gold_equity_barbell_pit_universe", fake_build)
    research_module._ARCHIVED_PIT_UNIVERSE_CACHE.clear()

    params = {
        "pit_universe_enabled": True,
        "timing_symbol": "000300",
        "universe_selection_policy": "audited_stable_etf_registry",
        "universe_start": "2016-01-01",
        "universe_end": "2026-05-31",
        "universe_min_history_days_as_of": 0,
        "universe_max_symbols_per_category": 0,
    }
    first_params, first_symbols = research_module._archived_pit_universe_parameters(
        "ashare_gold_equity_barbell_timing",
        params,
    )
    second_params, second_symbols = research_module._archived_pit_universe_parameters(
        "ashare_gold_equity_barbell_timing",
        params,
    )

    assert calls["count"] == 1
    assert first_symbols == second_symbols == ["000300", "510300", "518880"]
    assert first_params["risk_category_symbols"] == second_params["risk_category_symbols"]
    assert first_params["universe_selection_policy"] == "audited_stable_etf_registry"
    assert first_params["universe_start"] == "2016-01-01"
    assert first_params["universe_end"] == "2026-05-31"
    assert first_params["universe_max_symbols_per_category"] == 0


def test_gold_equity_pit_universe_attaches_sidecar_paths(tmp_path):
    import duckdb

    from quant.infrastructure.research.cn_etf_universe import build_gold_equity_barbell_pit_universe

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
                list_date DATE,
                delist_date DATE,
                instrument_type VARCHAR
            )
            """
        )
        conn.execute(
            """
            INSERT INTO cn_fund_instruments VALUES
            ('510300', '沪深300ETF', '沪深300', DATE '2015-01-01', NULL, 'ETF'),
            ('518880', '黄金ETF', '黄金9999', DATE '2015-01-01', NULL, 'ETF')
            """
        )
    finally:
        conn.close()

    conn = duckdb.connect(str(etf_path))
    try:
        conn.execute("CREATE TABLE daily_cn_ochl (timestamp TIMESTAMP, symbol VARCHAR)")
        conn.execute(
            """
            INSERT INTO daily_cn_ochl VALUES
            (TIMESTAMP '2020-01-02', '510300'),
            (TIMESTAMP '2020-01-02', '518880')
            """
        )
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
        conn.execute(
            """
            INSERT INTO cn_fund_nav VALUES
            ('510300', DATE '2020-01-02', 100000000.0, NULL),
            ('518880', DATE '2020-01-02', 90000000.0, NULL)
            """
        )
    finally:
        conn.close()

    universe = build_gold_equity_barbell_pit_universe(
        fund_meta_db_path=str(meta_path),
        etf_db_path=str(etf_path),
        fund_nav_db_path=str(nav_path),
    )

    assert universe["risk_category_symbols"]["csi300"] == ["510300"]
    assert universe["defensive_category_symbols"]["gold"] == ["518880"]


def test_gold_equity_pit_universe_can_lock_candidates_to_as_of(tmp_path):
    import duckdb

    from quant.infrastructure.research.cn_etf_universe import build_gold_equity_barbell_pit_universe

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
                list_date DATE,
                delist_date DATE,
                instrument_type VARCHAR
            )
            """
        )
        conn.execute(
            """
            INSERT INTO cn_fund_instruments VALUES
            ('510300', '沪深300ETF', '沪深300', DATE '2015-01-01', NULL, 'ETF'),
            ('515300', '沪深300ETF新发', '沪深300', DATE '2020-01-01', NULL, 'ETF'),
            ('518880', '黄金ETF', '黄金9999', DATE '2015-01-01', NULL, 'ETF')
            """
        )
    finally:
        conn.close()

    conn = duckdb.connect(str(etf_path))
    try:
        conn.execute("CREATE TABLE daily_cn_ochl (timestamp TIMESTAMP, symbol VARCHAR)")
        conn.execute(
            """
            INSERT INTO daily_cn_ochl VALUES
            (TIMESTAMP '2015-12-30', '510300'),
            (TIMESTAMP '2020-01-02', '515300'),
            (TIMESTAMP '2015-12-30', '518880')
            """
        )
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
        conn.execute(
            """
            INSERT INTO cn_fund_nav VALUES
            ('510300', DATE '2015-12-30', 100000000.0, NULL),
            ('515300', DATE '2020-01-02', 900000000.0, NULL),
            ('518880', DATE '2015-12-30', 80000000.0, NULL)
            """
        )
    finally:
        conn.close()

    universe = build_gold_equity_barbell_pit_universe(
        fund_meta_db_path=str(meta_path),
        etf_db_path=str(etf_path),
        fund_nav_db_path=str(nav_path),
        universe_as_of="2016-01-01",
        min_history_days_as_of=1,
    )

    assert universe["risk_category_symbols"]["csi300"] == ["510300"]
    assert "515300" not in universe["symbols"]
    assert universe["universe_selection_policy"] == "audited_stable_etf_registry"
    assert universe["universe_as_of"] == "2016-01-01"


def test_gold_equity_registered_universe_does_not_auto_expand_same_category_candidates(tmp_path):
    import duckdb

    from quant.infrastructure.research.cn_etf_universe import build_gold_equity_barbell_pit_universe

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
                list_date DATE,
                delist_date DATE,
                instrument_type VARCHAR
            )
            """
        )
        conn.execute(
            """
            INSERT INTO cn_fund_instruments VALUES
            ('510300', '沪深300ETF', '沪深300', DATE '2015-01-01', NULL, 'ETF'),
            ('515300', '沪深300ETF新发', '沪深300', DATE '2020-01-01', NULL, 'ETF'),
            ('518880', '黄金ETF', '黄金9999', DATE '2015-01-01', NULL, 'ETF')
            """
        )
    finally:
        conn.close()

    conn = duckdb.connect(str(etf_path))
    try:
        conn.execute("CREATE TABLE daily_cn_ochl (timestamp TIMESTAMP, symbol VARCHAR)")
        conn.execute(
            """
            INSERT INTO daily_cn_ochl VALUES
            (TIMESTAMP '2016-01-04', '510300'),
            (TIMESTAMP '2020-01-02', '515300'),
            (TIMESTAMP '2016-01-04', '518880')
            """
        )
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
        conn.execute(
            """
            INSERT INTO cn_fund_nav VALUES
            ('510300', DATE '2016-01-04', 100000000.0, NULL),
            ('515300', DATE '2020-01-02', 900000000.0, NULL),
            ('518880', DATE '2016-01-04', 80000000.0, NULL)
            """
        )
    finally:
        conn.close()

    universe = build_gold_equity_barbell_pit_universe(
        fund_meta_db_path=str(meta_path),
        etf_db_path=str(etf_path),
        fund_nav_db_path=str(nav_path),
        universe_start="2016-01-01",
        universe_end="2025-12-31",
    )

    assert universe["risk_category_symbols"]["csi300"] == ["510300"]
    assert "515300" not in universe["symbols"]
    assert universe["universe_selection_policy"] == "audited_stable_etf_registry"
    assert universe["universe_start"] == "2016-01-01"
    assert universe["universe_end"] == "2025-12-31"


def test_pit_fund_category_universe_selects_stable_categories(tmp_path):
    import duckdb

    from quant.infrastructure.research.cn_etf_universe import build_pit_fund_category_universe

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
        conn.execute(
            """
            INSERT INTO cn_fund_instruments VALUES
            ('510300', '沪深300ETF', '沪深300', '000300.SH', DATE '2015-01-01', NULL, 'ETF'),
            ('515300', '沪深300ETF增强', '沪深300', '000300.SH', DATE '2019-01-01', NULL, 'ETF'),
            ('510880', '红利ETF', '上证红利指数', '000015.SH', DATE '2015-01-01', NULL, 'ETF'),
            ('518880', '黄金ETF', '黄金9999', '', DATE '2015-01-01', NULL, 'ETF')
            """
        )
    finally:
        conn.close()

    conn = duckdb.connect(str(etf_path))
    try:
        conn.execute("CREATE TABLE daily_cn_ochl (timestamp TIMESTAMP, symbol VARCHAR)")
        conn.execute(
            """
            INSERT INTO daily_cn_ochl VALUES
            (TIMESTAMP '2020-01-02', '510300'),
            (TIMESTAMP '2020-01-02', '515300'),
            (TIMESTAMP '2020-01-02', '510880'),
            (TIMESTAMP '2020-01-02', '518880')
            """
        )
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
        conn.execute(
            """
            INSERT INTO cn_fund_nav VALUES
            ('510300', DATE '2020-01-02', 100000000.0, NULL),
            ('515300', DATE '2020-01-02', 900000000.0, NULL),
            ('510880', DATE '2020-01-02', 200000000.0, NULL),
            ('518880', DATE '2020-01-02', 300000000.0, NULL)
            """
        )
    finally:
        conn.close()

    universe = build_pit_fund_category_universe(
        ["equity_cn_broad_csi300", "equity_cn_strategy_dividend", "commodity_gold"],
        fund_meta_db_path=str(meta_path),
        etf_db_path=str(etf_path),
        fund_nav_db_path=str(nav_path),
    )

    assert universe["category_symbols"]["equity_cn_broad_csi300"] == ["510300"]
    assert universe["category_symbols"]["equity_cn_strategy_dividend"] == ["510880"]
    assert universe["category_symbols"]["commodity_gold"] == ["518880"]
    assert "515300" not in universe["symbols"]
    assert universe["registered_universe_counts"]["registered_symbol_count"] == 3
    assert universe["registered_universe_counts"]["active_symbol_count"] == 3


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


def test_pit_fund_category_universe_ignores_unregistered_benchmark_classification(tmp_path):
    import duckdb

    from quant.infrastructure.research.cn_etf_universe import build_pit_fund_category_universe

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
        conn.execute(
            """
            INSERT INTO cn_fund_instruments VALUES
            ('560999', '核心宽基ETF', '核心宽基指数', '000999.SH', DATE '2020-01-01', NULL, 'ETF')
            """
        )
    finally:
        conn.close()

    conn = duckdb.connect(str(etf_path))
    try:
        conn.execute("CREATE TABLE daily_cn_ochl (timestamp TIMESTAMP, symbol VARCHAR)")
        conn.execute("INSERT INTO daily_cn_ochl VALUES (TIMESTAMP '2021-01-04', '560999')")
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
        conn.execute("INSERT INTO cn_fund_nav VALUES ('560999', DATE '2021-01-04', 100000000.0, NULL)")
    finally:
        conn.close()

    universe = build_pit_fund_category_universe(
        ["equity_cn_broad_index"],
        fund_meta_db_path=str(meta_path),
        etf_db_path=str(etf_path),
        fund_nav_db_path=str(nav_path),
    )

    assert universe["category_symbols"]["equity_cn_broad_index"] == []
    assert "560999" not in universe["symbols"]


def test_pit_fund_category_universe_ignores_unregistered_static_category(tmp_path):
    import duckdb

    from quant.infrastructure.research.cn_etf_universe import build_pit_fund_category_universe

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
        conn.execute(
            """
            INSERT INTO cn_fund_instruments VALUES
            ('560999', '核心宽基ETF', '核心宽基指数', '000999.SH', DATE '2020-01-01', NULL, 'ETF')
            """
        )
    finally:
        conn.close()

    conn = duckdb.connect(str(etf_path))
    try:
        conn.execute("CREATE TABLE daily_cn_ochl (timestamp TIMESTAMP, symbol VARCHAR)")
        conn.execute("INSERT INTO daily_cn_ochl VALUES (TIMESTAMP '2020-01-03', '560999')")
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
        conn.execute("INSERT INTO cn_fund_nav VALUES ('560999', DATE '2020-01-03', 100000000.0, NULL)")
    finally:
        conn.close()

    universe = build_pit_fund_category_universe(
        ["equity_cn_broad_index"],
        fund_meta_db_path=str(meta_path),
        etf_db_path=str(etf_path),
        fund_nav_db_path=str(nav_path),
        universe_as_of="2020-01-03",
    )

    assert universe["category_symbols"]["equity_cn_broad_index"] == []
    assert "560999" not in universe["symbols"]


def test_gold_equity_pit_universe_can_keep_primary_symbol_per_category(tmp_path):
    import duckdb

    from quant.infrastructure.research.cn_etf_universe import build_gold_equity_barbell_pit_universe

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
                list_date DATE,
                delist_date DATE,
                instrument_type VARCHAR
            )
            """
        )
        conn.execute(
            """
            INSERT INTO cn_fund_instruments VALUES
            ('510300', '沪深300ETF小', '沪深300', DATE '2015-01-01', NULL, 'ETF'),
            ('159919', '沪深300ETF大', '沪深300', DATE '2015-01-01', NULL, 'ETF'),
            ('518880', '黄金ETF', '黄金9999', DATE '2015-01-01', NULL, 'ETF')
            """
        )
    finally:
        conn.close()

    conn = duckdb.connect(str(etf_path))
    try:
        conn.execute("CREATE TABLE daily_cn_ochl (timestamp TIMESTAMP, symbol VARCHAR)")
        conn.execute(
            """
            INSERT INTO daily_cn_ochl VALUES
            (TIMESTAMP '2015-12-29', '510300'),
            (TIMESTAMP '2015-12-30', '510300'),
            (TIMESTAMP '2015-12-29', '159919'),
            (TIMESTAMP '2015-12-30', '159919'),
            (TIMESTAMP '2015-12-29', '518880'),
            (TIMESTAMP '2015-12-30', '518880')
            """
        )
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
        conn.execute(
            """
            INSERT INTO cn_fund_nav VALUES
            ('510300', DATE '2015-12-30', 100000000.0, NULL),
            ('159919', DATE '2015-12-30', 900000000.0, NULL),
            ('518880', DATE '2015-12-30', 80000000.0, NULL)
            """
        )
    finally:
        conn.close()

    universe = build_gold_equity_barbell_pit_universe(
        fund_meta_db_path=str(meta_path),
        etf_db_path=str(etf_path),
        fund_nav_db_path=str(nav_path),
        universe_as_of="2016-01-01",
        min_history_days_as_of=1,
        max_symbols_per_category=1,
    )

    assert universe["risk_category_symbols"]["csi300"] == ["510300"]
    assert "159919" not in universe["symbols"]
    assert universe["universe_selection_policy"] == "audited_stable_etf_registry"
    assert universe["universe_max_symbols_per_category"] == 1


def test_gold_equity_etf_survivorship_audit_reports_metadata_gaps(tmp_path):
    import duckdb

    from quant.infrastructure.research.cn_etf_universe import build_gold_equity_barbell_survivorship_audit

    meta_path = tmp_path / "fund_meta.duckdb"
    etf_path = tmp_path / "cn_etf_o'clock.duckdb"

    conn = duckdb.connect(str(meta_path))
    try:
        conn.execute(
            """
            CREATE TABLE cn_fund_instruments (
                symbol VARCHAR,
                name VARCHAR,
                index_name VARCHAR,
                list_date DATE,
                delist_date DATE,
                instrument_type VARCHAR
            )
            """
        )
        conn.execute("INSERT INTO cn_fund_instruments VALUES ('510300', '沪深300ETF', '沪深300', DATE '2015-01-01', NULL, 'ETF')")
    finally:
        conn.close()

    conn = duckdb.connect(str(etf_path))
    try:
        conn.execute("CREATE TABLE daily_cn_ochl (timestamp TIMESTAMP, symbol VARCHAR)")
        conn.execute(
            """
            INSERT INTO daily_cn_ochl VALUES
            (TIMESTAMP '2020-01-02', '510300'),
            (TIMESTAMP '2020-01-02', '160706')
            """
        )
    finally:
        conn.close()

    audit = build_gold_equity_barbell_survivorship_audit(
        fund_meta_db_path=str(meta_path),
        etf_db_path=str(etf_path),
    )

    assert audit["kind"] == "etf_metadata_survivorship_audit"
    assert audit["material"] is True
    assert audit["bar_symbols_missing_fund_meta"] == 1
    assert audit["fund_meta_delisted_symbols"] == 0
    assert audit["bar_symbols_missing_fund_meta_sample"][0]["symbol"] == "160706"


def test_gold_equity_etf_survivorship_audit_reports_registered_universe_coverage(tmp_path):
    import duckdb

    from quant.infrastructure.research.cn_etf_universe import build_gold_equity_barbell_survivorship_audit

    meta_path = tmp_path / "fund_meta.duckdb"
    etf_path = tmp_path / "cn_etf_o'clock.duckdb"

    conn = duckdb.connect(str(meta_path))
    try:
        conn.execute(
            """
            CREATE TABLE cn_fund_instruments (
                symbol VARCHAR,
                name VARCHAR,
                index_name VARCHAR,
                list_date DATE,
                delist_date DATE,
                instrument_type VARCHAR,
                fund_category VARCHAR,
                category_group VARCHAR
            )
            """
        )
        conn.execute(
            """
            INSERT INTO cn_fund_instruments VALUES
            ('510300', '沪深300ETF', '沪深300', DATE '2015-01-01', NULL, 'ETF', 'equity_cn_broad_csi300', 'csi300'),
            ('159915', '创业板ETF', '创业板', DATE '2015-01-01', NULL, 'ETF', 'equity_cn_broad_chinext', 'chinext')
            """
        )
    finally:
        conn.close()

    conn = duckdb.connect(str(etf_path))
    try:
        conn.execute("CREATE TABLE daily_cn_ochl (timestamp TIMESTAMP, symbol VARCHAR)")
        conn.execute(
            """
            INSERT INTO daily_cn_ochl VALUES
            (TIMESTAMP '2020-01-02', '510300'),
            (TIMESTAMP '2020-01-02', '159915')
            """
        )
    finally:
        conn.close()

    audit = build_gold_equity_barbell_survivorship_audit(
        start=datetime(2020, 1, 1),
        end=datetime(2020, 1, 3),
        fund_meta_db_path=str(meta_path),
        etf_db_path=str(etf_path),
    )

    assert audit["universe_registry_version"] == "audited_stable_etf_registry_v1"
    assert audit["registered_universe_symbol_count"] == 6
    assert audit["registered_universe_symbols_with_bars"] == 2
    assert audit["registered_universe_missing_bar_count"] == 4


def test_strict_stage_uses_etf_survivorship_warning_copy():
    tmp_path = _test_root()
    try:
        research_store = FileResearchStore(tmp_path / "research")
        research_store.upsert_hypothesis({
            "hypothesis_id": "h1",
            "strategy_id": "ashare_gold_equity_barbell_timing",
            "title": "ETF Timing",
            "status": "candidate",
            "stage": "strict_backtest",
            "source": "fixture",
            "source_url": "https://example.test",
            "thesis": "fixture",
            "decision_reason": "",
            "metrics": {
                "strict_backtest": {
                    "metrics": {
                        "cagr": 0.1144,
                        "max_drawdown_pct": -0.1968,
                        "total_trades": 198,
                    },
                    "capacity": {"max_adv_participation": 0.02},
                    "data_quality": {
                        "survivorship_audit": {
                            "kind": "etf_metadata_survivorship_audit",
                            "material": True,
                            "bar_symbols_missing_fund_meta": 13,
                            "fund_meta_delisted_symbols": 0,
                        }
                    },
                }
            },
        })
        engine = ResearchEngine(config=ResearchConfig(), research_store=research_store)

        verdict, conclusion = engine._attach_strict_backtest_conclusion("ashare_gold_equity_barbell_timing")

        stages = research_store.list_hypotheses()[0]["metrics"]["research_stage_conclusions"]
        scores = stages["strict_backtest"]["scores"]
        assert verdict == "warn"
        assert "ETF 日线中有 13 个 symbol 缺少基金元数据" in conclusion
        assert "人工审计注册 ETF 代表池和调仓日可见数据约束" in conclusion
        assert "起点主代表 ETF universe" not in conclusion
        assert "小市值 Top20" not in conclusion
        assert scores["survivorship_audit_kind"] == "etf_metadata_survivorship_audit"
        assert scores["bar_symbols_missing_fund_meta"] == 13
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_evaluator_parses_extended_json_report():
    class JsonLLM:
        def analyze(self, prompt, context):
            return json.dumps(
                {
                    "suitability_score": 7.5,
                    "complexity_score": 3.0,
                    "data_requirement": "low",
                    "daily_adaptable": True,
                    "estimated_edge": 0.08,
                    "recommended_symbols": ["SPY", "QQQ"],
                    "strategy_type": "momentum",
                    "summary": "Daily OHLCV momentum breakout with clear behavioral rationale.",
                    "economic_rationale_score": 2.0,
                    "factor_uniqueness_score": 1.0,
                    "data_availability_score": 2.0,
                    "implementation_score": 2.0,
                    "overfit_risk_score": 1.0,
                    "cost_capacity_score": 1.0,
                    "regime_robustness_score": 1.0,
                    "risk_flags": ["survivorship_bias"],
                    "rejection_reason": "",
                }
            )

    report = StrategyEvaluator(JsonLLM()).evaluate(_raw_strategy())

    assert report.suitability_score == pytest.approx(7.5)
    assert report.economic_rationale_score == pytest.approx(2.0)
    assert report.factor_uniqueness_score == pytest.approx(1.0)
    assert report.data_availability_score == pytest.approx(2.0)
    assert report.risk_flags == ["survivorship_bias"]
    assert report.admission_score > 0
    assert "rank_ic" in report.validation_tests


def test_evaluator_uses_professional_heuristic_when_llm_unavailable():
    report = StrategyEvaluator().evaluate(_raw_strategy())

    assert report.admission_score >= 6.0
    assert report.signal_quality_score >= 6.0
    assert report.data_requirement == "low"
    assert "rank_ic" in report.validation_tests
    assert "fdr_control" in report.validation_tests
    assert report.rejection_reason == ""


def test_heuristic_evaluator_uses_cn_symbols_when_target_market_is_cn():
    report = StrategyEvaluator(rubric_config={
        "target_market": "cn",
        "default_symbols": ["000300", "600519"],
    }).evaluate(_raw_strategy())

    assert report.recommended_symbols == ["000300", "600519"]


def test_evaluator_haircuts_hf_signals_even_when_llm_is_optimistic():
    class OptimisticLLM:
        def analyze(self, prompt, context):
            return {
                "suitability_score": 9.5,
                "complexity_score": 8.0,
                "data_requirement": "high-frequency",
                "daily_adaptable": False,
                "estimated_edge": 0.45,
                "recommended_symbols": ["BTC"],
                "strategy_type": "stat_arb",
                "summary": "High-frequency order book signal.",
                "economic_rationale_score": 1.0,
                "factor_uniqueness_score": 1.0,
                "data_availability_score": 0.2,
                "implementation_score": 0.2,
                "overfit_risk_score": 0.1,
                "cost_capacity_score": 0.1,
                "regime_robustness_score": 0.1,
                "risk_flags": [],
                "rejection_reason": "",
            }

    raw = RawStrategy(
        title="High-Frequency Crypto Order Book Alpha",
        description="Tick-level order book imbalance with deep learning and very high turnover.",
        source="blog",
        source_url="",
    )

    report = StrategyEvaluator(OptimisticLLM()).evaluate(raw)

    assert report.admission_score < 6.0
    assert "high_frequency_not_daily" in report.risk_flags
    assert "unrealistic_edge" in report.risk_flags
    assert report.rejection_reason


def test_research_engine_persists_candidates_and_markdown_artifacts():
    tmp_path = _test_root()
    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
        )

        result = engine.run_full_pipeline()

        candidates = research_store.list_by_status("candidate")
        assert result.integrated == 1
        assert len(candidates) == 1
        assert candidates[0]["id"] == "daily_momentum_breakout"
        assert candidates[0]["research_meta"]["economic_rationale_score"] == pytest.approx(2.0)
        assert (tmp_path / "research" / "reports" / "latest" / "last_result.json").exists()
        assert "Daily Momentum Breakout" in (tmp_path / "research" / "idea_bank" / "discovered_strategies.md").read_text(encoding="utf-8")
        assert "economic_rationale" in (tmp_path / "research" / "reports" / "latest" / "strategy_evaluation.md").read_text(encoding="utf-8")
        fast_report = (tmp_path / "research" / "reports" / "latest" / "fast_research_report.html").read_text(encoding="utf-8")
        strict_report = (tmp_path / "research" / "reports" / "latest" / "strict_backtest_report.html").read_text(encoding="utf-8")
        wf_report = (tmp_path / "research" / "reports" / "latest" / "walkforward_audit_report.html").read_text(encoding="utf-8")
        full_report = (tmp_path / "research" / "reports" / "latest" / "full_research_report.html").read_text(encoding="utf-8")
        assert (tmp_path / "research" / "reports" / "daily_momentum_breakout" / "full_research_report.html").exists()
        assert (tmp_path / "research" / "reports" / "daily_momentum_breakout" / "fast_research_report.html").exists()
        assert (tmp_path / "research" / "reports" / "daily_momentum_breakout" / "strict_backtest_report.html").exists()
        assert (tmp_path / "research" / "reports" / "daily_momentum_breakout" / "walkforward_audit_report.html").exists()
        assert not (tmp_path / "research" / "reports" / "latest" / "full_research_report.md").exists()
        assert (tmp_path / "research" / "reports" / "latest" / "metadata.json").exists()
        assert "End-to-End Research Report" in full_report
        assert "2. 策略逻辑" in full_report
        assert "4. 重要 Metric" in full_report
        assert "5. Walk-forward" in full_report
        assert "6. Stability" in full_report
        assert "Metric Checklist" not in full_report
        assert "信号公式" in fast_report
        assert "full_research_report.html" not in fast_report
        assert 'class="formula"' in fast_report
        assert "严格回测证据" in strict_report
        assert "Split 明细" in wf_report
        assert "Daily Momentum Breakout" in fast_report
        assert "000300" in fast_report
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_discovery_only_stores_idea_bank_without_evaluation():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class FailingEvaluator:
        def evaluate(self, raw):
            raise AssertionError("discovery-only mode must not evaluate ideas")

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=True),
            scout=FixedScout(),
            evaluator=FailingEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
        )

        result = engine.run_discovery_only()
        ideas = research_store.list_ideas("discovered")

        assert result.discovered == 1
        assert result.evaluated == 0
        assert result.integrated == 0
        assert len(ideas) == 1
        assert ideas[0]["title"] == "Daily Momentum Breakout"
        assert (tmp_path / "research" / "idea_bank" / "discovered_strategies.md").exists()
        assert (tmp_path / "research" / "idea_bank" / "idea_bank.json").exists()
        assert (tmp_path / "research" / "idea_bank" / "idea_bank.md").exists()
        assert not (tmp_path / "research" / "full_research_report.html").exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_formal_research_loads_local_idea_bank_without_scouting():
    tmp_path = _test_root()

    class FailingScout:
        def search(self, sources=None, max_results=10):
            raise AssertionError("formal mode must load from local idea bank")

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    try:
        research_store = FileResearchStore(tmp_path / "research")
        research_store.upsert_idea(_raw_strategy(), status="discovered", reason="seed")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False),
            scout=FailingScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
        )

        result = engine.run_formal_research_from_idea_bank()
        ideas = research_store.list_ideas("candidate")

        assert result.discovered == 1
        assert result.evaluated == 1
        assert result.integrated == 1
        assert len(ideas) == 1
        assert ideas[0]["title"] == "Daily Momentum Breakout"
        assert (tmp_path / "research" / "reports" / "latest" / "strategy_evaluation.md").exists()
        assert not (tmp_path / "research" / "full_research_report.html").exists()
        assert (tmp_path / "research" / "reports" / "daily_momentum_breakout" / "full_research_report.html").exists()
        assert (tmp_path / "research" / "reports" / "daily_momentum_breakout" / "fast_research_report.html").exists()
        assert (tmp_path / "research" / "reports" / "daily_momentum_breakout" / "strict_backtest_report.html").exists()
        assert (tmp_path / "research" / "reports" / "daily_momentum_breakout" / "walkforward_audit_report.html").exists()
        assert (tmp_path / "research" / "reports" / "latest" / "full_research_report.html").exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_formal_research_filters_by_idea_id():
    tmp_path = _test_root()
    second = RawStrategy(
        title="Daily Breakout Candidate",
        description="Daily OHLCV breakout strategy for liquid A-share equities.",
        source="arxiv",
        source_url="https://example.test/breakout",
        authors="Researcher",
        published_date="2026-02-01",
    )

    class FailingScout:
        def search(self, sources=None, max_results=10):
            raise AssertionError("formal mode must load from local idea bank")

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    try:
        research_store = FileResearchStore(tmp_path / "research")
        research_store.upsert_idea(_raw_strategy(), status="discovered", reason="seed")
        research_store.upsert_idea(second, status="discovered", reason="seed")
        selected_id = next(row["idea_id"] for row in research_store.list_ideas() if row["title"] == second.title)
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False),
            scout=FailingScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
        )

        result = engine.run_formal_research_from_idea_bank(idea_ids=[selected_id])

        assert result.discovered == 1
        assert result.integrated == 1
        assert research_store.list_ideas("candidate")[0]["title"] == second.title
        assert research_store.list_ideas("discovered")[0]["title"] == "Daily Momentum Breakout"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_strict_stage_runs_without_fast_or_walkforward():
    tmp_path = _test_root()
    events = []

    def record_backtest(sid, result, config, integrator, pool):
        events.append(sid)
        result.backtested += 1

    try:
        research_store = FileResearchStore(tmp_path / "research")
        research_store.upsert_candidate({
            "id": "daily_momentum_breakout",
            "name": "Daily Momentum Breakout",
            "status": "candidate",
            "research_meta": {},
        })
        research_store.upsert_hypothesis({
            "hypothesis_id": "h1",
            "strategy_id": "daily_momentum_breakout",
            "title": "Daily Momentum Breakout",
            "status": "candidate",
            "stage": "stage2_integrate",
            "source": "fixture",
            "source_url": "https://example.test",
            "thesis": "fixture",
            "decision_reason": "",
            "metrics": {"rank_ic": 0.03, "fdr_adjusted_p": 0.01, "hit_rate": 0.55},
            "evidence": {"strategy_spec": {"strategy_id": "daily_momentum_breakout", "universe": ["000300"]}},
        })
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=True, rigor_enabled=True),
            research_store=research_store,
            backtest_fn=record_backtest,
            strategies_dir=str(tmp_path / "strategies"),
        )

        result = engine.run_strict_backtest_stage(strategy_ids=["daily_momentum_breakout"])
        hypothesis = research_store.list_hypotheses()[0]
        stages = hypothesis["metrics"]["research_stage_conclusions"]

        assert events == ["daily_momentum_breakout"]
        assert result.backtested == 1
        assert stages["strict_backtest"]["verdict"] == "warn"
        assert "walkforward_strict_audit" not in stages
        assert (tmp_path / "research" / "reports" / "daily_momentum_breakout" / "strict_backtest_report.html").exists()
        assert (tmp_path / "research" / "reports" / "daily_momentum_breakout" / "fast_research_report.html").exists()
        assert (tmp_path / "research" / "reports" / "daily_momentum_breakout" / "walkforward_audit_report.html").exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_strict_stage_refreshes_existing_needs_more_validation_reason():
    tmp_path = _test_root()

    def record_backtest(sid, result, config, integrator, pool):
        result.backtested += 1

    try:
        research_store = FileResearchStore(tmp_path / "research")
        research_store.upsert_candidate({
            "id": "ashare_gold_equity_barbell_timing",
            "name": "ETF Timing",
            "status": "needs_more_validation",
            "research_meta": {"needs_more_validation_reason": "old small cap reason"},
        })
        research_store.upsert_hypothesis({
            "hypothesis_id": "h1",
            "strategy_id": "ashare_gold_equity_barbell_timing",
            "title": "ETF Timing",
            "status": "needs_more_validation",
            "stage": "rigor",
            "source": "fixture",
            "source_url": "https://example.test",
            "thesis": "fixture",
            "decision_reason": "",
            "metrics": {
                "strict_backtest": {
                    "metrics": {
                        "cagr": 0.1144,
                        "max_drawdown_pct": -0.1968,
                        "total_trades": 198,
                    },
                    "capacity": {"max_adv_participation": 0.02},
                    "data_quality": {
                        "survivorship_audit": {
                            "kind": "etf_metadata_survivorship_audit",
                            "material": True,
                            "bar_symbols_missing_fund_meta": 13,
                            "fund_meta_delisted_symbols": 0,
                        }
                    },
                }
            },
            "evidence": {"strategy_spec": {"strategy_id": "ashare_gold_equity_barbell_timing", "universe": ["510050"]}},
        })
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=True, rigor_enabled=True),
            research_store=research_store,
            backtest_fn=record_backtest,
            strategies_dir=str(tmp_path / "strategies"),
        )

        engine.run_strict_backtest_stage(strategy_ids=["ashare_gold_equity_barbell_timing"])

        candidate = research_store.get_candidate("ashare_gold_equity_barbell_timing")
        reason = candidate["research_meta"]["needs_more_validation_reason"]
        assert "ETF 日线中有 13 个 symbol 缺少基金元数据" in reason
        assert "old small cap reason" not in reason
        assert "起点主代表 ETF universe" not in reason
        assert "小市值 Top20" not in reason
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_walkforward_stage_runs_without_strict_backtest():
    tmp_path = _test_root()
    calls = []

    class FixedRigorHub:
        def run_walkforward(self, strategy_id, symbols, start, end):
            calls.append((strategy_id, tuple(symbols), start, end))
            return type(
                "WalkForward",
                (),
                {
                    "is_viable": True,
                    "aggregate_oos_sharpe": 1.2,
                    "worst_oos_sharpe": 0.4,
                    "pct_profitable_splits": 0.8,
                    "deflated_sharpe_ratio": 0.98,
                    "splits": [],
                },
            )()

    def fail_backtest(*args, **kwargs):
        raise AssertionError("walkforward stage must not run strict backtest")

    try:
        research_store = FileResearchStore(tmp_path / "research")
        research_store.upsert_candidate({
            "id": "daily_momentum_breakout",
            "name": "Daily Momentum Breakout",
            "status": "candidate",
            "research_meta": {"strategy_spec": {"universe": ["000300"]}},
        })
        research_store.upsert_hypothesis({
            "hypothesis_id": "h1",
            "strategy_id": "daily_momentum_breakout",
            "title": "Daily Momentum Breakout",
            "status": "candidate",
            "stage": "stage2_integrate",
            "source": "fixture",
            "source_url": "https://example.test",
            "thesis": "fixture",
            "decision_reason": "",
            "metrics": {"rank_ic": 0.03, "fdr_adjusted_p": 0.01, "hit_rate": 0.55},
            "evidence": {"strategy_spec": {"strategy_id": "daily_momentum_breakout", "universe": ["000300"]}},
        })
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False, rigor_enabled=True),
            research_store=research_store,
            backtest_fn=fail_backtest,
            rigor_hub=FixedRigorHub(),
            strategies_dir=str(tmp_path / "strategies"),
        )

        result = engine.run_walkforward_audit_stage(strategy_ids=["daily_momentum_breakout"])
        hypothesis = research_store.list_hypotheses()[0]
        stages = hypothesis["metrics"]["research_stage_conclusions"]

        assert calls and calls[0][0] == "daily_momentum_breakout"
        assert result.backtested == 0
        assert result.walkforward_passed == 1
        assert stages["walkforward_strict_audit"]["verdict"] == "pass"
        assert "strict_backtest" not in stages
        assert (tmp_path / "research" / "reports" / "daily_momentum_breakout" / "walkforward_audit_report.html").exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_walkforward_stage_uses_persistent_rejected_candidate_metadata():
    tmp_path = _test_root()
    captured = {}

    class FixedRigorHub:
        def run_walkforward(self, strategy_id, symbols, start, end, benchmark_data=None, strategy_archive_dir="", initial_cash=0):
            captured["strategy_id"] = strategy_id
            captured["symbols"] = list(symbols)
            captured["strategy_archive_dir"] = strategy_archive_dir
            captured["initial_cash"] = initial_cash
            return SimpleNamespace(
                is_viable=False,
                aggregate_oos_sharpe=-0.1,
                worst_oos_sharpe=-0.2,
                deflated_sharpe_ratio=None,
                pct_profitable_splits=0.0,
                splits=[],
            )

    try:
        archive_dir = tmp_path / "rejected_strategy" / "persisted_rejected"
        archive_dir.mkdir(parents=True)
        research_store = FileResearchStore(tmp_path / "research")
        research_store.upsert_candidate({
            "id": "persisted_rejected",
            "name": "Persisted Rejected",
            "status": "rejected",
            "research_meta": {
                "strategy_spec": {"universe": ["600001", "600002"]},
                "rejected_strategy_dir": str(archive_dir),
            },
        })
        research_store.upsert_hypothesis({
            "hypothesis_id": "h1",
            "strategy_id": "persisted_rejected",
            "title": "Persisted Rejected",
            "status": "rejected",
            "stage": "go_no_go",
            "source": "fixture",
            "source_url": "https://example.test",
            "thesis": "fixture",
            "decision_reason": "strict failed",
            "metrics": {},
            "evidence": {"strategy_spec": {"strategy_id": "persisted_rejected", "universe": ["600001", "600002"]}},
        })
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False, rigor_enabled=True, default_symbols=["000300", "600519"]),
            research_store=research_store,
            rigor_hub=FixedRigorHub(),
            strategies_dir=str(tmp_path / "strategies"),
        )

        engine.run_walkforward_audit_stage(strategy_ids=["persisted_rejected"])

        assert captured["strategy_id"] == "persisted_rejected"
        assert captured["symbols"] == ["600001", "600002"]
        assert captured["strategy_archive_dir"].replace("\\", "/").endswith("rejected_strategy/persisted_rejected")
        assert captured["initial_cash"] == pytest.approx(10_000)
        assert (tmp_path / "research" / "reports" / "persisted_rejected" / "walkforward_audit_report.html").exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_stage_report_replaces_corrupt_decision_reason():
    from quant.infrastructure.research.reporting import build_research_stage_report_html

    report = build_research_stage_report_html(
        "fast_research",
        {"run_id": "encoding_guard", "backtested": 1, "walkforward_passed": 0},
        [
            {
                "title": "Encoding Guard",
                "status": "rejected",
                "stage": "go_no_go",
                "decision_reason": "?? FDR ?????????",
                "metrics": {
                    "fdr_adjusted_p": 0.41,
                    "rank_ic": 0.008,
                    "strict_backtest": {"metrics": {"sharpe": 0.5}},
                },
            }
        ],
    )

    assert "?? FDR" not in report
    assert "信号 FDR 不显著" in report


def test_research_engine_records_candidate_hypothesis_ledger():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
        )

        result = engine.run_full_pipeline()
        hypotheses = research_store.list_hypotheses("candidate")

        assert result.integrated == 1
        assert len(hypotheses) == 1
        assert hypotheses[0]["strategy_id"] == "daily_momentum_breakout"
        assert hypotheses[0]["title"] == "Daily Momentum Breakout"
        assert hypotheses[0]["stage"] == "stage2_integrate"
        assert hypotheses[0]["metrics"]["suitability_score"] == pytest.approx(7.5)
        assert hypotheses[0]["metrics"]["estimated_edge"] == pytest.approx(0.08)
        assert "admission_score" in hypotheses[0]["metrics"]
        assert "signal_quality_score" in hypotheses[0]["metrics"]
        assert hypotheses[0]["evidence"]["source_url"] == "https://example.test/paper"
        assert any(entry.phase == "stage1_queue" for entry in result.log)
        assert any(entry.phase == "stage2_integrate" for entry in result.log)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_writes_promotion_dossier_artifact():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    class RecordingArtifactStore:
        def __init__(self):
            self.saved = []

        def save_json(self, run_id, name, data):
            self.saved.append((run_id, name, data))
            return {"artifact_id": "artifact-1", "name": name, "path": f"/tmp/{name}.json"}

    try:
        artifact_store = RecordingArtifactStore()
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            artifact_store=artifact_store,
            strategies_dir=str(tmp_path / "strategies"),
        )

        result = engine.run_full_pipeline()
        candidate = research_store.get_candidate("daily_momentum_breakout")

        assert result.integrated == 1
        run_id, name, dossier = next(item for item in artifact_store.saved if item[1] == "promotion_dossier_daily_momentum_breakout")
        assert run_id == "research_pipeline"
        assert name == "promotion_dossier_daily_momentum_breakout"
        assert dossier["strategy_id"] == "daily_momentum_breakout"
        assert dossier["hypothesis"]["title"] == "Daily Momentum Breakout"
        assert dossier["evaluation"]["suitability_score"] == pytest.approx(7.5)
        assert "admission_score" in dossier["evaluation"]
        assert "validation_tests" in dossier
        assert dossier["risk_flags"] == ["survivorship_bias"]
        assert dossier["next_action"] == "walk_forward_or_paper_review"
        assert candidate["research_meta"]["promotion_dossier_artifact"]["artifact_id"] == "artifact-1"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_records_lineage_manifest_for_tracked_run():
    tmp_path = _test_root()

    class EmptyScout:
        def search(self, sources=None, max_results=10):
            return []

    class RecordingExperimentStore:
        def __init__(self):
            self.started = []
            self.completed = []

        def start_run(self, strategy_id, metadata):
            self.started.append((strategy_id, metadata))
            return "run-lineage"

        def complete_run(self, run_id, status, error=None):
            self.completed.append((run_id, status, error))

    class RecordingArtifactStore:
        def __init__(self):
            self.saved = []

        def save_json(self, run_id, name, data):
            self.saved.append((run_id, name, data))
            return {"artifact_id": "lineage-1", "name": name}

    experiment_store = RecordingExperimentStore()
    artifact_store = RecordingArtifactStore()
    engine = ResearchEngine(
        config=ResearchConfig(
            auto_backtest=False,
            sources=["arxiv"],
            default_symbols=["SPY", "QQQ"],
            default_backtest_start="2021-01-01",
            default_backtest_end="2024-12-31",
            llm_api_key="secret-key",
        ),
        scout=EmptyScout(),
        research_store=FileResearchStore(tmp_path / "research"),
        experiment_store=experiment_store,
        artifact_store=artifact_store,
        strategies_dir=str(tmp_path / "strategies"),
    )

    result = engine.run_full_pipeline(sources=["arxiv"])

    assert result.run_id == "run-lineage"
    assert experiment_store.started[0][0] == "research_pipeline"
    metadata = experiment_store.started[0][1]
    assert metadata["manifest_version"] == 1
    assert len(metadata["config_hash"]) == 16
    assert len(metadata["data_hash"]) == 16
    assert metadata["data_summary"]["sources"] == ["arxiv"]
    assert metadata["data_summary"]["default_symbols"] == ["SPY", "QQQ"]
    assert metadata["config_summary"]["llm_api_key"] == "***"
    assert artifact_store.saved[0][0] == "run-lineage"
    assert artifact_store.saved[0][1] == "lineage_manifest"
    assert artifact_store.saved[0][2]["run_id"] == "run-lineage"
    assert artifact_store.saved[0][2]["config_hash"] == metadata["config_hash"]


def test_research_engine_records_lineage_manifest_without_tracking():
    tmp_path = _test_root()

    class EmptyScout:
        def search(self, sources=None, max_results=10):
            return []

    class RecordingArtifactStore:
        def __init__(self):
            self.saved = []

        def save_json(self, run_id, name, data):
            self.saved.append((run_id, name, data))
            return {"artifact_id": "lineage-2", "name": name}

    artifact_store = RecordingArtifactStore()
    engine = ResearchEngine(
        config=ResearchConfig(auto_backtest=False, tracking_enabled=False, sources=["ssrn"]),
        scout=EmptyScout(),
        research_store=FileResearchStore(tmp_path / "research"),
        artifact_store=artifact_store,
        strategies_dir=str(tmp_path / "strategies"),
    )

    result = engine.run_full_pipeline()

    assert result.run_id is None
    assert artifact_store.saved[0][0] == "research_pipeline"
    assert artifact_store.saved[0][1] == "lineage_manifest"
    assert artifact_store.saved[0][2]["run_id"] is None
    assert artifact_store.saved[0][2]["data_summary"]["sources"] == ["ssrn"]


def test_research_engine_writes_candidate_scorecard_artifact():
    tmp_path = _test_root()

    high = _raw_strategy()
    low = RawStrategy(
        title="Fragile Intraday Microstructure",
        description="Requires intraday order book effects and does not adapt cleanly to daily bars.",
        source="ssrn",
        source_url="https://example.test/fragile",
    )

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [high, low]

    class MixedEvaluator:
        def evaluate(self, raw):
            report = _evaluation_report()
            if raw.title == low.title:
                report.suitability_score = 2.0
                report.estimated_edge = 0.01
                report.risk_flags = ["hf_not_daily"]
            return report

    class RecordingArtifactStore:
        def __init__(self):
            self.tables = []

        def save_json(self, run_id, name, data):
            return {"artifact_id": f"json-{name}", "name": name}

        def save_table(self, run_id, name, table):
            self.tables.append((run_id, name, table))
            return {"artifact_id": "scorecard-1", "name": name}

    try:
        artifact_store = RecordingArtifactStore()
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False, evaluation_threshold=6.0),
            scout=FixedScout(),
            evaluator=MixedEvaluator(),
            research_store=FileResearchStore(tmp_path / "research"),
            artifact_store=artifact_store,
            strategies_dir=str(tmp_path / "strategies"),
        )

        result = engine.run_full_pipeline()

        run_id, name, rows = next(item for item in artifact_store.tables if item[1] == "candidate_scorecard")
        assert result.integrated == 1
        assert result.rejected == 1
        assert run_id == "research_pipeline"
        assert name == "candidate_scorecard"
        assert [row["title"] for row in rows] == ["Daily Momentum Breakout", "Fragile Intraday Microstructure"]
        assert rows[0]["status"] == "candidate"
        assert rows[0]["strategy_id"] == "daily_momentum_breakout"
        assert rows[0]["suitability_score"] == pytest.approx(7.5)
        assert "admission_score" in rows[0]
        assert "signal_quality_score" in rows[0]
        assert rows[1]["status"] == "rejected"
        assert rows[1]["suitability_score"] == pytest.approx(2.0)
        assert "suitability=2.0" in rows[1]["decision_reason"]
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_records_rejected_hypothesis_ledger():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class LowScoreEvaluator:
        def evaluate(self, raw):
            report = _evaluation_report()
            report.suitability_score = 3.0
            return report

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False, evaluation_threshold=6.0),
            scout=FixedScout(),
            evaluator=LowScoreEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
        )

        result = engine.run_full_pipeline()
        hypotheses = research_store.list_hypotheses("rejected")

        assert result.rejected == 1
        assert len(hypotheses) == 1
        assert hypotheses[0]["strategy_id"] == ""
        assert hypotheses[0]["stage"] == "stage1_admission"
        assert "suitability=3.0" in hypotheses[0]["decision_reason"]
        assert hypotheses[0]["metrics"]["suitability_score"] == pytest.approx(3.0)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_candidate_pool_updates_persistent_status():
    tmp_path = _test_root()
    research_store = FileResearchStore(tmp_path / "research")
    try:
        research_store.upsert_candidate(
            {
                "id": "daily_momentum_breakout",
                "name": "Daily Momentum Breakout",
                "status": "candidate",
                "research_meta": {"suitability_score": 7.5},
            }
        )

        pool = CandidatePool(research_store=research_store)

        assert pool.promote("daily_momentum_breakout") is True
        assert research_store.get_candidate("daily_momentum_breakout")["status"] == "paused"
        assert pool.promote("daily_momentum_breakout") is False
        assert CandidatePool(research_store=research_store).list_candidates() == []
        assert CandidatePool(research_store=research_store).get_research_meta("daily_momentum_breakout") == {"suitability_score": 7.5}
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_runs_strict_backtest_before_pausing_low_dsr_candidate():
    tmp_path = _test_root()
    events = []

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    class LowDsrRigorHub:
        def run_walkforward(self, strategy_id, symbols, start, end):
            events.append("walkforward")
            return type(
                "WalkForward",
                (),
                {
                    "is_viable": True,
                    "worst_oos_sharpe": 0.7,
                    "deflated_sharpe_ratio": 0.5,
                },
            )()

    def record_backtest(sid, result, config, integrator, pool):
        events.append("strict")
        result.backtested += 1

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=True, rigor_enabled=True),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
            backtest_fn=record_backtest,
            rigor_hub=LowDsrRigorHub(),
        )

        result = engine.run_full_pipeline()

        candidate = research_store.get_candidate("daily_momentum_breakout")
        hypothesis = research_store.list_hypotheses()[0]
        stages = hypothesis["metrics"]["research_stage_conclusions"]
        assert events == ["strict", "walkforward"]
        assert result.rejected == 0
        assert result.backtested == 1
        assert result.walkforward_passed == 0
        assert result.errors == []
        assert candidate["status"] == "needs_more_validation"
        assert candidate["research_meta"]["dsr_warning"] == pytest.approx(0.5)
        assert stages["fast_research"]["verdict"] == "warn"
        assert stages["strict_backtest"]["verdict"] == "warn"
        assert stages["walkforward_strict_audit"]["verdict"] == "warn"
        assert stages["final_decision"]["verdict"] == "warn"
        assert any(entry.phase == "rigor" and entry.verdict == "warning" for entry in result.log)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_nonviable_walkforward_rejects_candidate_and_updates_ledger():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    class FixedSpecBuilder:
        def build(self, raw, report):
            return StrategySpec(
                strategy_id="daily_momentum_breakout",
                strategy_type="momentum",
                signal_formula_key="momentum_close_return",
                universe=["000300"],
                horizon_days=5,
                lookback_days=20,
                execution_lag_days=1,
                required_fields=["close"],
                status="ready",
            )

    class FixedValidator:
        def validate(self, spec):
            return ValidationReport(
                strategy_id=spec.strategy_id,
                status="validated",
                rank_ic=0.05,
                rank_ic_ir=1.0,
                ic_decay=[(1, 0.05), (5, 0.04), (10, 0.03), (21, 0.025)],
                fdr_adjusted_p=0.01,
                fdr_significant=True,
                ff_alpha_monthly=0.0,
                ff_alpha_tstat=0.0,
                ff_r2=0.0,
                long_short_spread=0.0,
                hit_rate=0.55,
                data_start="2020-01-01",
                data_end="2020-12-31",
                n_observations=120,
            )

    class NonviableRigorHub:
        def run_walkforward(self, strategy_id, symbols, start, end):
            return type(
                "WalkForward",
                (),
                {
                    "is_viable": False,
                    "aggregate_oos_sharpe": -1.2,
                    "worst_oos_sharpe": -4.6,
                    "pct_profitable_splits": 0.2,
                    "deflated_sharpe_ratio": 0.0,
                    "capacity_ok": False,
                },
            )()

    def record_backtest(sid, result, config, integrator, pool):
        result.backtested += 1

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(
                auto_backtest=True,
                rigor_enabled=True,
                rigor_config={
                    "purged_walkforward": {"test_window_days": 42},
                    "thresholds": {"min_worst_oos_sharpe": 0.4, "min_profitable_splits_pct": 0.6},
                    "cost_model": {"max_adv_pct": 0.02},
                },
            ),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
            backtest_fn=record_backtest,
            rigor_hub=NonviableRigorHub(),
            spec_builder=FixedSpecBuilder(),
            validator=FixedValidator(),
        )

        result = engine.run_full_pipeline()

        candidate = research_store.get_candidate("daily_momentum_breakout")
        hypothesis = research_store.list_hypotheses()[0]
        idea = research_store.list_ideas()[0]
        assert result.rejected == 0
        assert result.backtested == 1
        assert candidate["status"] == "needs_more_validation"
        assert (tmp_path / "strategies" / "daily_momentum_breakout" / "strategy.py").exists()
        assert not (tmp_path / "rejected_strategy" / "daily_momentum_breakout" / "strategy.py").exists()
        assert hypothesis["status"] == "needs_more_validation"
        assert hypothesis["stage"] == "rigor"
        assert "strict Backtester 未返回结构化结果" in hypothesis["decision_reason"]
        walkforward = hypothesis["metrics"]["walkforward"]
        assert walkforward["aggregate_oos_sharpe"] == pytest.approx(-1.2)
        assert walkforward["worst_oos_sharpe"] == pytest.approx(-4.6)
        assert walkforward["capacity_ok"] is False
        assert walkforward["thresholds"]["test_window_days"] == 42
        assert walkforward["thresholds"]["min_worst_oos_sharpe"] == pytest.approx(0.4)
        assert walkforward["thresholds"]["min_profitable_splits_pct"] == pytest.approx(0.6)
        assert walkforward["thresholds"]["max_adv_pct"] == pytest.approx(0.02)
        assert walkforward["verdict"] == "fail"
        stages = hypothesis["metrics"]["research_stage_conclusions"]
        assert stages["fast_research"]["verdict"] == "pass"
        assert stages["strict_backtest"]["verdict"] == "warn"
        assert stages["walkforward_strict_audit"]["verdict"] == "fail"
        assert stages["final_decision"]["verdict"] == "warn"
        assert idea["status"] != "rejected"
        assert any(entry.phase == "stage2_validation" and entry.verdict == "info" for entry in result.log)
        assert any(entry.phase == "rigor" and entry.verdict == "info" for entry in result.log)
        assert any(entry.phase == "rigor" and entry.verdict == "fail" for entry in result.log)
        rigor_fail = next(entry for entry in result.log if entry.phase == "rigor" and entry.verdict == "fail")
        assert rigor_fail.scores["deflated_sharpe_ratio"] == pytest.approx(0.0)
        assert any(entry.phase == "backtest" and entry.verdict == "info" for entry in result.log)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_ic_decay_warning_is_logged_without_rejecting():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    class FixedSpecBuilder:
        def build(self, raw, report):
            return StrategySpec(
                strategy_id="daily_momentum_breakout",
                strategy_type="momentum",
                signal_formula_key="momentum_close_return",
                universe=["SPY"],
                horizon_days=5,
                lookback_days=20,
                execution_lag_days=1,
                required_fields=["close"],
                status="ready",
            )

    class FixedValidator:
        def validate(self, spec):
            return ValidationReport(
                strategy_id=spec.strategy_id,
                status="validated",
                rank_ic=0.05,
                rank_ic_ir=1.0,
                ic_decay=[(1, 0.05), (5, 0.03), (10, 0.02), (21, 0.01)],
                fdr_adjusted_p=0.01,
                fdr_significant=True,
                ff_alpha_monthly=0.0,
                ff_alpha_tstat=0.0,
                ff_r2=0.0,
                long_short_spread=0.0,
                hit_rate=0.55,
                data_start="2020-01-01",
                data_end="2020-12-31",
                n_observations=120,
            )

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False, validation_enabled=True),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
            spec_builder=FixedSpecBuilder(),
            validator=FixedValidator(),
        )

        result = engine.run_full_pipeline()

        warnings = [entry for entry in result.log if entry.phase == "stage2_validation" and entry.verdict == "warn"]
        assert result.integrated == 1
        assert result.rejected == 0
        assert any("high_ic_decay" in entry.reason or "high_ic_decay" in entry.scores for entry in warnings)
        assert any("high_ic_decay" in entry.scores.get("errors", []) for entry in warnings)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_passes_ready_strategy_spec_to_integrator():
    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    class FixedSpecBuilder:
        def build(self, raw, report):
            return StrategySpec(
                strategy_id="daily_momentum_breakout",
                strategy_type="momentum",
                signal_formula_key="momentum_close_return",
                universe=["SPY"],
                horizon_days=5,
                lookback_days=20,
                execution_lag_days=1,
                required_fields=["close"],
                status="ready",
            )

    class FixedValidator:
        def validate(self, spec):
            return ValidationReport(
                strategy_id=spec.strategy_id,
                status="validated",
                rank_ic=0.05,
                rank_ic_ir=1.0,
                ic_decay=[(1, 0.05), (5, 0.04), (10, 0.03), (21, 0.025)],
                fdr_adjusted_p=0.01,
                fdr_significant=True,
                ff_alpha_monthly=0.0,
                ff_alpha_tstat=0.0,
                ff_r2=0.0,
                long_short_spread=0.0,
                hit_rate=0.55,
                data_start="2020-01-01",
                data_end="2020-12-31",
                n_observations=120,
            )

    class RecordingIntegrator:
        registry = {}

        def __init__(self):
            self.received_spec = None

        def integrate(self, raw, report, spec=None):
            self.received_spec = spec
            return "daily_momentum_breakout"

    integrator = RecordingIntegrator()
    engine = ResearchEngine(
        config=ResearchConfig(auto_backtest=False, validation_enabled=True),
        scout=FixedScout(),
        evaluator=FixedEvaluator(),
        integrator=integrator,
        spec_builder=FixedSpecBuilder(),
        validator=FixedValidator(),
    )

    result = engine.run_full_pipeline()

    assert result.integrated == 1
    assert integrator.received_spec is not None
    assert integrator.received_spec.signal_formula_key == "momentum_close_return"


def test_fast_validation_gate_fails_weak_after_cost_portfolio_diagnostics():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    class FixedSpecBuilder:
        def build(self, raw, report):
            return StrategySpec(
                strategy_id="daily_momentum_breakout",
                strategy_type="momentum",
                signal_formula_key="momentum_close_return",
                universe=["600519"],
                horizon_days=5,
                lookback_days=20,
                execution_lag_days=1,
                required_fields=["close"],
                status="ready",
            )

    class WeakPortfolioValidator:
        def validate(self, spec):
            return ValidationReport(
                strategy_id=spec.strategy_id,
                status="validated",
                rank_ic=0.05,
                rank_ic_ir=1.0,
                ic_decay=[(1, 0.05), (5, 0.04), (10, 0.03), (21, 0.025)],
                fdr_adjusted_p=0.01,
                fdr_significant=True,
                ff_alpha_monthly=0.0,
                ff_alpha_tstat=0.0,
                ff_r2=0.0,
                long_short_spread=0.0,
                hit_rate=0.55,
                data_start="2020-01-01",
                data_end="2020-12-31",
                n_observations=120,
                portfolio_diagnostics={
                    "top_bucket_after_cost_sharpe": 0.20,
                    "top_bucket_after_cost_annualized_return": 0.04,
                    "top_bucket_after_cost_max_drawdown": -0.75,
                    "benchmark_excess_after_cost_sharpe": -0.10,
                    "benchmark_excess_after_cost_annualized_return": -0.02,
                    "top_bucket_turnover": 0.10,
                    "rolling_oos": [
                        {"split": "2020", "annualized_return": 0.05},
                        {"split": "2021", "annualized_return": -0.02},
                        {"split": "2022", "annualized_return": -0.01},
                    ],
                },
            )

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=False, validation_enabled=True),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
            spec_builder=FixedSpecBuilder(),
            validator=WeakPortfolioValidator(),
        )

        result = engine.run_full_pipeline()

        candidate = research_store.get_candidate("daily_momentum_breakout")
        hypothesis = research_store.list_hypotheses()[0]
        gate = candidate["research_meta"]["validation_gate"]
        assert result.integrated == 1
        assert result.validated_passed == 0
        assert gate["status"] == "failed"
        assert "top_bucket_after_cost_sharpe" in gate["reason"]
        assert gate["metrics"]["top_bucket_after_cost_max_drawdown"] == pytest.approx(-0.75)
        assert gate["metrics"]["rolling_oos_positive_pct"] == pytest.approx(1 / 3)
        assert hypothesis["metrics"]["research_stage_conclusions"]["fast_research"]["verdict"] == "fail"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_continues_after_negative_rank_ic_direction():
    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    class FixedSpecBuilder:
        def build(self, raw, report):
            return StrategySpec(
                strategy_id="daily_momentum_breakout",
                strategy_type="momentum",
                signal_formula_key="momentum_close_return",
                universe=["SPY"],
                horizon_days=5,
                lookback_days=20,
                execution_lag_days=1,
                required_fields=["close"],
                status="ready",
            )

    class NegativeValidator:
        def validate(self, spec):
            return ValidationReport(
                strategy_id=spec.strategy_id,
                status="validated",
                rank_ic=-0.05,
                rank_ic_ir=-1.0,
                ic_decay=[(1, -0.04), (5, -0.05), (10, -0.03), (21, -0.02)],
                fdr_adjusted_p=0.01,
                fdr_significant=True,
                ff_alpha_monthly=0.0,
                ff_alpha_tstat=0.0,
                ff_r2=0.0,
                long_short_spread=-0.001,
                hit_rate=0.45,
                data_start="2020-01-01",
                data_end="2020-12-31",
                n_observations=120,
            )

    class RecordingIntegrator:
        registry = {}

        def __init__(self):
            self.called = False

        def integrate(self, raw, report, spec=None):
            self.called = True
            return "daily_momentum_breakout"

    integrator = RecordingIntegrator()
    engine = ResearchEngine(
        config=ResearchConfig(auto_backtest=False, validation_enabled=True),
        scout=FixedScout(),
        evaluator=FixedEvaluator(),
        integrator=integrator,
        spec_builder=FixedSpecBuilder(),
        validator=NegativeValidator(),
    )

    result = engine.run_full_pipeline()

    assert result.integrated == 1
    assert result.rejected == 0
    assert integrator.called is True
    assert any(entry.phase == "stage2_validation" and entry.verdict == "fail" for entry in result.log)


def test_validation_failed_strategy_runs_backtest_then_archives_to_rejected_strategy():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    class FixedSpecBuilder:
        def build(self, raw, report):
            return StrategySpec(
                strategy_id="daily_momentum_breakout",
                strategy_type="momentum",
                signal_formula_key="momentum_close_return",
                universe=["000300"],
                horizon_days=5,
                lookback_days=20,
                execution_lag_days=1,
                required_fields=["close"],
                status="ready",
            )

    class NegativeValidator:
        def validate(self, spec):
            return ValidationReport(
                strategy_id=spec.strategy_id,
                status="validated",
                rank_ic=-0.05,
                rank_ic_ir=-1.0,
                ic_decay=[(1, -0.04), (5, -0.05), (10, -0.03), (21, -0.02)],
                fdr_adjusted_p=0.01,
                fdr_significant=True,
                ff_alpha_monthly=0.0,
                ff_alpha_tstat=0.0,
                ff_r2=0.0,
                long_short_spread=-0.001,
                hit_rate=0.45,
                data_start="2020-01-01",
                data_end="2020-12-31",
                n_observations=120,
            )

    def record_backtest(sid, result, config, integrator, pool):
        result.backtested += 1

    try:
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(auto_backtest=True, validation_enabled=True),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
            backtest_fn=record_backtest,
            spec_builder=FixedSpecBuilder(),
            validator=NegativeValidator(),
        )

        result = engine.run_full_pipeline()

        candidate = research_store.get_candidate("daily_momentum_breakout")
        hypothesis = research_store.list_hypotheses()[0]
        idea = research_store.list_ideas()[0]
        assert result.integrated == 1
        assert result.backtested == 1
        assert result.rejected == 1
        assert candidate["status"] == "rejected"
        assert candidate["research_meta"]["validation_gate"]["status"] == "failed"
        assert candidate["research_meta"]["rejected_strategy_dir"].endswith(
            "rejected_strategy/daily_momentum_breakout"
        )
        assert not (tmp_path / "strategies" / "daily_momentum_breakout" / "strategy.py").exists()
        assert (tmp_path / "rejected_strategy" / "daily_momentum_breakout" / "strategy.py").exists()
        assert hypothesis["status"] == "rejected"
        assert hypothesis["stage"] == "go_no_go"
        assert "Validation failed" in hypothesis["decision_reason"]
        assert "strict Backtester executed for audit" in hypothesis["decision_reason"]
        stages = hypothesis["metrics"]["research_stage_conclusions"]
        assert stages["fast_research"]["verdict"] == "fail"
        assert stages["strict_backtest"]["verdict"] == "warn"
        assert stages["walkforward_strict_audit"]["verdict"] == "not_run"
        assert stages["final_decision"]["verdict"] == "fail"
        assert idea["status"] == "rejected"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_research_engine_uses_strategy_spec_universe_for_walkforward():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    class FixedSpecBuilder:
        def build(self, raw, report):
            return StrategySpec(
                strategy_id="daily_momentum_breakout",
                strategy_type="momentum",
                signal_formula_key="momentum_close_return",
                universe=["600519"],
                horizon_days=5,
                lookback_days=20,
                execution_lag_days=1,
                required_fields=["close"],
                status="ready",
            )

    class FixedValidator:
        def validate(self, spec):
            return ValidationReport(
                strategy_id=spec.strategy_id,
                status="validated",
                rank_ic=0.05,
                rank_ic_ir=1.0,
                ic_decay=[(1, 0.05), (5, 0.04), (10, 0.03), (21, 0.025)],
                fdr_adjusted_p=0.01,
                fdr_significant=True,
                ff_alpha_monthly=0.0,
                ff_alpha_tstat=0.0,
                ff_r2=0.0,
                long_short_spread=0.0,
                hit_rate=0.55,
                data_start="2020-01-01",
                data_end="2020-12-31",
                n_observations=120,
            )

    class RecordingRigorHub:
        def __init__(self):
            self.symbols = None

        def run_walkforward(self, strategy_id, symbols, start, end):
            self.symbols = symbols
            return type(
                "WalkForward",
                (),
                {
                    "is_viable": True,
                    "worst_oos_sharpe": 1.0,
                    "deflated_sharpe_ratio": 0.99,
                },
            )()

    try:
        rigor_hub = RecordingRigorHub()
        engine = ResearchEngine(
            config=ResearchConfig(
                auto_backtest=True,
                rigor_enabled=True,
                default_symbols=["000300", "000905"],
            ),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=FileResearchStore(tmp_path / "research"),
            strategies_dir=str(tmp_path / "strategies"),
            spec_builder=FixedSpecBuilder(),
            validator=FixedValidator(),
            rigor_hub=rigor_hub,
            backtest_fn=lambda *args, **kwargs: None,
        )

        result = engine.run_full_pipeline()

        assert result.integrated == 1
        assert rigor_hub.symbols == ["600519"]
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_walkforward_trade_enrichment_adds_capacity_fields():
    from quant.api.research_bp import _serialize_walkforward_trade

    class Trade:
        symbol = "SPY"
        side = "BUY"
        quantity = 50
        fill_price = 20.0
        pnl = 0.0
        fill_date = "2020-01-03"

    data = pd.DataFrame(
        [
            {"symbol": "SPY", "timestamp": "2020-01-02", "volume": 10_000},
            {"symbol": "SPY", "timestamp": "2020-01-03", "volume": 20_000},
        ]
    )

    trade = _serialize_walkforward_trade(Trade(), data)

    assert trade["trade_value"] == pytest.approx(1_000.0)
    assert trade["avg_daily_volume"] == pytest.approx(20_000.0)


def test_research_config_default_backtest_window_spans_2016_to_2026():
    cfg = ResearchConfig()

    assert cfg.default_backtest_start == "2016-01-01"
    assert cfg.default_backtest_end == "2026-05-31"


def test_promoted_strategy_report_scripts_use_current_default_end_date():
    import runpy

    for script in (
        Path("quant/scripts/run_xueqiu_small_cap_financial_filter_strict_backtest.py"),
        Path("quant/scripts/run_ashare_gold_equity_barbell_timing_strict_backtest.py"),
    ):
        namespace = runpy.run_path(str(script))
        assert namespace["END"].date().isoformat() == "2026-05-31"


def test_broad_asset_etf_rotation_report_script_uses_domestic_default_universe():
    import runpy

    script = Path("quant/scripts/run_ashare_broad_asset_etf_rotation_strict_backtest.py")

    namespace = runpy.run_path(str(script))

    assert namespace["START"].date().isoformat() == "2016-01-01"
    assert namespace["END"].date().isoformat() == "2026-05-31"
    scenario = namespace["SCENARIOS"][0]
    assert scenario["name"] == "monthly_126d_vol60_continuous_tilt70_domestic"
    assert scenario["weight_mode"] == "continuous_branch_tilt"
    assert scenario["tilt_strength"] == pytest.approx(0.70)
    assert scenario["target_exposure"] == pytest.approx(1.0)
    assert "csi1000" in namespace["DEFAULT_CATEGORY_SYMBOLS"]
    assert scenario["category_symbols"]["csi1000"] == ["512100"]
    assert any(item["tilt_strength"] == pytest.approx(0.50) for item in namespace["SCENARIOS"][1:])
    blocked = {"nasdaq", "hsi", "hshares", "china_internet", "cross_border"}
    assert blocked.isdisjoint(set(scenario["category_symbols"]))


def test_broad_asset_etf_rotation_report_script_keeps_registered_missing_data_buckets():
    import runpy

    namespace = runpy.run_path(str(Path("quant/scripts/run_ashare_broad_asset_etf_rotation_strict_backtest.py")))
    universe = {
        "category_symbols": {
            "sse50": ["510050"],
            "csi300": ["510300"],
            "csi1000": ["512100"],
            "chinext": ["159915"],
            "chinext50": ["159949"],
            "dividend": ["510880"],
            "gold": ["518880"],
            "cash": ["511990"],
            "bond_rate": [],
        },
        "registered_universe_counts": {"registered_symbol_count": 9, "active_symbol_count": 8, "missing_data_count": 1},
        "universe_registry_version": "audited_stable_etf_registry_v1",
        "universe_selection_policy": "audited_stable_etf_registry",
    }

    scenario = namespace["_with_pit_universe"](namespace["SCENARIOS"][0], universe)

    assert scenario["category_symbols"]["bond_rate"] == ["511010"]
    assert "511010" in scenario["symbols"]
    assert scenario["missing_pit_categories"] == ["bond_rate"]


def test_broad_asset_etf_rotation_stability_runner_uses_one_factor_variants():
    from quant.scripts.run_ashare_broad_asset_etf_rotation_stability import PARAMETER_KEYS, _stability_variants
    from quant.scripts.run_ashare_broad_asset_etf_rotation_strict_backtest import SCENARIOS

    base = dict(SCENARIOS[0])
    base["symbols"] = ["510300", "518880"]
    variants = _stability_variants(base)

    assert len(variants) >= 10
    assert variants[0]["stability_variant"] == "base_locked"
    assert any(variant["stability_variant"] == "tilt_85" for variant in variants)
    base_params = {key: variants[0].get(key) for key in PARAMETER_KEYS}
    for variant in variants[1:]:
        changed = [key for key in PARAMETER_KEYS if variant.get(key) != base_params.get(key)]
        assert len(changed) == 1


def test_broad_asset_etf_rotation_stability_payload_keeps_best_as_audit_only():
    from quant.scripts.run_ashare_broad_asset_etf_rotation_stability import _build_parameter_sensitivity_payload

    base_scenario = {
        "momentum_lookback": 126,
        "trend_window": 120,
        "volatility_window": 60,
        "tilt_strength": 0.70,
        "temperature": 0.75,
        "min_branch_weight": 0.02,
        "max_branch_weight": 0.30,
        "rebalance_threshold": 0.02,
        "trend_penalty": 1.0,
        "holding_days": 20,
        "min_avg_turnover": 20_000_000,
        "target_exposure": 1.0,
    }
    rows = [
        {
            "variant": "base_locked",
            "parameters": {"momentum_lookback": 126},
            "cagr": 0.10,
            "max_drawdown_pct": -0.20,
            "sharpe": 0.8,
            "total_trades": 100,
            "max_adv_participation": 0.01,
        },
        {
            "variant": "lookback_84",
            "parameters": {"momentum_lookback": 84},
            "cagr": 0.095,
            "max_drawdown_pct": -0.21,
            "sharpe": 0.75,
            "total_trades": 96,
            "max_adv_participation": 0.01,
        },
        {
            "variant": "tilt_85",
            "parameters": {"tilt_strength": 0.85},
            "cagr": 0.02,
            "max_drawdown_pct": -0.25,
            "sharpe": 0.2,
            "total_trades": 70,
            "max_adv_participation": 0.01,
        },
    ]

    payload = _build_parameter_sensitivity_payload(base_scenario, rows)

    assert payload["tested_count"] == 3
    assert payload["pass_count"] == 2
    assert payload["best_params"] == {"momentum_lookback": 126}
    assert "audit evidence only" in payload["stability_note"]
    assert payload["rows"][2]["verdict"] == "warn"


def test_broad_asset_etf_rotation_strict_runner_defaults_to_followup_audits(monkeypatch):
    from quant.scripts import run_ashare_broad_asset_etf_rotation_strict_backtest as strict_runner
    from quant.scripts import run_ashare_broad_asset_etf_rotation_stability as stability_runner
    from quant.scripts import run_ashare_broad_asset_etf_rotation_walkforward as walkforward_runner

    calls = []

    def fake_walkforward(max_workers=4):
        calls.append(("walkforward", max_workers))
        return {"walkforward": {"total_splits": 1}}, Path("walkforward.html")

    def fake_stability(max_workers=4):
        calls.append(("stability", max_workers))
        return {"parameter_sensitivity": {"tested_count": 1}}, Path("stability.html")

    monkeypatch.setattr(walkforward_runner, "run_walkforward", fake_walkforward)
    monkeypatch.setattr(stability_runner, "run_stability", fake_stability)

    args = strict_runner._parse_args([])
    skipped = strict_runner._parse_args(["--skip-followups"])
    summary = strict_runner._run_default_followup_audits(walkforward_workers=2, stability_workers=3)

    assert args.run_followups is True
    assert skipped.run_followups is False
    assert calls == [("walkforward", 2), ("stability", 3)]
    assert summary["walkforward"]["payload"]["walkforward"]["total_splits"] == 1
    assert summary["stability"]["payload"]["parameter_sensitivity"]["tested_count"] == 1


def test_broad_asset_etf_rotation_walkforward_payload_matches_full_report_contract():
    from quant.features.research.models import PurgedWalkForwardResult
    from quant.scripts.run_ashare_broad_asset_etf_rotation_walkforward import _build_walkforward_payload

    result = PurgedWalkForwardResult(
        splits=[
            {
                "train_start_date": "2020-01-01",
                "train_end_date": "2020-12-31",
                "test_start_date": "2021-01-01",
                "test_end_date": "2021-03-31",
                "test_sharpe": 0.4,
                "trade_count": 6,
                "has_trades": True,
                "response": {"metrics": {"total_return": 0.025}},
            },
            {
                "train_start_date": "2020-04-01",
                "train_end_date": "2021-03-31",
                "test_start_date": "2021-04-01",
                "test_end_date": "2021-06-30",
                "test_sharpe": 0.0,
                "trade_count": 0,
                "has_trades": False,
            },
        ],
        aggregate_oos_sharpe=0.4,
        worst_oos_sharpe=0.4,
        deflated_sharpe_ratio=None,
        sharpe_degradation=0.0,
        pct_profitable_splits=1.0,
        is_viable=True,
        capacity_ok=True,
        evaluated_splits=1,
        no_trade_splits=1,
        total_splits=2,
    )

    payload = _build_walkforward_payload(result, max_workers=4)
    walkforward = payload["walkforward"]

    assert walkforward["verdict"] == "pass"
    assert walkforward["total_splits"] == 2
    assert walkforward["evaluated_splits"] == 1
    assert walkforward["no_trade_splits"] == 1
    assert walkforward["thresholds"]["test_window_days"] == 63
    assert walkforward["splits"][0]["oos_sharpe"] == pytest.approx(0.4)
    assert walkforward["splits"][0]["return"] == pytest.approx(0.025)


def test_broad_asset_etf_rotation_walkforward_split_replay_uses_train_window_warmup(monkeypatch):
    from quant.scripts import run_ashare_broad_asset_etf_rotation_walkforward as walkforward_runner

    captured = {}

    def fake_run_one(scenario, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit, start, end):
        captured["start"] = start.date().isoformat()
        captured["end"] = end.date().isoformat()
        return {
            "metrics": {"sharpe": 99.0, "cagr": 9.9, "max_drawdown_pct": 0.0, "total_trades": 99},
            "capacity": {"max_adv_participation": 0.01},
            "equity_curve": {
                "strategy": [
                    {"date": "2020-01-01", "value": 100.0},
                    {"date": "2020-12-31", "value": 100.0},
                    {"date": "2021-01-04", "value": 101.0},
                    {"date": "2021-01-05", "value": 102.0},
                    {"date": "2021-01-06", "value": 102.5},
                ]
            },
        }

    monkeypatch.setattr(walkforward_runner.strict_runner, "_run_one", fake_run_one)
    response = walkforward_runner._run_split_replay(
        {
            "name": "base",
            "symbols": ["510300"],
            "category_symbols": {"csi300": ["510300"]},
            "pit_size_fields": [],
        },
        {
            "start": "2021-01-04",
            "end": "2021-01-06",
            "train_start_date": "2020-01-01",
        },
        {"510300": 100},
        None,
        {},
        {},
    )

    assert captured == {"start": "2020-01-01", "end": "2021-01-06"}
    assert response["metrics"]["sharpe"] != 99.0
    assert response["metrics"]["total_return"] == pytest.approx(0.025)
    assert response["metrics"]["total_trades"] == 3
    assert response["returns"].index.min().date().isoformat() == "2021-01-04"


def test_broad_asset_full_report_attaches_persisted_stability_payload(tmp_path):
    from quant.scripts.run_ashare_broad_asset_etf_rotation_strict_backtest import _attach_followup_metrics

    payload = {
        "parameter_sensitivity": {
            "status": "pass",
            "tested_count": 4,
            "pass_count": 3,
            "max_degradation_pct": 12.5,
        }
    }
    (tmp_path / "stability_result.json").write_text(json.dumps(payload), encoding="utf-8")
    row = {"metrics": {}}

    _attach_followup_metrics(row, tmp_path)

    assert row["metrics"]["parameter_sensitivity"]["tested_count"] == 4


def test_broad_asset_full_report_attaches_persisted_walkforward_payload(tmp_path):
    from quant.scripts.run_ashare_broad_asset_etf_rotation_strict_backtest import _attach_followup_metrics

    payload = {
        "walkforward": {
            "verdict": "warn",
            "aggregate_oos_sharpe": 0.21,
            "worst_oos_sharpe": -0.10,
            "pct_profitable_splits": 0.55,
            "total_splits": 4,
            "evaluated_splits": 3,
            "no_trade_splits": 1,
        }
    }
    (tmp_path / "walkforward_result.json").write_text(json.dumps(payload), encoding="utf-8")
    row = {"metrics": {}}

    _attach_followup_metrics(row, tmp_path)

    assert row["metrics"]["walkforward"]["total_splits"] == 4
    assert row["metrics"]["research_stage_conclusions"]["walkforward_strict_audit"]["verdict"] == "warn"


def test_api_yearly_returns_from_equity_uses_calendar_years():
    from quant.api.research_bp import _yearly_returns_from_equity

    equity = pd.Series(
        [100.0, 110.0, 99.0],
        index=pd.to_datetime(["2012-01-03", "2012-12-31", "2013-12-31"]),
    )

    yearly = _yearly_returns_from_equity(equity, initial_cash=100.0)

    assert yearly["2012"] == pytest.approx(0.10)
    assert yearly["2013"] == pytest.approx(-0.10)


def test_api_make_strategy_scout_uses_infrastructure_sources():
    from quant.api import research_bp as research_module

    scout = research_module._make_strategy_scout(ResearchConfig(sources=["ssrn"], scout_config={"rank_results": True}))

    assert scout._source_hub is not None
    assert scout._hub_sources == ["ssrn"]
    assert scout._source_hub._sources["ssrn"].__class__.__name__ == "SSRNSource"


def test_api_load_research_config_reads_feature_yaml():
    from quant.api import research_bp as research_module

    cfg = research_module._load_research_config()

    assert cfg.sources == ["arxiv", "ssrn", "nber", "blog"]
    assert cfg.default_backtest_start == "2016-01-01"
    assert cfg.default_backtest_end == "2026-05-31"
    assert cfg.scout_config["query_plan"]["ssrn"][0]["query"] == "daily trading strategy equity factor"
    assert cfg.scout_config["required_match_terms"] == ["daily_ohlcv"]
    assert cfg.production_gate_config["max_drawdown_cagr_10_15"] == 0.25
    assert cfg.rigor_config["cost_model"]["max_adv_pct"] == 0.05


def test_api_load_research_config_does_not_hide_invalid_config(monkeypatch):
    from quant.api import research_bp as research_module

    monkeypatch.setattr(
        research_module,
        "_load_research_config_data",
        lambda loader_cls=None: {"research": {"unknown_field": True}},
    )

    with pytest.raises(TypeError):
        research_module._load_research_config()


def test_api_remote_llm_without_key_uses_heuristic(monkeypatch):
    from quant.api import research_bp as research_module

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    adapter = research_module._create_llm_adapter(ResearchConfig(llm_provider="deepseek", llm_api_key=None))

    assert adapter is None


def test_api_make_rigor_hub_uses_two_arg_walkforward_runner_and_experiment_store(monkeypatch):
    from quant.api import research_bp as research_module

    calls = []
    experiment_store = object()

    def walkforward_runner(strategy_id, request):
        calls.append((strategy_id, request))
        return {"metrics": {"sharpe": 0.0}}

    def legacy_runner_factory():
        raise AssertionError("legacy backtest runner should not wire RigorHub")

    monkeypatch.setattr(research_module, "_make_walkforward_runner", lambda: walkforward_runner)
    monkeypatch.setattr(research_module, "_make_backtest_fn", legacy_runner_factory)

    hub = research_module._make_rigor_hub(ResearchConfig(), experiment_store=experiment_store)
    response = hub._runner("test_strat", {"start": "2020-01-01", "end": "2020-02-01"})

    assert response["metrics"]["sharpe"] == 0.0
    assert calls == [("test_strat", {"start": "2020-01-01", "end": "2020-02-01"})]
    assert hub._experiment_store is experiment_store


def test_walkforward_candidate_info_prefers_stored_candidate(monkeypatch):
    from quant.api import research_bp as research_module

    stored = {
        "id": "stored_strategy",
        "parameters": {"target_exposure": 0.5},
        "research_meta": {"strategy_spec": {"universe": ["600001"]}},
    }

    monkeypatch.setattr(research_module, "_archived_candidate_info", lambda sid, archive_dir=None: None)
    monkeypatch.setattr(research_module, "_stored_candidate_info", lambda sid: stored)

    info = research_module._candidate_info_for_walkforward("stored_strategy", symbols=["600002"])

    assert info is stored
    assert info["parameters"]["target_exposure"] == 0.5


def test_walkforward_candidate_info_falls_back_to_symbols(monkeypatch):
    from quant.api import research_bp as research_module

    monkeypatch.setattr(research_module, "_archived_candidate_info", lambda sid, archive_dir=None: None)
    monkeypatch.setattr(research_module, "_stored_candidate_info", lambda sid: None)

    info = research_module._candidate_info_for_walkforward("missing_strategy", symbols=["600002"])

    assert info["research_meta"]["strategy_spec"]["universe"] == ["600002"]


def test_api_standalone_strict_backtest_recovers_persistent_candidate_metadata(monkeypatch):
    from quant.api import research_bp as research_module
    import quant.features.backtest.benchmark as benchmark_module
    import quant.features.backtest.engine as engine_module
    import quant.features.backtest.walkforward as walkforward_module
    import quant.features.strategies.registry as registry_module
    import quant.infrastructure.data.providers.duckdb_provider as duckdb_module

    tmp_path = _test_root()
    captured = {}

    class FakeStrategy:
        def __init__(self, symbols=None, max_position_pct=1.0, holding_days=1):
            self.symbols = list(symbols or [])
            self.max_position_pct = max_position_pct
            self.max_positions = 20
            self.holding_days = holding_days

    class FakeRegistry:
        def get(self, sid):
            return FakeStrategy

    class FakeStorage:
        def get_all_instrument_meta(self):
            return pd.DataFrame({"symbol": ["600001", "600002"], "lot_size": [100, 100]})

    class FakeDuckDBProvider:
        def __init__(self):
            self.storage = FakeStorage()

        def connect(self):
            return None

        def disconnect(self):
            return None

        def get_bars_for_symbols(self, symbols, start, end, timeframe):
            captured["symbols"] = list(symbols)
            return pd.DataFrame(
                {
                    "timestamp": pd.to_datetime(["2020-01-01", "2020-01-02"] * len(symbols)),
                    "symbol": [symbol for symbol in symbols for _ in range(2)],
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10.2,
                    "volume": 100000,
                }
            )

        def get_bars(self, symbol, start, end, timeframe):
            return pd.DataFrame(
                {
                    "timestamp": pd.to_datetime(["2020-01-01", "2020-01-02"]),
                    "close": [100.0, 101.0],
                    "adj_close": [100.0, 101.0],
                }
            )

    class FakeBenchmarkProvider:
        def __init__(self, frame, price_column="close"):
            self.frame = frame

        def get_benchmark_equity(self, start, end, initial_cash):
            return pd.Series([float(initial_cash), float(initial_cash) * 1.01], index=pd.to_datetime(["2020-01-01", "2020-01-02"]))

    class FakeBacktester:
        def __init__(self, *args, **kwargs):
            return None

        def run(self, start, end, strategies, initial_cash, data_provider, symbols):
            captured["strategy_symbols"] = list(strategies[0].symbols)
            captured["max_position_pct"] = strategies[0].max_position_pct
            captured["holding_days"] = strategies[0].holding_days
            captured["initial_cash"] = initial_cash
            return SimpleNamespace(
                final_nav=float(initial_cash) * 1.05,
                sharpe_ratio=0.8,
                sortino_ratio=1.0,
                total_return=0.05,
                max_drawdown_pct=-0.03,
                win_rate=0.6,
                profit_factor=1.2,
                trades=[],
                metrics=SimpleNamespace(total_trades=0, statistical_significance={"t_stat": 1.5, "p_value": 0.13}),
                diagnostics=None,
                equity_curve=pd.Series([float(initial_cash), float(initial_cash) * 1.05], index=pd.to_datetime(["2020-01-01", "2020-01-02"])),
            )

    monkeypatch.setattr(registry_module, "StrategyRegistry", FakeRegistry)
    monkeypatch.setattr(duckdb_module, "DuckDBProvider", FakeDuckDBProvider)
    monkeypatch.setattr(benchmark_module, "BenchmarkProvider", FakeBenchmarkProvider)
    monkeypatch.setattr(engine_module, "Backtester", FakeBacktester)
    monkeypatch.setattr(walkforward_module, "DataFrameProvider", lambda frame: frame)

    try:
        research_store = FileResearchStore(tmp_path / "research")
        research_store.upsert_candidate(
            {
                "id": "persisted_candidate",
                "name": "Persisted Candidate",
                "status": "candidate",
                "parameters": {"max_position_pct": 0.85, "holding_days": 7, "ignored_param": "ignored"},
                "research_meta": {"strategy_spec": {"universe": ["600001", "600002"]}},
            }
        )
        research_store.upsert_hypothesis(
            {
                "hypothesis_id": "h1",
                "strategy_id": "persisted_candidate",
                "title": "Persisted Candidate",
                "status": "candidate",
                "stage": "stage2_integrate",
                "source": "fixture",
                "source_url": "https://example.test",
                "thesis": "fixture",
                "decision_reason": "",
                "metrics": {},
                "evidence": {"strategy_spec": {"strategy_id": "persisted_candidate"}},
            }
        )
        pool = CandidatePool(research_store=research_store)
        backtest_fn = research_module._make_backtest_fn()
        integrator = SimpleNamespace(get_registry_entry=lambda sid: None)
        result = ResearchResult()

        backtest_fn("persisted_candidate", result, ResearchConfig(default_backtest_start="2020-01-01", default_backtest_end="2020-01-02"), integrator, pool)

        hypothesis = research_store.list_hypotheses()[0]
        assert captured["symbols"] == ["600001", "600002"]
        assert captured["strategy_symbols"] == ["600001", "600002"]
        assert captured["max_position_pct"] == pytest.approx(0.85)
        assert captured["holding_days"] == 7
        assert captured["initial_cash"] == pytest.approx(10_000)
        assert hypothesis["metrics"]["strict_backtest"]["metrics"]["sharpe"] == pytest.approx(0.8)
        assert result.backtested == 1
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_api_make_validation_components_wires_market_and_factor_ports(monkeypatch):
    from quant.api import research_bp as research_module

    market_data = object()
    factor_data = object()

    monkeypatch.setattr(research_module, "_make_research_market_data", lambda cfg: market_data)
    monkeypatch.setattr(research_module, "_make_factor_data", lambda cfg: factor_data)

    spec_builder, validator = research_module._make_validation_components(
        ResearchConfig(
            validation_enabled=True,
            validation_min_obs=123,
            validation_config={"min_stocks": 17, "factor_validation_enabled": True},
        )
    )

    assert spec_builder.__class__.__name__ == "StrategySpecBuilder"
    assert validator.__class__.__name__ == "FactorValidator"
    assert validator._market_data is market_data
    assert validator._factor_data is factor_data
    assert validator._config["min_observations"] == 123
    assert validator._config["min_stocks"] == 17
    assert validator._config["factor_validation_enabled"] is True
    assert validator._config["start_date"] == "2016-01-01"
    assert validator._config["end_date"] == "2026-05-31"


def test_walkforward_runner_reuses_prefetched_data(monkeypatch):
    from quant.api import research_bp as research_module
    import quant.features.backtest.engine as engine_module
    import quant.features.strategies.registry as registry_module
    import quant.infrastructure.data.providers.duckdb_provider as duckdb_module

    fetches = []
    seen_windows = []

    class FakeStorage:
        def get_lot_size(self, symbol):
            return 100

    class FakeDuckDBProvider:
        def __init__(self):
            self.storage = FakeStorage()

        def connect(self):
            return None

        def disconnect(self):
            return None

        def get_bars_for_symbols(self, symbols, start, end, timeframe):
            fetches.append((tuple(symbols), start, end, timeframe))
            dates = pd.date_range(start, end, freq="D")
            rows = []
            for date in dates:
                rows.append({
                    "timestamp": date,
                    "symbol": symbols[0],
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10.0,
                    "volume": 100000,
                })
            return pd.DataFrame(rows)

    class FakeStrategy:
        def __init__(self, symbols):
            self.symbols = symbols

    class FakeRegistry:
        def get(self, sid):
            return FakeStrategy

    class FakeBacktester:
        def __init__(self, *args, **kwargs):
            return None

        def run(self, start, end, strategies, initial_cash, data_provider, symbols):
            seen_windows.append((start, end, data_provider.data["timestamp"].min(), data_provider.data["timestamp"].max()))
            return SimpleNamespace(
                equity_curve=pd.Series([initial_cash, initial_cash * 1.01], index=pd.to_datetime([start, end])),
                trades=[],
                sharpe_ratio=1.0,
                max_drawdown_pct=-0.01,
                total_return=0.01,
                win_rate=0.5,
            )

    monkeypatch.setattr(duckdb_module, "DuckDBProvider", FakeDuckDBProvider)
    monkeypatch.setattr(registry_module, "StrategyRegistry", FakeRegistry)
    monkeypatch.setattr(engine_module, "Backtester", FakeBacktester)

    runner = research_module._make_walkforward_runner()
    base_request = {
        "symbols": ["000001"],
        "initial_cash": 100000,
        "walkforward_start_date": "2020-01-01",
        "walkforward_end_date": "2020-01-10",
        "walkforward_prefetch_data": True,
    }

    first = runner("test_strategy", {**base_request, "start": "2020-01-03", "end": "2020-01-05"})
    second = runner("test_strategy", {**base_request, "start": "2020-01-06", "end": "2020-01-08"})

    assert len(fetches) == 1
    assert fetches[0][1].strftime("%Y-%m-%d") == "2020-01-01"
    assert fetches[0][2].strftime("%Y-%m-%d") == "2020-01-10"
    assert seen_windows[0][2] == pd.Timestamp("2020-01-03")
    assert seen_windows[0][3] == pd.Timestamp("2020-01-05")
    assert seen_windows[1][2] == pd.Timestamp("2020-01-06")
    assert seen_windows[1][3] == pd.Timestamp("2020-01-08")
    assert first["metrics"]["sharpe"] == 1.0
    assert second["metrics"]["sharpe"] == 1.0


def test_walkforward_runner_loads_archived_rejected_strategy(monkeypatch):
    from quant.api import research_bp as research_module
    import quant.features.backtest.engine as engine_module
    import quant.features.strategies.registry as registry_module
    import quant.infrastructure.data.providers.duckdb_provider as duckdb_module

    tmp_path = _test_root()
    captured = {}

    class FakeRegistry:
        def get(self, name):
            return None

    class FakeStorage:
        def get_all_instrument_meta(self):
            return pd.DataFrame({"symbol": ["600001"], "lot_size": [100]})

    class FakeDuckDBProvider:
        def __init__(self):
            self.storage = FakeStorage()

        def connect(self):
            return None

        def disconnect(self):
            return None

        def get_bars_for_symbols(self, symbols, start, end, timeframe):
            return pd.DataFrame(
                {
                    "timestamp": pd.date_range(start, end, freq="D"),
                    "symbol": "600001",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10.0,
                    "volume": 100000,
                }
            )

    class FakeBacktester:
        def __init__(self, *args, **kwargs):
            return None

        def run(self, start, end, strategies, initial_cash, data_provider, symbols):
            captured["strategy_class"] = strategies[0].__class__.__name__
            captured["strategy_symbols"] = list(strategies[0].symbols)
            return SimpleNamespace(
                equity_curve=pd.Series([initial_cash, initial_cash * 1.02], index=pd.to_datetime([start, end])),
                trades=[],
                sharpe_ratio=1.1,
                max_drawdown_pct=-0.02,
                total_return=0.02,
                win_rate=0.6,
            )

    try:
        archive_dir = tmp_path / "rejected_strategy" / "archived_strategy"
        archive_dir.mkdir(parents=True)
        (archive_dir / "strategy.py").write_text(
            "\n".join(
                [
                    "from quant.features.strategies.registry import strategy",
                    "",
                    "@strategy('archived_strategy')",
                    "class ArchivedStrategy:",
                    "    def __init__(self, symbols=None):",
                    "        self.symbols = list(symbols or [])",
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(registry_module, "StrategyRegistry", FakeRegistry)
        monkeypatch.setattr(duckdb_module, "DuckDBProvider", FakeDuckDBProvider)
        monkeypatch.setattr(engine_module, "Backtester", FakeBacktester)

        runner = research_module._make_walkforward_runner()
        response = runner(
            "archived_strategy",
            {
                "symbols": ["600001"],
                "start": "2020-01-01",
                "end": "2020-01-03",
                "initial_cash": 100000,
                "strategy_archive_dir": str(archive_dir),
            },
        )

        assert captured["strategy_class"] == "ArchivedStrategy"
        assert captured["strategy_symbols"] == ["600001"]
        assert response["metrics"]["sharpe"] == pytest.approx(1.1)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_walkforward_runner_uses_archived_parameters_and_execution_cost_model(monkeypatch):
    from quant.api import research_bp as research_module
    import quant.features.backtest.engine as engine_module
    import quant.features.strategies.registry as registry_module
    import quant.infrastructure.data.providers.duckdb_provider as duckdb_module

    tmp_path = _test_root()
    captured = {}

    class FakeRegistry:
        def get(self, name):
            return None

    class FakeStorage:
        def get_all_instrument_meta(self):
            return pd.DataFrame({"symbol": ["600001"], "lot_size": [100]})

    class FakeDuckDBProvider:
        def __init__(self):
            self.storage = FakeStorage()

        def connect(self):
            return None

        def disconnect(self):
            return None

        def get_bars_for_symbols(self, symbols, start, end, timeframe):
            return pd.DataFrame(
                {
                    "timestamp": pd.date_range(start, end, freq="D"),
                    "symbol": "600001",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10.0,
                    "volume": 100000,
                    "turnover": 1000000.0,
                }
            )

    class FakeBacktester:
        def __init__(self, config, *args, **kwargs):
            captured["config"] = config

        def run(self, start, end, strategies, initial_cash, data_provider, symbols):
            captured["max_position_pct"] = strategies[0].max_position_pct
            captured["holding_days"] = strategies[0].holding_days
            captured["initial_cash"] = initial_cash
            captured["data_columns"] = list(data_provider.data.columns)
            return SimpleNamespace(
                equity_curve=pd.Series([initial_cash, initial_cash * 1.02], index=pd.to_datetime([start, end])),
                trades=[],
                sharpe_ratio=1.1,
                max_drawdown_pct=-0.02,
                total_return=0.02,
                win_rate=0.6,
            )

    try:
        archive_dir = tmp_path / "rejected_strategy" / "joinquant_small_cap_low_price"
        archive_dir.mkdir(parents=True)
        (archive_dir / "config.yaml").write_text(
            "\n".join(
                [
                    "strategy:",
                    "  name: joinquant_small_cap_low_price",
                    "parameters:",
                    "  max_position_pct: 0.7",
                    "  holding_days: 5",
                ]
            ),
            encoding="utf-8",
        )
        (archive_dir / "strategy.py").write_text(
            "\n".join(
                [
                    "from quant.features.strategies.registry import strategy",
                    "",
                    "@strategy('joinquant_small_cap_low_price')",
                    "class ArchivedStrategy:",
                    "    def __init__(self, symbols=None, max_position_pct=1.0, holding_days=1):",
                    "        self.symbols = list(symbols or [])",
                    "        self.max_position_pct = max_position_pct",
                    "        self.holding_days = holding_days",
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(registry_module, "StrategyRegistry", FakeRegistry)
        monkeypatch.setattr(duckdb_module, "DuckDBProvider", FakeDuckDBProvider)
        monkeypatch.setattr(engine_module, "Backtester", FakeBacktester)

        runner = research_module._make_walkforward_runner()
        runner(
            "joinquant_small_cap_low_price",
            {
                "symbols": ["600001"],
                "start": "2020-01-01",
                "end": "2020-01-03",
                "strategy_archive_dir": str(archive_dir),
            },
        )

        model = captured["config"]["backtest"]["execution_cost_model"]
        assert model["name"] == "small_cap_realistic"
        assert captured["config"]["risk"]["max_position_pct"] == pytest.approx(0.20)
        assert captured["max_position_pct"] == pytest.approx(0.7)
        assert captured["holding_days"] == 5
        assert captured["initial_cash"] == pytest.approx(10_000)
        assert "adv20_value" in captured["data_columns"]
        assert "volatility20" in captured["data_columns"]
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_api_load_lot_sizes_uses_bulk_instrument_meta():
    from quant.api.research_bp import _load_lot_sizes

    class FakeStorage:
        def __init__(self):
            self.single_calls = 0

        def get_lot_size(self, symbol):
            self.single_calls += 1
            return 100

        def get_all_instrument_meta(self):
            return pd.DataFrame([
                {"symbol": "000001", "lot_size": 100},
                {"symbol": "600519", "lot_size": 200},
                {"symbol": "AAPL", "lot_size": 1},
            ])

    storage = FakeStorage()
    db_provider = SimpleNamespace(storage=storage)
    lot_sizes = _load_lot_sizes(db_provider, ["000001", "600519", "000002", "AAPL"], lambda sym: sym.isdigit())

    assert lot_sizes == {"000001": 100, "600519": 200, "000002": 100, "AAPL": 1}
    assert storage.single_calls == 0


def test_api_make_validation_components_respects_disabled_flag():
    from quant.api import research_bp as research_module

    assert research_module._make_validation_components(ResearchConfig(validation_enabled=False)) == (None, None)


def test_api_candidate_symbols_prefer_strategy_spec_universe():
    from quant.api import research_bp as research_module

    info = {"research_meta": {"strategy_spec": {"universe": ["600519", "000001"]}}}

    assert research_module._candidate_symbols(info, ["000300"]) == ["600519", "000001"]
    assert research_module._candidate_symbols({"research_meta": {"strategy_spec": {"universe": ["AAPL"]}}}, ["SPY"]) == DEFAULT_A_SHARE_SYMBOLS
    assert research_module._candidate_symbols({}, ["000300"]) == ["000300"]


def test_api_latest_report_payload_points_to_stage_reports(tmp_path):
    from quant.api import research_bp as research_module

    report_dir = tmp_path / "research"
    (report_dir / "reports" / "latest").mkdir(parents=True)
    full_path = report_dir / "reports" / "latest" / "full_research_report.html"
    fast_path = report_dir / "reports" / "latest" / "fast_research_report.html"
    full_path.write_text("<html></html>", encoding="utf-8")
    fast_path.write_text("<html></html>", encoding="utf-8")

    payload = research_module._latest_report_payload(ResearchConfig(research_dir=str(report_dir)))

    assert payload["available"] is True
    assert payload["reports_root"] == str(report_dir / "reports")
    assert payload["full_report"]["available"] is True
    assert payload["full_report"]["path"] == str(full_path)
    assert payload["full_report"]["url"] == "/api/research/report/latest"
    assert payload["stage_reports"]["fast_research"]["available"] is True
    assert payload["stage_reports"]["fast_research"]["path"] == str(fast_path)
    assert payload["stage_reports"]["fast_research"]["url"] == "/api/research/report/stage/fast_research"
    assert payload["stage_reports"]["strict_backtest"]["available"] is False
    assert "updated_at" in payload


def test_api_latest_report_payload_ignores_legacy_full_report(tmp_path):
    from quant.api import research_bp as research_module

    report_dir = tmp_path / "research"
    report_dir.mkdir()
    report_path = report_dir / "full_research_report.html"
    report_path.write_text("<html><body>legacy</body></html>", encoding="utf-8")

    payload = research_module._latest_report_payload(ResearchConfig(research_dir=str(report_dir)))

    assert payload["available"] is False
    assert payload["full_report"]["available"] is False


def test_api_latest_report_endpoint_serves_full_report(tmp_path, monkeypatch):
    from flask import Flask
    from quant.api import research_bp as research_module

    report_dir = tmp_path / "research"
    latest_dir = report_dir / "reports" / "latest"
    latest_dir.mkdir(parents=True)
    (latest_dir / "full_research_report.html").write_text("<html><body>full</body></html>", encoding="utf-8")
    monkeypatch.setattr(research_module, "_load_research_config", lambda: ResearchConfig(research_dir=str(report_dir)))

    app = Flask(__name__)
    app.register_blueprint(research_module.research_bp)
    response = app.test_client().get("/api/research/report/latest")

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert b"full" in response.data


def test_api_parse_research_idea_statuses():
    from quant.api import research_bp as research_module

    assert research_module._parse_statuses("discovered,research_queue") == ["discovered", "research_queue"]
    assert research_module._parse_statuses(["candidate", " rejected "]) == ["candidate", "rejected"]
    assert research_module._parse_statuses(None) is None
    assert research_module._parse_idea_ids("abc,def") == ["abc", "def"]


def test_api_scheduler_injects_validation_components(monkeypatch):
    from quant.api import research_bp as research_module

    captured = {}

    class FakeEngine:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(research_module, "_research_scheduler", None)
    monkeypatch.setattr(research_module, "_load_research_config", lambda: ResearchConfig(auto_run=False))
    monkeypatch.setattr(research_module, "_make_research_store", lambda cfg: object())
    monkeypatch.setattr(research_module, "_create_llm_adapter", lambda cfg: None)
    monkeypatch.setattr(research_module, "_make_strategy_scout", lambda cfg: object())
    monkeypatch.setattr(research_module, "_make_backtest_fn", lambda: object())
    monkeypatch.setattr(research_module, "_make_rigor_hub", lambda cfg, experiment_store=None: None)
    monkeypatch.setattr(research_module, "_make_benchmark_data_loader", lambda cfg: None)
    monkeypatch.setattr(research_module, "_make_experiment_stores", lambda cfg: (None, None))
    monkeypatch.setattr(research_module, "_make_validation_components", lambda cfg: ("spec", "validator"))
    monkeypatch.setattr(research_module, "ResearchEngine", FakeEngine)

    scheduler = research_module._get_scheduler()

    assert scheduler.engine is not None
    assert captured["spec_builder"] == "spec"
    assert captured["validator"] == "validator"


def test_cli_make_validation_components_wires_market_and_factor_ports(monkeypatch):
    from quant.scripts import run_research as cli

    market_data = object()
    factor_data = object()

    monkeypatch.setattr(cli, "_create_research_market_data", lambda cfg: market_data)
    monkeypatch.setattr(cli, "_create_factor_data", lambda cfg: factor_data)

    spec_builder, validator = cli._create_validation_components(
        ResearchConfig(
            validation_enabled=True,
            validation_min_obs=88,
            validation_config={"sensitivity_enabled": True},
        )
    )

    assert spec_builder.__class__.__name__ == "StrategySpecBuilder"
    assert validator.__class__.__name__ == "FactorValidator"
    assert validator._market_data is market_data
    assert validator._factor_data is factor_data
    assert validator._config["min_observations"] == 88
    assert validator._config["sensitivity_enabled"] is True
    assert validator._config["start_date"] == "2016-01-01"
    assert validator._config["end_date"] == "2026-05-31"


def test_cli_load_research_config_reads_feature_yaml_and_syncs_thresholds():
    from quant.scripts import run_research as cli

    cfg = cli._load_research_config()

    assert cfg.default_backtest_start == "2016-01-01"
    assert cfg.default_backtest_end == "2026-05-31"
    assert cfg.production_gate_config["max_drawdown_cagr_15_20"] == 0.30
    assert cfg.rigor_config["cost_model"]["max_adv_pct"] == 0.05


def test_cli_full_and_formal_modes_enable_full_report_execution_defaults():
    from quant.scripts import run_research as cli

    for mode in ("full", "formal"):
        cfg = ResearchConfig(auto_backtest=False, rigor_enabled=False)
        cli._apply_mode_defaults(cfg, mode)

        assert cfg.auto_backtest is True
        assert cfg.rigor_enabled is True
        assert cli._mode_requires_backtest_runner(mode) is True

    fast_cfg = ResearchConfig(auto_backtest=True, rigor_enabled=True)
    cli._apply_mode_defaults(fast_cfg, "fast")
    assert fast_cfg.auto_backtest is False
    assert fast_cfg.rigor_enabled is False
    assert cli._mode_requires_backtest_runner("fast") is False

    strict_cfg = ResearchConfig(auto_backtest=False, rigor_enabled=True)
    cli._apply_mode_defaults(strict_cfg, "strict")
    assert strict_cfg.auto_backtest is True
    assert strict_cfg.rigor_enabled is False
    assert cli._mode_requires_backtest_runner("strict") is True

    walkforward_cfg = ResearchConfig(auto_backtest=True, rigor_enabled=False)
    cli._apply_mode_defaults(walkforward_cfg, "walkforward")
    assert walkforward_cfg.auto_backtest is False
    assert walkforward_cfg.rigor_enabled is True
    assert cli._mode_requires_backtest_runner("walkforward") is True


def test_api_full_and_formal_modes_enable_full_report_execution_defaults():
    from quant.api import research_bp as research_module

    for mode in ("full", "formal", "from_bank", "formal_research"):
        cfg = ResearchConfig(auto_backtest=False, rigor_enabled=False)
        research_module._apply_research_mode_defaults(cfg, mode)

        assert cfg.auto_backtest is True
        assert cfg.rigor_enabled is True

    fast_cfg = ResearchConfig(auto_backtest=True, rigor_enabled=True)
    research_module._apply_research_mode_defaults(fast_cfg, "fast")
    assert fast_cfg.auto_backtest is False
    assert fast_cfg.rigor_enabled is False
