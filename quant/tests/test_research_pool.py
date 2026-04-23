import pytest
from quant.features.research.pool import CandidatePool


def test_list_candidates_filters_status():
    registry = {
        "S1": {"id": "s1", "status": "candidate", "name": "Cand1"},
        "S2": {"id": "s2", "status": "active", "name": "Active1"},
        "S3": {"id": "s3", "status": "candidate", "name": "Cand2"},
    }
    pool = CandidatePool(strategy_registry=registry)
    cands = pool.list_candidates()
    assert len(cands) == 2
    assert all(c["status"] == "candidate" for c in cands)


def test_promote_candidate():
    strategies = {
        "S1": {"id": "s1", "status": "candidate", "name": "Cand1"},
    }
    saved = []
    pool = CandidatePool(strategy_registry=strategies, on_state_change=lambda: saved.append(True))
    pool.promote("s1")
    assert strategies["S1"]["status"] == "paused"
    assert len(saved) == 1


def test_reject_candidate():
    strategies = {
        "S1": {"id": "s1", "status": "candidate", "name": "Cand1"},
    }
    saved = []
    pool = CandidatePool(strategy_registry=strategies, on_state_change=lambda: saved.append(True))
    pool.reject("s1", reason="low sharpe")
    assert strategies["S1"]["status"] == "rejected"
    assert strategies["S1"]["research_meta"]["rejection_reason"] == "low sharpe"
    assert len(saved) == 1
