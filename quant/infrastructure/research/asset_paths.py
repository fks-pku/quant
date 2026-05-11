from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable


IDEA_BANK_DIR = Path("idea_bank")
REPORTS_DIR = Path("reports")
LATEST_REPORT_DIR = REPORTS_DIR / "latest"

IDEA_BANK_JSON = IDEA_BANK_DIR / "idea_bank.json"
IDEA_BANK_MD = IDEA_BANK_DIR / "idea_bank.md"
DISCOVERED_STRATEGIES_MD = IDEA_BANK_DIR / "discovered_strategies.md"
LEGACY_IDEA_BANK_JSON = Path("idea_bank.json")
LEGACY_IDEA_BANK_MD = Path("idea_bank.md")

REPORT_HTML = Path("full_research_report.html")
REPORT_MD = Path("full_research_report.md")
LAST_RESULT_JSON = Path("last_result.json")
STRATEGY_EVALUATION_MD = Path("strategy_evaluation.md")
LATEST_REPORT_METADATA = LATEST_REPORT_DIR / "metadata.json"


def report_dir(report_id: str) -> Path:
    return REPORTS_DIR / safe_asset_key(report_id)


def latest_report_html_path() -> Path:
    return LATEST_REPORT_DIR / REPORT_HTML


def report_id_for_result(data: Dict[str, Any], hypotheses: Iterable[Dict[str, Any]]) -> str:
    strategy_ids = [str(row.get("strategy_id", "")).strip() for row in hypotheses if row.get("strategy_id")]
    unique_strategy_ids = sorted(set(strategy_ids))
    if len(unique_strategy_ids) == 1:
        return safe_asset_key(unique_strategy_ids[0])

    log_strategy_ids = [
        str((entry.get("scores") or {}).get("strategy_id", "")).strip()
        for entry in data.get("log", []) or []
        if isinstance(entry, dict) and (entry.get("scores") or {}).get("strategy_id")
    ]
    unique_log_strategy_ids = sorted(set(log_strategy_ids))
    if len(unique_log_strategy_ids) == 1:
        return safe_asset_key(unique_log_strategy_ids[0])

    titles = [str(row.get("title", "")).strip() for row in hypotheses if row.get("title")]
    unique_titles = sorted(set(titles))
    if len(unique_titles) == 1:
        return safe_asset_key(unique_titles[0])

    run_id = str(data.get("run_id", "")).strip()
    if run_id:
        return safe_asset_key(run_id)
    return "research_pipeline"


def safe_asset_key(value: Any, fallback: str = "research_pipeline") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9._-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._-")
    return (text[:120] or fallback).strip("._-") or fallback
