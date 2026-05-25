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
    }


def test_production_gate_passes_professional_daily_strategy():
    gate = evaluate_production_readiness(_base_metrics())

    assert gate["verdict"] == "pass"
    assert gate["status"] == "paper_trading_candidate"
    assert gate["failures"] == []
    assert gate["thresholds"]["max_drawdown_cagr_10_15"] == pytest.approx(0.25)
    assert gate["metrics"]["strict_cagr"] == pytest.approx(0.13)
    assert gate["metrics"]["max_adv_participation"] == pytest.approx(0.021)


def test_production_gate_rejects_when_core_online_standards_fail():
    metrics = _base_metrics()
    metrics["strict_backtest"]["metrics"].update(
        {
            "sharpe": 0.61,
            "cagr": 0.02,
            "max_drawdown_pct": -0.34,
            "profit_factor": 1.08,
            "total_trades": 50,
        }
    )
    metrics["strict_backtest"]["capacity"]["max_adv_participation"] = 0.061

    gate = evaluate_production_readiness(metrics)

    assert gate["verdict"] == "fail"
    assert gate["status"] == "rejected"
    assert "strict_cagr=2.00% < 5.00%" in gate["failures"]
    assert "total_trades=50 <= 50" in gate["failures"]
    assert "max_adv_participation=6.10% > 5.00%" in gate["failures"]


def test_production_gate_rejects_drawdown_by_cagr_tier():
    metrics = _base_metrics()
    metrics["strict_backtest"]["metrics"]["max_drawdown_pct"] = -0.26

    gate = evaluate_production_readiness(metrics)

    assert gate["verdict"] == "fail"
    assert gate["status"] == "rejected"
    assert gate["failures"] == [
        "strict_max_drawdown=26.00% > 25.00% for strict_cagr=13.00%"
    ]


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
    assert gate["thresholds"]["max_adv_participation"] == pytest.approx(0.05)


def test_final_decision_downgrades_candidate_when_production_gate_fails(tmp_path):
    metrics = _base_metrics()
    metrics["strict_backtest"]["metrics"]["max_drawdown_pct"] = -0.28
    metrics["strict_backtest"]["capacity"]["max_adv_participation"] = 0.06
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
        "strict_max_drawdown=28.00% > 25.00% for strict_cagr=13.00%",
        "max_adv_participation=6.00% > 5.00%",
    ]
