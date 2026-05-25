import pytest

from quant.features.research.production_gate import evaluate_production_readiness
from quant.features.research.models import ResearchConfig
from quant.features.research.research_engine import ResearchEngine
from quant.infrastructure.research.repository import FileResearchStore


def _base_metrics():
    return {
        "rank_ic": 0.032,
        "rank_ic_ir": 0.45,
        "rank_ic_tstat": 2.6,
        "hit_rate": 0.58,
        "portfolio_diagnostics": {
            "top_bucket_after_cost_sharpe": 1.05,
            "top_bucket_after_cost_annualized_return": 0.14,
            "top_bucket_after_cost_max_drawdown": -0.16,
            "top_bucket_after_cost_calmar_ratio": 0.88,
        },
        "strict_backtest": {
            "metrics": {
                "sharpe": 1.12,
                "cagr": 0.13,
                "max_drawdown_pct": -0.18,
                "calmar_ratio": 0.72,
                "profit_factor": 1.35,
                "total_trades": 86,
            },
            "capacity": {
                "max_adv_participation": 0.021,
                "p95_adv_participation": 0.012,
            },
            "diagnostics": {
                "cost_drag_pct": 0.08,
            },
        },
        "walkforward": {
            "aggregate_oos_sharpe": 0.92,
            "worst_oos_sharpe": 0.34,
            "pct_profitable_splits": 0.62,
            "deflated_sharpe_ratio": 0.96,
            "capacity_ok": True,
            "bull_only_warning": False,
        },
        "ensemble": {
            "mean_correlation": 0.28,
        },
    }


def test_production_gate_passes_professional_daily_strategy():
    gate = evaluate_production_readiness(_base_metrics())

    assert gate["verdict"] == "pass"
    assert gate["status"] == "paper_trading_candidate"
    assert gate["failures"] == []
    assert gate["thresholds"]["min_strict_sharpe"] == pytest.approx(0.8)
    assert gate["metrics"]["strict_sharpe"] == pytest.approx(1.12)


def test_production_gate_rejects_when_core_online_standards_fail():
    metrics = _base_metrics()
    metrics["strict_backtest"]["metrics"].update(
        {
            "sharpe": 0.61,
            "cagr": 0.02,
            "max_drawdown_pct": -0.34,
            "profit_factor": 1.08,
            "total_trades": 24,
        }
    )
    metrics["walkforward"].update(
        {
            "aggregate_oos_sharpe": 0.44,
            "worst_oos_sharpe": -0.12,
            "pct_profitable_splits": 0.48,
            "capacity_ok": False,
        }
    )
    metrics["ensemble"]["mean_correlation"] = 0.68

    gate = evaluate_production_readiness(metrics)

    assert gate["verdict"] == "fail"
    assert gate["status"] == "rejected"
    assert "strict_sharpe=0.61 < 0.80" in gate["failures"]
    assert "strict_cagr=2.00% < 5.00%" in gate["failures"]
    assert "strict_max_drawdown=34.00% > 25.00%" in gate["failures"]
    assert "total_trades=24 < 50" in gate["failures"]
    assert "aggregate_oos_sharpe=0.44 < 0.80" in gate["failures"]
    assert "capacity_ok=False" in gate["failures"]
    assert "mean_correlation=0.68 > 0.50" in gate["failures"]


def test_production_gate_warns_when_preferred_correlation_is_not_low():
    metrics = _base_metrics()
    metrics["ensemble"]["mean_correlation"] = 0.38

    gate = evaluate_production_readiness(metrics)

    assert gate["verdict"] == "warn"
    assert gate["status"] == "needs_more_validation"
    assert gate["failures"] == []
    assert gate["warnings"] == ["mean_correlation=0.38 > preferred 0.30"]


def test_final_decision_attaches_production_gate_verdict(tmp_path):
    research_store = FileResearchStore(tmp_path / "research")
    research_store.upsert_hypothesis(
        {
            "hypothesis_id": "h1",
            "strategy_id": "daily_gate_pass",
            "title": "Daily Gate Pass",
            "status": "candidate",
            "stage": "go_no_go",
            "source": "fixture",
            "source_url": "https://example.test",
            "thesis": "fixture",
            "decision_reason": "",
            "metrics": _base_metrics(),
            "evidence": {"strategy_spec": {"strategy_id": "daily_gate_pass"}},
        }
    )
    engine = ResearchEngine(config=ResearchConfig(), research_store=research_store)

    engine._attach_final_research_conclusion("daily_gate_pass", "candidate", "")

    hypothesis = research_store.list_hypotheses()[0]
    final = hypothesis["metrics"]["research_stage_conclusions"]["final_decision"]
    gate = final["scores"]["production_gate"]
    assert final["verdict"] == "pass"
    assert gate["verdict"] == "pass"
    assert gate["thresholds"]["min_aggregate_oos_sharpe"] == pytest.approx(0.8)


def test_final_decision_downgrades_candidate_when_production_gate_fails(tmp_path):
    metrics = _base_metrics()
    metrics["strict_backtest"]["metrics"]["sharpe"] = 0.62
    metrics["walkforward"]["aggregate_oos_sharpe"] = 0.49
    research_store = FileResearchStore(tmp_path / "research")
    research_store.upsert_hypothesis(
        {
            "hypothesis_id": "h1",
            "strategy_id": "daily_gate_fail",
            "title": "Daily Gate Fail",
            "status": "candidate",
            "stage": "go_no_go",
            "source": "fixture",
            "source_url": "https://example.test",
            "thesis": "fixture",
            "decision_reason": "",
            "metrics": metrics,
            "evidence": {"strategy_spec": {"strategy_id": "daily_gate_fail"}},
        }
    )
    engine = ResearchEngine(config=ResearchConfig(), research_store=research_store)

    engine._attach_final_research_conclusion("daily_gate_fail", "candidate", "")

    hypothesis = research_store.list_hypotheses()[0]
    final = hypothesis["metrics"]["research_stage_conclusions"]["final_decision"]
    assert final["verdict"] == "fail"
    assert "Production gate failed" in final["conclusion"]
    assert final["scores"]["production_gate"]["failures"] == [
        "strict_sharpe=0.62 < 0.80",
        "aggregate_oos_sharpe=0.49 < 0.80",
    ]
