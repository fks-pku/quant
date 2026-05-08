import io
import logging
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

FF5_DAILY_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
MOMENTUM_DAILY_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Momentum_Factor_daily_CSV.zip"
CACHE_TTL_DAYS = 30


def download_ff5_daily(cache_dir: Any = None) -> Optional[pd.DataFrame]:
    return _download_factor_zip(
        FF5_DAILY_URL,
        _cache_dir(cache_dir) / "ff5_daily.parquet",
        {
            "Mkt-RF": "MKT",
            "SMB": "SMB",
            "HML": "HML",
            "RMW": "RMW",
            "CMA": "CMA",
            "RF": "RF",
        },
    )


def download_momentum_daily(cache_dir: Any = None) -> Optional[pd.DataFrame]:
    return _download_factor_zip(
        MOMENTUM_DAILY_URL,
        _cache_dir(cache_dir) / "ff_momentum_daily.parquet",
        {"Mom": "Mom"},
        fallback_paths=[_cache_dir(cache_dir) / "momentum_daily.parquet"],
    )


def _download_factor_zip(
    url: str,
    cache_path: Path,
    columns: dict,
    fallback_paths: Iterable[Path] = (),
) -> Optional[pd.DataFrame]:
    for path in (cache_path, *fallback_paths):
        if _is_fresh(path):
            return pd.read_parquet(path)
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        frame = _parse_zipped_csv(response.content, columns)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(cache_path)
        return frame
    except Exception as e:
        logger.warning(f"Factor download failed: {e}")
        for path in (cache_path, *fallback_paths):
            if not path.exists():
                continue
            try:
                return pd.read_parquet(path)
            except Exception as cache_error:
                logger.warning(f"Cached factor load failed: {cache_error}")
        return None


def _parse_zipped_csv(content: bytes, columns: dict) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        csv_name = archive.namelist()[0]
        text = archive.read(csv_name).decode("utf-8", errors="ignore")

    lines = text.splitlines()
    header_idx = next(i for i, line in enumerate(lines) if line.strip().startswith(","))
    rows = []
    header = [value.strip() for value in lines[header_idx].split(",")]
    for line in lines[header_idx + 1:]:
        values = [value.strip() for value in line.split(",")]
        if len(values) < len(header) or not values[0].isdigit():
            break
        rows.append(values[:len(header)])

    frame = pd.DataFrame(rows, columns=header)
    frame = frame.rename(columns=columns)
    keep = ["date"] + list(columns.values())
    frame = frame.rename(columns={frame.columns[0]: "date"})
    frame = frame[keep]
    frame["date"] = pd.to_datetime(frame["date"], format="%Y%m%d")
    for column in keep[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce") / 100.0
    return frame.dropna().sort_values("date").reset_index(drop=True)


def _cache_dir(cache_dir: Any = None) -> Path:
    if cache_dir is not None:
        return Path(cache_dir)
    return Path(__file__).resolve().parents[2] / "var" / "research" / "factor_zoo"


def _is_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    modified = datetime.fromtimestamp(path.stat().st_mtime)
    return datetime.now() - modified <= timedelta(days=CACHE_TTL_DAYS)
