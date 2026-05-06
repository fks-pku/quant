from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from quant.domain.ports import FactorData


class FFFactorStore(FactorData):
    def __init__(self, cache_path: str | Path):
        self.cache_path = Path(cache_path)

    def get_factors(self, names: List[str], start: str, end: str) -> Any:
        df = self._read_cache()
        if df.empty:
            return df
        date_col = "date" if "date" in df.columns else "timestamp"
        if date_col not in df.columns:
            return pd.DataFrame()
        df[date_col] = pd.to_datetime(df[date_col])
        filtered = df[(df[date_col] >= pd.Timestamp(start)) & (df[date_col] <= pd.Timestamp(end))]
        columns = [date_col, *[name for name in names if name in filtered.columns]]
        return filtered[columns].reset_index(drop=True)

    def list_factors(self) -> List[Dict[str, Any]]:
        df = self._read_cache()
        if df.empty:
            return []
        excluded = {"date", "timestamp"}
        return [{"name": column, "source": "fama_french"} for column in df.columns if column not in excluded]

    def _read_cache(self) -> pd.DataFrame:
        if not self.cache_path.exists():
            return pd.DataFrame()
        if self.cache_path.suffix.lower() == ".parquet":
            return pd.read_parquet(self.cache_path)
        if self.cache_path.suffix.lower() in {".csv", ".txt"}:
            return pd.read_csv(self.cache_path)
        return pd.DataFrame()
