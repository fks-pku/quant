from quant.features.research.discovery.quality import attach_discovery_quality
from quant.features.research.discovery.worldquant101 import (
    WorldQuant101Source,
    build_worldquant101_raw_strategies,
    worldquant101_alpha_spec,
)
from quant.infrastructure.research.repository import FileResearchStore


def test_worldquant101_catalog_covers_all_alphas():
    rows = build_worldquant101_raw_strategies()

    assert len(rows) == 101
    assert rows[0].title == "WorldQuant 101 Alpha #001"
    assert rows[-1].metadata["alpha_number"] == 101


def test_worldquant101_marks_daily_cn_field_readiness():
    ready = worldquant101_alpha_spec(1)
    vwap_blocked = worldquant101_alpha_spec(5)
    industry_blocked = worldquant101_alpha_spec(48)

    assert ready["required_local_fields"] == ("close",)
    assert ready["a_share_ready"] is True
    assert vwap_blocked["missing_daily_cn_fields"] == ("vwap",)
    assert industry_blocked["missing_daily_cn_fields"] == ("indclass",)


def test_worldquant101_source_filters_ready_alphas():
    rows = WorldQuant101Source().search(query={"ready_only": True}, max_results=200)

    assert rows
    assert all(row["metadata"]["a_share_ready"] for row in rows)
    assert all(not row["metadata"]["missing_daily_cn_fields"] for row in rows)
    assert len(rows) < 101


def test_worldquant101_ideas_can_be_written_to_local_idea_bank(tmp_path):
    store = FileResearchStore(str(tmp_path / "research"))
    raw = attach_discovery_quality(build_worldquant101_raw_strategies(alpha_numbers=[1])[0])

    store.upsert_idea(raw, status="factor_library", reason="seed")
    rows = store.list_ideas(status="factor_library")

    assert len(rows) == 1
    assert rows[0]["metadata"]["external_library"] == "worldquant_101_formulaic_alphas"
    assert (tmp_path / "research" / "idea_bank" / "idea_bank.json").exists()

