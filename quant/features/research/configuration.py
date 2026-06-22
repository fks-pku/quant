from __future__ import annotations

from typing import Any, Dict

from quant.domain.models.research_source_catalog import (
    default_research_query_plan,
    default_research_source_names,
    default_research_source_quality,
)


def research_config_kwargs_from_data(data: Dict[str, Any]) -> Dict[str, Any]:
    source = dict(data or {})
    research_cfg = dict(source.get("research", {}) or {})
    validation_cfg = dict(source.get("validation", {}) or {})
    evaluation_cfg = dict(source.get("evaluation", {}) or {})
    pit_cfg = dict(source.get("pit", {}) or {})
    llm_cfg = dict(source.get("llm", {}) or {})

    merged_validation = dict(research_cfg.get("validation_config", {}) or {})
    merged_validation.update(validation_cfg)
    research_cfg["validation_config"] = merged_validation

    merged_evaluation = dict(research_cfg.get("evaluation_config", {}) or {})
    merged_evaluation.update(evaluation_cfg)
    research_cfg["evaluation_config"] = merged_evaluation

    if "enabled" in pit_cfg:
        research_cfg["pit_enabled"] = bool(pit_cfg.get("enabled"))
    if "universe_snapshot_dir" in pit_cfg:
        research_cfg["pit_universe_snapshot_dir"] = pit_cfg.get("universe_snapshot_dir")

    research_cfg.setdefault("llm_provider", llm_cfg.get("provider", "minimax"))
    research_cfg.setdefault("llm_model", llm_cfg.get("model", "MiniMax-M2.7"))
    research_cfg.setdefault("llm_api_key", llm_cfg.get("api_key"))
    research_cfg.setdefault("llm_temperature", llm_cfg.get("temperature", 0.3))
    research_cfg.setdefault("llm_base_url", llm_cfg.get("base_url"))
    research_cfg.setdefault("llm_group_id", llm_cfg.get("group_id"))
    research_cfg.setdefault("sources", default_research_source_names())

    scout_cfg = dict(research_cfg.get("scout_config", {}) or {})
    source_quality = default_research_source_quality()
    source_quality.update(dict(scout_cfg.get("source_quality", {}) or {}))
    scout_cfg["source_quality"] = source_quality
    query_plan = default_research_query_plan()
    query_plan.update(dict(scout_cfg.get("query_plan", {}) or {}))
    scout_cfg["query_plan"] = query_plan
    research_cfg["scout_config"] = scout_cfg

    sync_production_gate_thresholds(research_cfg)
    return research_cfg


def sync_production_gate_thresholds(research_cfg: Dict[str, Any]) -> None:
    gate = dict(research_cfg.get("production_gate_config", {}) or {})
    if not gate:
        return

    rigor = dict(research_cfg.get("rigor_config", {}) or {})
    thresholds = dict(rigor.get("thresholds", {}) or {})
    for gate_key, rigor_key in (
        ("min_worst_oos_sharpe", "min_worst_oos_sharpe"),
        ("min_profitable_splits_pct", "min_profitable_splits_pct"),
        ("min_deflated_sharpe_ratio", "min_deflated_sharpe_ratio"),
    ):
        if gate_key in gate and rigor_key not in thresholds:
            thresholds[rigor_key] = gate[gate_key]
    if thresholds:
        rigor["thresholds"] = thresholds

    cost_model = dict(rigor.get("cost_model", {}) or {})
    if "max_adv_participation" in gate and "max_adv_pct" not in cost_model:
        cost_model["max_adv_pct"] = gate["max_adv_participation"]
    if cost_model:
        rigor["cost_model"] = cost_model

    if rigor:
        research_cfg["rigor_config"] = rigor
