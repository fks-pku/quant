import pytest
from unittest.mock import patch
from quant.features.research.pool import CandidatePool


def test_list_candidates_filters_status():
    with patch("quant.features.research.pool.AVAILABLE_STRATEGIES", {
        "S1": {"id": "s1", "status": "candidate", "name": "Cand1"},
        "S2": {"id": "s2", "status": "active", "name": "Active1"},
        "S3": {"id": "s3", "status": "candidate", "name": "Cand2"},
    }):
        pool = CandidatePool()
        cands = pool.list_candidates()
        assert len(cands) == 2
        assert all(c["status"] == "candidate" for c in cands)


def test_promote_candidate():
    strategies = {
        "S1": {"id": "s1", "status": "candidate", "name": "Cand1"},
    }
    with patch("quant.features.research.pool.AVAILABLE_STRATEGIES", strategies):
        with patch("quant.features.research.pool._save_strategy_state") as mock_save:
            pool = CandidatePool()
            pool.promote("s1")
            assert strategies["S1"]["status"] == "paused"
            mock_save.assert_called_once()


def test_reject_candidate():
    strategies = {
        "S1": {"id": "s1", "status": "candidate", "name": "Cand1"},
    }
    with patch("quant.features.research.pool.AVAILABLE_STRATEGIES", strategies):
        with patch("quant.features.research.pool._save_strategy_state") as mock_save:
            pool = CandidatePool()
            pool.reject("s1", reason="low sharpe")
            assert strategies["S1"]["status"] == "rejected"
            assert strategies["S1"]["research_meta"]["rejection_reason"] == "low sharpe"
            mock_save.assert_called_once()
