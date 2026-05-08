import logging
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from quant.domain.ports.factor_data import FactorData

logger = logging.getLogger(__name__)


class FFFactorStore(FactorData):
    def __init__(self, cache_dir: Any = None):
        self._cache_dir = Path(cache_dir) if cache_dir is not None else _default_cache_dir()

    def get_factors(self, names: List[str], start: str, end: str) -> Any:
        requested = list(dict.fromkeys(names))
        frames = []
        for file_name in ("ff5_daily.parquet", "ff_momentum_daily.parquet", "momentum_daily.parquet", "cn3_daily.parquet"):
            path = self._cache_dir / file_name
            if not path.exists():
                continue
            try:
                frames.append(_read_factor_file(path))
            except Exception as e:
                logger.warning(f"Failed to read factor cache {path}: {e}")
        if not frames:
            return None

        factors = pd.concat(frames, axis=1)
        factors = factors.loc[:, ~factors.columns.duplicated()]
        available = [name for name in requested if name in factors.columns]
        if not available:
            return None

        start_ts = pd.to_datetime(start)
        end_ts = pd.to_datetime(end)
        result = factors.loc[(factors.index >= start_ts) & (factors.index <= end_ts), available].dropna(how="all")
        return result if not result.empty else None

    def list_factors(self) -> List[Dict[str, Any]]:
        return [
            {"name": "MKT", "frequency": "daily", "source": "Fama-French"},
            {"name": "SMB", "frequency": "daily", "source": "Fama-French"},
            {"name": "HML", "frequency": "daily", "source": "Fama-French"},
            {"name": "RMW", "frequency": "daily", "source": "Fama-French"},
            {"name": "CMA", "frequency": "daily", "source": "Fama-French"},
            {"name": "RF", "frequency": "daily", "source": "Fama-French"},
            {"name": "Mom", "frequency": "daily", "source": "Fama-French"},
        ]


def _read_factor_file(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame.set_index("date")
    frame.index = pd.to_datetime(frame.index)
    return frame.sort_index()


def _default_cache_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "var" / "research" / "factor_zoo"
