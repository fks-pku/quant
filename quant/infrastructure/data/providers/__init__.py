"""Data providers package."""

from quant.infrastructure.data.providers.base import DataProvider
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
from quant.infrastructure.data.providers.akshare import AkshareProvider
from quant.infrastructure.data.providers.tushare import TushareProvider

__all__ = ["DataProvider", "DuckDBProvider", "AkshareProvider", "TushareProvider"]
