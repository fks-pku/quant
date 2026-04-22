import pytest
from quant.api_server import app


def test_research_candidates_empty():
    with app.test_client() as client:
        resp = client.get("/api/research/candidates")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "candidates" in data
