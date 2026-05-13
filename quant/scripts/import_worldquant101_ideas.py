from __future__ import annotations

import argparse
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

from quant.features.research.discovery.quality import attach_discovery_quality
from quant.features.research.discovery.worldquant101 import build_worldquant101_raw_strategies
from quant.infrastructure.research.repository import FileResearchStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Import WorldQuant 101 alpha ideas into the local research idea bank")
    parser.add_argument("--status", default="factor_library", help="Idea status to write; use discovered to enqueue")
    parser.add_argument("--ready-only", action="store_true", help="Only import alphas supported by current daily_cn fields")
    parser.add_argument("--alpha", action="append", dest="alphas", help="Alpha numbers or ranges, e.g. 1,5,20-25")
    parser.add_argument("--limit", type=int, default=None, help="Maximum imported rows after filtering")
    args = parser.parse_args()

    var_root = Path(__file__).resolve().parent.parent / "infrastructure" / "var" / "research"
    var_root.mkdir(parents=True, exist_ok=True)
    alpha_numbers = _parse_alpha_args(args.alphas)
    raws = build_worldquant101_raw_strategies(alpha_numbers=alpha_numbers, ready_only=args.ready_only)
    if args.limit is not None:
        raws = raws[: max(0, int(args.limit))]

    store = FileResearchStore(str(var_root))
    ready = 0
    blocked = 0
    for raw in raws:
        raw = attach_discovery_quality(raw)
        metadata = raw.metadata or {}
        if metadata.get("a_share_ready"):
            ready += 1
        else:
            blocked += 1
        store.upsert_idea(raw, status=args.status, reason=_reason(metadata))

    print(f"Imported: {len(raws)}")
    print(f"A-share daily_cn ready: {ready}")
    print(f"Needs field/proxy mapping: {blocked}")
    print(f"Status: {args.status}")
    print(f"Idea bank: {var_root / 'idea_bank' / 'idea_bank.json'}")


def _parse_alpha_args(values: list[str] | None) -> list[int] | None:
    if not values:
        return None
    numbers = set()
    for value in values:
        for chunk in str(value).replace(" ", "").split(","):
            if not chunk:
                continue
            if "-" in chunk:
                start, end = chunk.split("-", 1)
                numbers.update(range(int(start), int(end) + 1))
            else:
                numbers.add(int(chunk))
    return sorted(number for number in numbers if 1 <= number <= 101)


def _reason(metadata: dict) -> str:
    if metadata.get("a_share_ready"):
        return "WorldQuant 101 factor seed; local daily_cn fields available, exact formula implementation pending"
    missing = ", ".join(metadata.get("missing_daily_cn_fields") or [])
    return f"WorldQuant 101 factor seed; requires field/proxy mapping before validation: {missing}"


if __name__ == "__main__":
    main()
