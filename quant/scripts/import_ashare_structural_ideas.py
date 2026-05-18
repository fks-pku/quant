from __future__ import annotations

import argparse
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

from quant.features.research.discovery.ashare_structural import build_ashare_structural_raw_strategies
from quant.features.research.discovery.quality import attach_discovery_quality
from quant.infrastructure.research.repository import FileResearchStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Import A-share structural daily ideas into the local research idea bank")
    parser.add_argument("--status", default="ashare_structural", help="Idea status to write; use discovered to enqueue")
    parser.add_argument("--idea", action="append", dest="ideas", help="Idea id or formula key, comma-separated")
    parser.add_argument("--limit", type=int, default=None, help="Maximum imported rows after filtering")
    args = parser.parse_args()

    var_root = Path(__file__).resolve().parent.parent / "infrastructure" / "var" / "research"
    var_root.mkdir(parents=True, exist_ok=True)
    raws = build_ashare_structural_raw_strategies(idea_ids=_parse_ideas(args.ideas))
    if args.limit is not None:
        raws = raws[: max(0, int(args.limit))]

    store = FileResearchStore(str(var_root))
    for raw in raws:
        raw = attach_discovery_quality(raw)
        store.upsert_idea(raw, status=args.status, reason=_reason(raw.metadata or {}))

    print(f"Imported: {len(raws)}")
    print("A-share daily_cn_ochl ready: {0}".format(len(raws)))
    print(f"Status: {args.status}")
    print(f"Idea bank: {var_root / 'idea_bank' / 'idea_bank.json'}")


def _parse_ideas(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    ideas = []
    for value in values:
        ideas.extend(item for item in str(value).replace(" ", "").split(",") if item)
    return ideas


def _reason(metadata: dict) -> str:
    return (
        f"A-share structural daily idea; formula={metadata.get('formula_key', '')}; "
        "local daily_cn_ochl fields available, strict execution validation still required"
    )


if __name__ == "__main__":
    main()
