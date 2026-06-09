from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

from quant.features.research.discovery.quality import discovery_score
from quant.features.research.models import ResearchConfig
from quant.infrastructure.research.repository import FileResearchStore
from quant.scripts.run_research import _create_configured_scout, _load_research_config, _resolve_source_arg


def run_scout(
    config: ResearchConfig,
    research_root: Optional[Path | str] = None,
    sources: Optional[List[str]] = None,
    status: str = "discovered",
    max_results: Optional[int] = None,
    summary_name: str = "",
) -> Dict[str, Any]:
    root = Path(research_root) if research_root is not None else _default_research_root()
    root.mkdir(parents=True, exist_ok=True)
    if sources is not None:
        config.sources = list(sources)
    if max_results is not None:
        config.max_results_per_source = int(max_results)

    scout = _create_configured_scout(config)
    rows = scout.search(sources=config.sources, max_results=config.max_results_per_source)
    store = FileResearchStore(root)
    store.write_discoveries(rows)
    for raw in rows:
        store.upsert_idea(raw, status=status, reason="Low-frequency public idea scout")

    summary = _summary(rows, config.sources, status)
    _write_summary(root, summary, summary_name)
    return summary


def _summary(rows, sources: List[str], status: str) -> Dict[str, Any]:
    source_counts = Counter(raw.source for raw in rows)
    top_ideas = []
    for raw in sorted(rows, key=lambda item: (-discovery_score(item), item.source, item.title))[:20]:
        quality = (raw.metadata or {}).get("discovery_quality") or {}
        top_ideas.append(
            {
                "title": raw.title,
                "source": raw.source,
                "source_url": raw.source_url,
                "published_date": raw.published_date,
                "discovery_score": float(quality.get("score", 0.0) or 0.0),
                "matched_terms": list(quality.get("matched_terms") or []),
                "risk_flags": list(quality.get("risk_flags") or []),
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": list(sources),
        "status": status,
        "stored": len(rows),
        "source_counts": dict(source_counts),
        "top_ideas": top_ideas,
    }


def _write_summary(root: Path, summary: Dict[str, Any], summary_name: str = "") -> None:
    idea_bank_dir = root / "idea_bank"
    idea_bank_dir.mkdir(parents=True, exist_ok=True)
    name = summary_name or f"low_frequency_scout_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    json_path = idea_bank_dir / f"{name}.json"
    md_path = idea_bank_dir / f"{name}.md"
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(_summary_markdown(summary), encoding="utf-8")


def _summary_markdown(summary: Dict[str, Any]) -> str:
    lines = [
        "# Low Frequency Idea Scout",
        "",
        f"- Generated: {summary['generated_at']}",
        f"- Sources: {', '.join(summary['sources'])}",
        f"- Stored: {summary['stored']}",
        f"- Status: {summary['status']}",
        "",
        "## Source Counts",
        "",
    ]
    for source, count in sorted((summary.get("source_counts") or {}).items()):
        lines.append(f"- {source}: {count}")
    lines.extend(["", "## Top Ideas", ""])
    for item in summary.get("top_ideas") or []:
        flags = ", ".join(item.get("risk_flags") or []) or "none"
        terms = ", ".join(item.get("matched_terms") or []) or "none"
        lines.extend(
            [
                f"### {item['title']}",
                f"- Source: [{item['source']}]({item['source_url']})",
                f"- Published: {item.get('published_date') or 'unknown'}",
                f"- Discovery Score: {item['discovery_score']:.2f}",
                f"- Matched Terms: {terms}",
                f"- Risk Flags: {flags}",
                "",
            ]
        )
    return "\n".join(lines)


def _default_research_root() -> Path:
    return Path(__file__).resolve().parent.parent / "infrastructure" / "var" / "research"


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan public low-frequency strategy ideas into the local idea bank")
    parser.add_argument("--source", default="config", help="config, all, or comma-separated source names")
    parser.add_argument("--max", type=int, default=None, dest="max_results", help="Max results per query/source")
    parser.add_argument("--status", default="discovered", help="Idea-bank status to assign")
    parser.add_argument("--min-score", type=float, default=None, help="Override scout_config.min_discovery_score")
    parser.add_argument("--summary-name", default="", help="Stable summary filename without extension")
    args = parser.parse_args()

    config = _load_research_config()
    config.sources = _resolve_source_arg(args.source, config.sources)
    if args.min_score is not None:
        scout_cfg = dict(config.scout_config or {})
        scout_cfg["min_discovery_score"] = float(args.min_score)
        config.scout_config = scout_cfg
    summary = run_scout(
        config,
        sources=config.sources,
        status=args.status,
        max_results=args.max_results,
        summary_name=args.summary_name,
    )
    print(f"Stored: {summary['stored']}")
    print(f"Sources: {', '.join(summary['sources'])}")
    print(f"Idea bank: {_default_research_root() / 'idea_bank' / 'idea_bank.json'}")


if __name__ == "__main__":
    main()
