import hashlib
import logging
from typing import List

from quant.features.research.models import RawStrategy

logger = logging.getLogger(__name__)


def deduplicate(strategies: List[RawStrategy]) -> List[RawStrategy]:
    seen = set()
    unique = []
    skipped = 0
    for s in strategies:
        key = _dedup_key(s)
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        unique.append(s)
    if skipped:
        logger.info(f"Dedup: skipped {skipped} duplicates")
    return unique


def _dedup_key(s: RawStrategy) -> str:
    text = f"{s.title.lower().strip()}::{s.description.lower().strip()[:200]}"
    return hashlib.md5(text.encode()).hexdigest()
