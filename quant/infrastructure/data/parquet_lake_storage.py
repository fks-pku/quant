"""Parquet lake storage adapter for local/OSS-backed market data."""

from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import duckdb
import pandas as pd

from quant.domain.models.order import Order
from quant.domain.ports.storage import Storage
from quant.shared.utils.logger import setup_logger


_PKG_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_PARQUET_LAKE_ROOT = _PKG_DIR / "var" / "parquet_lake"
_MANIFEST_NAME = "_manifest.json"
_RCLONE_SYNC_FLAGS = ("--progress", "--transfers", "16", "--checkers", "32")


@dataclass(frozen=True)
class ParquetDatasetSpec:
    name: str
    db_file: str
    table: str
    date_column: Optional[str]
    key_columns: tuple[str, ...]
    order_by: tuple[str, ...]

    @property
    def partitioned(self) -> bool:
        return self.date_column is not None


@dataclass(frozen=True)
class ParquetDatasetExport:
    name: str
    db_file: str
    table: str
    date_column: Optional[str]
    rows: int
    min_date: Optional[str]
    max_date: Optional[str]
    local_path: str
    partitioned: bool
    files: int
    bytes: int


@dataclass(frozen=True)
class ParquetLakeManifest:
    version: int
    created_at_utc: str
    mode: str
    start: Optional[str]
    end: Optional[str]
    datasets: list[ParquetDatasetExport]


PARQUET_DATASETS: tuple[ParquetDatasetSpec, ...] = (
    ParquetDatasetSpec("stock_ohlcv", "cn_ohlcv.duckdb", "daily_cn_ochl", "timestamp", ("timestamp", "symbol"), ("symbol", "timestamp")),
    ParquetDatasetSpec("etf_ohlcv", "cn_etf_ohlcv.duckdb", "daily_cn_ochl", "timestamp", ("timestamp", "symbol"), ("symbol", "timestamp")),
    ParquetDatasetSpec("index_ohlcv", "cn_index_ohlcv.duckdb", "daily_cn_ochl", "timestamp", ("timestamp", "symbol"), ("symbol", "timestamp")),
    ParquetDatasetSpec("daily_basic", "cn_daily_basic.duckdb", "cn_daily_basic", "trade_date", ("symbol", "trade_date"), ("symbol", "trade_date")),
    ParquetDatasetSpec("security_status", "cn_status.duckdb", "cn_security_status_daily", "trade_date", ("symbol", "trade_date"), ("symbol", "trade_date")),
    ParquetDatasetSpec(
        "financial_indicators",
        "cn_financial_indicators.duckdb",
        "cn_financial_indicators",
        "ann_date",
        ("symbol", "ann_date", "end_date"),
        ("symbol", "ann_date", "end_date"),
    ),
    ParquetDatasetSpec(
        "corporate_actions_dividends",
        "cn_corporate_actions.duckdb",
        "cn_dividends",
        "ex_date",
        ("symbol", "ex_date"),
        ("symbol", "ex_date"),
    ),
    ParquetDatasetSpec(
        "index_weight",
        "cn_index_weight.duckdb",
        "cn_index_weight",
        "trade_date",
        ("index_code", "trade_date", "symbol"),
        ("index_code", "trade_date", "symbol"),
    ),
    ParquetDatasetSpec("fund_nav", "cn_fund_nav.duckdb", "cn_fund_nav", "nav_date", ("symbol", "nav_date"), ("symbol", "nav_date")),
    ParquetDatasetSpec("fund_meta", "cn_fund_meta.duckdb", "cn_fund_instruments", None, ("symbol",), ("symbol",)),
)
PARQUET_DATASET_BY_NAME = {dataset.name: dataset for dataset in PARQUET_DATASETS}


