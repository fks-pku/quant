from typing import Iterable, List

from quant.features.research.models import RegimeLabel


class RegimeDetector:
    def label_splits(self, splits: Iterable[dict]) -> List[RegimeLabel]:
        return [
            RegimeLabel(
                regime="unknown",
                start_date=str(split.get("test_start", "")),
                end_date=str(split.get("test_end", "")),
                confidence=0.0,
            )
            for split in splits
        ]
