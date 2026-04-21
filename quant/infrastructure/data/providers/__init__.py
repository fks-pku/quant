"""Data providers package."""

from quant.infrastructure.data.providers.base import DataProvider
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider

__all__ = ["DataProvider", "DuckDBProvider"]