class ParquetLakeStorage(Storage):
    def __init__(
        self,
        lake_root: str | Path = _DEFAULT_PARQUET_LAKE_ROOT,
        auto_flush_manifest: bool = True,
    ):
        self.lake_root = Path(lake_root)
        self.lake_root.mkdir(parents=True, exist_ok=True)
        self.auto_flush_manifest = auto_flush_manifest
        self.logger = setup_logger("ParquetLakeStorage")
        self._lock = threading.RLock()
        self._conn = duckdb.connect(":memory:")
        self._reader = None
        self._dirty = False
        self._touched_paths: dict[str, set[Path]] = {}

    def write_frame(self, dataset_name: str, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        spec = self._dataset(dataset_name)
        frame = self._normalize_frame_dates(df.copy(), spec)
        if spec.date_column:
            frame = frame.dropna(subset=[spec.date_column])
        if spec.key_columns:
            frame = frame.drop_duplicates(subset=list(spec.key_columns), keep="last")
        if frame.empty:
            return 0
        with self._lock:
            if spec.partitioned:
                self._write_partitioned(spec, frame)
            else:
                self._write_single_file(spec, frame)
            self._dirty = True
            self._close_reader()
            if self.auto_flush_manifest:
                self.flush_manifest()
        return len(frame)

    def save_bars(self, df: Any, timeframe: str = "1d") -> int:
        if timeframe not in ("1d", "day", "daily"):
            raise ValueError("Parquet lake storage only supports daily bars")
        frame = self._normalize_bar_frame(df)
        if frame.empty:
            return 0
        symbol = str(frame["symbol"].iloc[0])
        dataset = "etf_ohlcv" if self._is_cn_etf_symbol(symbol) else "stock_ohlcv"
        return self.write_frame(dataset, frame)

    def save_cn_index_bars(self, df: pd.DataFrame, timeframe: str = "1d") -> int:
        if timeframe not in ("1d", "day", "daily"):
            raise ValueError("Parquet lake storage only supports daily index bars")
        return self.write_frame("index_ohlcv", self._normalize_bar_frame(df))

    def save_cn_dividends(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        frame = df.copy()
        if "ex_date" in frame.columns:
            frame["ex_date"] = pd.to_datetime(frame["ex_date"], errors="coerce").dt.date
        cols = [
            "symbol",
            "ex_date",
            "cash_dividend",
            "stock_dividend",
            "allotment_ratio",
            "allotment_price",
            "record_date",
            "pay_date",
            "ann_date",
        ]
        for column in cols:
            if column not in frame.columns:
                frame[column] = "" if column in {"record_date", "pay_date", "ann_date"} else 0.0
        for column in ("cash_dividend", "stock_dividend", "allotment_ratio", "allotment_price"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0)
        for column in ("record_date", "pay_date", "ann_date"):
            frame[column] = frame[column].fillna("").astype(str)
        frame = frame[cols].dropna(subset=["symbol", "ex_date"]).drop_duplicates()
        if frame.empty:
            return 0
        frame = (
            frame.sort_values(["symbol", "ex_date", "ann_date"])
            .groupby(["symbol", "ex_date"], as_index=False)
            .agg(
                cash_dividend=("cash_dividend", "max"),
                stock_dividend=("stock_dividend", "max"),
                allotment_ratio=("allotment_ratio", "max"),
                allotment_price=("allotment_price", "max"),
                record_date=("record_date", "last"),
                pay_date=("pay_date", "last"),
                ann_date=("ann_date", "last"),
            )
        )
        with self._lock:
            self._delete_symbols("corporate_actions_dividends", frame["symbol"].dropna().astype(str).unique())
        return self.write_frame("corporate_actions_dividends", frame)

    def save_cn_fund_nav(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        frame = df.copy()
        if "symbol" not in frame.columns and "ts_code" in frame.columns:
            frame["symbol"] = frame["ts_code"].astype(str).str.split(".").str[0]
        if "nav_date" not in frame.columns and "trade_date" in frame.columns:
            frame["nav_date"] = frame["trade_date"]
        frame["nav_date"] = pd.to_datetime(frame["nav_date"], errors="coerce").dt.date
        if "ann_date" not in frame.columns:
            frame["ann_date"] = ""
        frame["ann_date"] = frame["ann_date"].fillna("").astype(str)
        for column in ("unit_nav", "accum_nav", "accum_div", "adj_nav", "net_asset", "total_netasset"):
            if column not in frame.columns:
                frame[column] = pd.NA
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        cols = ["symbol", "nav_date", "ann_date", "unit_nav", "accum_nav", "accum_div", "adj_nav", "net_asset", "total_netasset"]
        frame = frame[cols].dropna(subset=["symbol", "nav_date"]).drop_duplicates(subset=["symbol", "nav_date"], keep="last")
        return self.write_frame("fund_nav", frame)

    def save_cn_fund_instruments(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        frame = df.copy()
        if "symbol" not in frame.columns and "ts_code" in frame.columns:
            frame["symbol"] = frame["ts_code"].astype(str).str.split(".").str[0]
        frame = frame.dropna(subset=["symbol"]).drop_duplicates(subset=["symbol"], keep="last")
        return self.write_frame("fund_meta", frame)

    def load_ranges(self, dataset_name: str) -> Dict[str, Any]:
        spec = self._dataset(dataset_name)
        if not spec.date_column:
            return {}
        files = self._parquet_files(spec)
        if not files:
            return {}
        query = f"""
            SELECT
                symbol,
                MIN(CAST({self._quote_ident(spec.date_column)} AS DATE)) AS start_date,
                MAX(CAST({self._quote_ident(spec.date_column)} AS DATE)) AS end_date,
                COUNT(*) AS rows
            FROM read_parquet({self._sql_string_list(files)}, hive_partitioning=false)
            GROUP BY symbol
            ORDER BY symbol
        """
        rows = self._conn.execute(query).fetchall()
        return {str(symbol): (start, end, int(count or 0)) for symbol, start, end, count in rows}

    def load_latest_adj_factors(self, dataset_name: str) -> Dict[str, float]:
        files = self._parquet_files(self._dataset(dataset_name))
        if not files:
            return {}
        rows = self._conn.execute(
            f"""
            SELECT symbol, adj_factor
            FROM (
                SELECT
                    symbol,
                    adj_factor,
                    ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY timestamp DESC) AS rn
                FROM read_parquet({self._sql_string_list(files)}, hive_partitioning=false)
                WHERE adj_factor IS NOT NULL
            )
            WHERE rn = 1
            """
        ).fetchall()
        return {str(symbol): float(adj_factor) for symbol, adj_factor in rows if adj_factor is not None}

    def sync_touched(self, remote_prefix: str, dry_run: bool = False) -> None:
        self.flush_manifest()
        remote = remote_prefix.rstrip("/")
        for dataset, paths in sorted(self._touched_paths.items()):
            dataset_dir = self.lake_root / dataset
            for local_path in sorted(paths):
                relative = local_path.relative_to(dataset_dir)
                remote_path = f"{remote}/{dataset}/{relative.as_posix()}" if str(relative) != "." else f"{remote}/{dataset}"
                self._run_rclone(["sync", str(local_path), remote_path, *_RCLONE_SYNC_FLAGS], dry_run)
        self._run_rclone(["copyto", "--s3-no-check-bucket", str(self.lake_root / _MANIFEST_NAME), f"{remote}/{_MANIFEST_NAME}"], dry_run)

    def flush_manifest(self) -> None:
        exports = [self._dataset_export(spec) for spec in PARQUET_DATASETS if self._parquet_files(spec)]
        manifest = ParquetLakeManifest(
            version=1,
            created_at_utc=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            mode="incremental",
            start=None,
            end=None,
            datasets=exports,
        )
        (self.lake_root / _MANIFEST_NAME).write_text(
            json.dumps(asdict(manifest), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self._dirty = False

    def get_bars(
        self,
        symbol: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        timeframe: str = "1d",
    ) -> Any:
        return self._reader_storage().get_bars(symbol, start, end, timeframe)

    def get_symbols(self, timeframe: str = "1d", market: str = "hk") -> List[str]:
        return self._reader_storage().get_symbols(timeframe, market)

    def get_date_range(self, symbol: str, timeframe: str = "1d") -> Optional[Dict[str, datetime]]:
        return self._reader_storage().get_date_range(symbol, timeframe)

    def get_lot_size(self, symbol: str) -> int:
        return 100

    def save_order(self, order: Order) -> None:
        raise NotImplementedError("ParquetLakeStorage does not persist orders")

    def get_orders(self, symbol: Optional[str] = None, status: Optional[str] = None) -> Any:
        return pd.DataFrame()

    def save_portfolio_snapshot(self, snapshot: dict) -> None:
        raise NotImplementedError("ParquetLakeStorage does not persist portfolio snapshots")

    def get_portfolio_snapshots(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> Any:
        return pd.DataFrame()

    def save_strategy_snapshot(self, snapshot: dict) -> None:
        raise NotImplementedError("ParquetLakeStorage does not persist strategy snapshots")

    def get_strategy_snapshots(self, strategy_name: Optional[str] = None) -> List[dict]:
        return []

    def list_tables(self) -> List[str]:
        return self._reader_storage().list_tables()

    def table_row_count(self, table_name: str) -> int:
        return self._reader_storage().table_row_count(table_name)

    def close(self) -> None:
        with self._lock:
            if self._dirty:
                self.flush_manifest()
            self._close_reader()
            self._conn.close()

    def _write_partitioned(self, spec: ParquetDatasetSpec, frame: pd.DataFrame) -> None:
        assert spec.date_column is not None
        partition_days = pd.to_datetime(frame[spec.date_column], errors="coerce").dt.date
        for partition_day in sorted(day for day in partition_days.dropna().unique()):
            mask = partition_days == partition_day
            day_frame = frame.loc[mask].copy()
            partition_dir = self._partition_dir(spec, partition_day)
            target = partition_dir / "data.parquet"
            combined = self._combine_existing(target, day_frame, spec)
            self._write_parquet(target, combined)
            self._mark_touched(spec.name, partition_dir)

    def _write_single_file(self, spec: ParquetDatasetSpec, frame: pd.DataFrame) -> None:
        dataset_dir = self.lake_root / spec.name
        target = dataset_dir / "data.parquet"
        combined = self._combine_existing(target, frame, spec)
        self._write_parquet(target, combined)
        self._mark_touched(spec.name, dataset_dir)

    def _combine_existing(self, target: Path, frame: pd.DataFrame, spec: ParquetDatasetSpec) -> pd.DataFrame:
        if target.exists():
            existing = self._conn.execute("SELECT * FROM read_parquet(?, hive_partitioning=false)", [str(target)]).fetchdf()
            combined = pd.concat([existing, frame], ignore_index=True, sort=False)
        else:
            combined = frame.copy()
        combined = self._normalize_frame_dates(combined, spec)
        if spec.key_columns:
            combined = combined.drop_duplicates(subset=list(spec.key_columns), keep="last")
        return self._sort_frame(combined, spec.order_by)

    def _delete_symbols(self, dataset_name: str, symbols: Iterable[str]) -> None:
        symbol_set = {str(symbol) for symbol in symbols if str(symbol)}
        if not symbol_set:
            return
        spec = self._dataset(dataset_name)
        for target in self._parquet_files(spec):
            existing = self._conn.execute("SELECT * FROM read_parquet(?, hive_partitioning=false)", [str(target)]).fetchdf()
            if "symbol" not in existing.columns:
                continue
            kept = existing[~existing["symbol"].astype(str).isin(symbol_set)].copy()
            if len(kept) == len(existing):
                continue
            if kept.empty:
                target.unlink()
            else:
                self._write_parquet(target, self._sort_frame(kept, spec.order_by))
            self._mark_touched(spec.name, target.parent)
        self._dirty = True
        self._close_reader()

    def _write_parquet(self, target: Path, frame: pd.DataFrame) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp.parquet")
        if tmp.exists():
            tmp.unlink()
        self._conn.register("_parquet_lake_frame", frame)
        try:
            self._conn.execute(
                """
                COPY (SELECT * FROM _parquet_lake_frame)
                TO ?
                (FORMAT parquet, COMPRESSION zstd)
                """,
                [str(tmp)],
            )
        finally:
            self._conn.unregister("_parquet_lake_frame")
        tmp.replace(target)

    def _dataset_export(self, spec: ParquetDatasetSpec) -> ParquetDatasetExport:
        dataset_dir = self.lake_root / spec.name
        files = self._parquet_files(spec)
        file_count, size = self._path_stats(dataset_dir)
        rows = 0
        min_date = None
        max_date = None
        if files:
            if spec.date_column:
                row = self._conn.execute(
                    f"""
                    SELECT
                        COUNT(*),
                        MIN(CAST({self._quote_ident(spec.date_column)} AS DATE)),
                        MAX(CAST({self._quote_ident(spec.date_column)} AS DATE))
                    FROM read_parquet({self._sql_string_list(files)}, hive_partitioning=false)
                    """
                ).fetchone()
                rows = int(row[0] or 0)
                min_date = row[1].isoformat() if row and row[1] else None
                max_date = row[2].isoformat() if row and row[2] else None
            else:
                row = self._conn.execute(
                    f"SELECT COUNT(*) FROM read_parquet({self._sql_string_list(files)}, hive_partitioning=false)"
                ).fetchone()
                rows = int(row[0] or 0)
        return ParquetDatasetExport(
            name=spec.name,
            db_file=spec.db_file,
            table=spec.table,
            date_column=spec.date_column,
            rows=rows,
            min_date=min_date,
            max_date=max_date,
            local_path=str(dataset_dir),
            partitioned=spec.partitioned,
            files=file_count,
            bytes=size,
        )

    def _reader_storage(self):
        if self._dirty:
            self.flush_manifest()
        if self._reader is None:
            from quant.infrastructure.data.storage_duckdb import DuckDBStorage

            self._reader = DuckDBStorage(
                read_only=True,
                parquet_lake_root=str(self.lake_root),
                prefer_parquet_lake=True,
            )
        return self._reader

    def _close_reader(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    def _normalize_bar_frame(self, df: Any) -> pd.DataFrame:
        if df is None:
            return pd.DataFrame()
        frame = pd.DataFrame(df).copy()
        if frame.empty:
            return frame
        if "timestamp" in frame.columns:
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
        if "turnover" not in frame.columns:
            frame["turnover"] = pd.NA
        for column in ("adj_open", "adj_high", "adj_low", "adj_close", "adj_factor"):
            if column not in frame.columns:
                frame[column] = pd.NA
        cols = [
            "timestamp",
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "turnover",
            "adj_open",
            "adj_high",
            "adj_low",
            "adj_close",
            "adj_factor",
        ]
        return frame[[column for column in cols if column in frame.columns]].dropna(subset=["timestamp", "symbol"])

    def _normalize_frame_dates(self, frame: pd.DataFrame, spec: ParquetDatasetSpec) -> pd.DataFrame:
        if not spec.date_column or spec.date_column not in frame.columns:
            return frame
        if spec.date_column == "timestamp":
            frame[spec.date_column] = pd.to_datetime(frame[spec.date_column], errors="coerce")
        else:
            frame[spec.date_column] = pd.to_datetime(frame[spec.date_column], errors="coerce").dt.date
        return frame

    def _sort_frame(self, frame: pd.DataFrame, order_by: Sequence[str]) -> pd.DataFrame:
        columns = [column for column in order_by if column in frame.columns]
        if not columns:
            return frame.reset_index(drop=True)
        return frame.sort_values(columns).reset_index(drop=True)

    def _partition_dir(self, spec: ParquetDatasetSpec, value: date) -> Path:
        return self.lake_root / spec.name / f"year={value.year:04d}" / f"month={value.month:02d}" / f"day={value.day:02d}"

    def _parquet_files(self, spec: ParquetDatasetSpec) -> list[Path]:
        dataset_dir = self.lake_root / spec.name
        if not dataset_dir.exists():
            return []
        return sorted(path for path in dataset_dir.rglob("*.parquet") if path.is_file() and not path.name.endswith(".tmp.parquet"))

    def _mark_touched(self, dataset: str, path: Path) -> None:
        self._touched_paths.setdefault(dataset, set()).add(path)

    def _path_stats(self, path: Path) -> tuple[int, int]:
        files = [item for item in path.rglob("*.parquet") if item.is_file()]
        return len(files), sum(item.stat().st_size for item in files)

    def _dataset(self, dataset_name: str) -> ParquetDatasetSpec:
        try:
            return PARQUET_DATASET_BY_NAME[dataset_name]
        except KeyError as exc:
            raise ValueError(f"Unknown Parquet lake dataset {dataset_name}") from exc

    def _run_rclone(self, args: Sequence[str], dry_run: bool) -> None:
        command = ["rclone", *args]
        self.logger.info(" ".join(command))
        if dry_run:
            return
        subprocess.run(command, check=True)

    @staticmethod
    def _is_cn_etf_symbol(symbol: str) -> bool:
        return symbol.isdigit() and len(symbol) == 6 and symbol.startswith(("15", "16", "50", "51", "52", "56", "58"))

    @staticmethod
    def _quote_ident(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    @staticmethod
    def _sql_string_list(paths: Sequence[Path]) -> str:
        return "[" + ", ".join(ParquetLakeStorage._sql_string(str(path)) for path in paths) + "]"

    @staticmethod
    def _sql_string(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"
