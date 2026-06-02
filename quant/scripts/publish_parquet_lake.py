"""Publish live DuckDB sidecars as an OSS-backed Parquet data lake."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

import duckdb


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DUCKDB_DIR = ROOT / "quant" / "infrastructure" / "var" / "duckdb" / "live"
DEFAULT_LAKE_ROOT = ROOT / "quant" / "infrastructure" / "var" / "parquet_lake"
DEFAULT_REMOTE_PREFIX = "oss:quant-duckdb-backup/vk-quant/parquet-lake"
MANIFEST_NAME = "_manifest.json"
RCLONE_SYNC_FLAGS = ("--progress", "--transfers", "16", "--checkers", "32")
RCLONE_S3_NO_CHECK_BUCKET = ("--s3-no-check-bucket",)


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    db_file: str
    table: str
    date_column: Optional[str]
    order_by: tuple[str, ...]


@dataclass(frozen=True)
class DatasetExport:
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
class LakeManifest:
    version: int
    created_at_utc: str
    mode: str
    start: Optional[str]
    end: Optional[str]
    datasets: list[DatasetExport]


DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec("stock_ohlcv", "cn_ohlcv.duckdb", "daily_cn_ochl", "timestamp", ("symbol", "timestamp")),
    DatasetSpec("etf_ohlcv", "cn_etf_ohlcv.duckdb", "daily_cn_ochl", "timestamp", ("symbol", "timestamp")),
    DatasetSpec("index_ohlcv", "cn_index_ohlcv.duckdb", "daily_cn_ochl", "timestamp", ("symbol", "timestamp")),
    DatasetSpec("daily_basic", "cn_daily_basic.duckdb", "cn_daily_basic", "trade_date", ("symbol", "trade_date")),
    DatasetSpec(
        "security_status",
        "cn_status.duckdb",
        "cn_security_status_daily",
        "trade_date",
        ("symbol", "trade_date"),
    ),
    DatasetSpec(
        "financial_indicators",
        "cn_financial_indicators.duckdb",
        "cn_financial_indicators",
        "ann_date",
        ("symbol", "ann_date", "end_date"),
    ),
    DatasetSpec(
        "corporate_actions_dividends",
        "cn_corporate_actions.duckdb",
        "cn_dividends",
        "ex_date",
        ("symbol", "ex_date"),
    ),
    DatasetSpec(
        "index_weight",
        "cn_index_weight.duckdb",
        "cn_index_weight",
        "trade_date",
        ("index_code", "trade_date", "symbol"),
    ),
    DatasetSpec("fund_nav", "cn_fund_nav.duckdb", "cn_fund_nav", "nav_date", ("symbol", "nav_date")),
    DatasetSpec("fund_meta", "cn_fund_meta.duckdb", "cn_fund_instruments", None, ("symbol",)),
)
DATASET_BY_NAME = {dataset.name: dataset for dataset in DATASETS}


def parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def selected_datasets(names: str) -> list[DatasetSpec]:
    if names == "all":
        return list(DATASETS)
    result = []
    for name in names.split(","):
        key = name.strip()
        if not key:
            continue
        if key not in DATASET_BY_NAME:
            raise ValueError(f"Unknown dataset {key}; valid values: {', '.join(DATASET_BY_NAME)}")
        result.append(DATASET_BY_NAME[key])
    if not result:
        raise ValueError("At least one dataset is required")
    return result


def export_lake(
    duckdb_dir: Path,
    output_root: Path,
    datasets: Sequence[DatasetSpec],
    mode: str,
    start: Optional[date],
    end: Optional[date],
    allow_missing: bool,
) -> LakeManifest:
    output_root.mkdir(parents=True, exist_ok=True)
    exports: list[DatasetExport] = []
    for dataset in datasets:
        db_path = duckdb_dir / dataset.db_file
        if not db_path.exists():
            if allow_missing:
                continue
            raise FileNotFoundError(db_path)
        dataset_dir = output_root / dataset.name
        if mode == "full" and dataset_dir.exists():
            shutil.rmtree(dataset_dir)
        dataset_dir.mkdir(parents=True, exist_ok=True)
        exports.append(_export_dataset(db_path, dataset, dataset_dir, start, end))
    manifest = LakeManifest(
        version=1,
        created_at_utc=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        mode=mode,
        start=start.isoformat() if start else None,
        end=end.isoformat() if end else None,
        datasets=exports,
    )
    write_manifest(output_root / MANIFEST_NAME, manifest)
    return manifest


def _export_dataset(
    db_path: Path,
    dataset: DatasetSpec,
    dataset_dir: Path,
    start: Optional[date],
    end: Optional[date],
) -> DatasetExport:
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        if not _table_exists(conn, dataset.table):
            raise ValueError(f"{db_path.name} does not contain table {dataset.table}")
        where = _where_clause(dataset.date_column, start, end)
        stats = _dataset_stats(conn, dataset, where)
        if dataset.date_column:
            _export_partitioned(conn, dataset, dataset_dir, where)
        else:
            _export_single_file(conn, dataset, dataset_dir, where)
    finally:
        conn.close()
    files, size = _path_stats(dataset_dir)
    return DatasetExport(
        name=dataset.name,
        db_file=db_path.name,
        table=dataset.table,
        date_column=dataset.date_column,
        rows=stats["rows"],
        min_date=stats["min_date"],
        max_date=stats["max_date"],
        local_path=str(dataset_dir),
        partitioned=dataset.date_column is not None,
        files=files,
        bytes=size,
    )


def _table_exists(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'main'
              AND table_name = ?
            """,
            [table],
        ).fetchone()[0]
    )


def _where_clause(date_column: Optional[str], start: Optional[date], end: Optional[date]) -> str:
    if not date_column:
        return ""
    clauses = [f"CAST({quote_ident(date_column)} AS DATE) IS NOT NULL"]
    if start:
        clauses.append(f"CAST({quote_ident(date_column)} AS DATE) >= DATE '{start.isoformat()}'")
    if end:
        clauses.append(f"CAST({quote_ident(date_column)} AS DATE) <= DATE '{end.isoformat()}'")
    return "WHERE " + " AND ".join(clauses)


def _dataset_stats(conn: duckdb.DuckDBPyConnection, dataset: DatasetSpec, where: str) -> dict[str, Optional[str] | int]:
    if dataset.date_column:
        date_expr = f"CAST({quote_ident(dataset.date_column)} AS DATE)"
        row = conn.execute(
            f"""
            SELECT COUNT(*), MIN({date_expr}), MAX({date_expr})
            FROM {quote_ident(dataset.table)}
            {where}
            """
        ).fetchone()
        return {
            "rows": int(row[0] or 0),
            "min_date": row[1].isoformat() if row and row[1] else None,
            "max_date": row[2].isoformat() if row and row[2] else None,
        }
    row = conn.execute(f"SELECT COUNT(*) FROM {quote_ident(dataset.table)}").fetchone()
    return {"rows": int(row[0] or 0), "min_date": None, "max_date": None}


def _export_partitioned(
    conn: duckdb.DuckDBPyConnection,
    dataset: DatasetSpec,
    dataset_dir: Path,
    where: str,
) -> None:
    if not dataset.date_column:
        raise ValueError(f"{dataset.name} is not date partitioned")
    date_expr = f"CAST({quote_ident(dataset.date_column)} AS DATE)"
    partitions = conn.execute(
        f"""
        SELECT DISTINCT
            CAST(EXTRACT(year FROM {date_expr}) AS INTEGER) AS year,
            CAST(EXTRACT(month FROM {date_expr}) AS INTEGER) AS month,
            CAST(EXTRACT(day FROM {date_expr}) AS INTEGER) AS day
        FROM {quote_ident(dataset.table)}
        {where}
        ORDER BY year, month, day
        """
    ).fetchall()
    for year, month, day in partitions:
        partition_dir = dataset_dir / f"year={int(year):04d}" / f"month={int(month):02d}" / f"day={int(day):02d}"
        partition_dir.mkdir(parents=True, exist_ok=True)
        target = partition_dir / "data.parquet"
        if target.exists():
            target.unlink()
        partition_where = _partition_where_clause(dataset.date_column, where, int(year), int(month), int(day))
        conn.execute(
            f"""
            COPY (
                SELECT *
                FROM {quote_ident(dataset.table)}
                {partition_where}
                {_order_by(dataset.order_by)}
            )
            TO ?
            (FORMAT parquet, COMPRESSION zstd)
            """,
            [str(target)],
        )


def _export_single_file(
    conn: duckdb.DuckDBPyConnection,
    dataset: DatasetSpec,
    dataset_dir: Path,
    where: str,
) -> None:
    order_by = _order_by(dataset.order_by)
    target = dataset_dir / "data.parquet"
    if target.exists():
        target.unlink()
    conn.execute(
        f"""
        COPY (
            SELECT *
            FROM {quote_ident(dataset.table)}
            {where}
            {order_by}
        )
        TO ?
        (FORMAT parquet, COMPRESSION zstd)
        """,
        [str(target)],
    )


def _partition_where_clause(date_column: str, where: str, year: int, month: int, day: int) -> str:
    date_expr = f"CAST({quote_ident(date_column)} AS DATE)"
    extra = [
        f"CAST(EXTRACT(year FROM {date_expr}) AS INTEGER) = {year}",
        f"CAST(EXTRACT(month FROM {date_expr}) AS INTEGER) = {month}",
        f"CAST(EXTRACT(day FROM {date_expr}) AS INTEGER) = {day}",
    ]
    if not where:
        return "WHERE " + " AND ".join(extra)
    return where + " AND " + " AND ".join(extra)


def _order_by(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    return "ORDER BY " + ", ".join(quote_ident(column) for column in columns)


def quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _path_stats(path: Path) -> tuple[int, int]:
    files = [item for item in path.rglob("*") if item.is_file()]
    return len(files), sum(item.stat().st_size for item in files)


def write_manifest(path: Path, manifest: LakeManifest) -> None:
    payload = asdict(manifest)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def upload_lake(local_root: Path, remote_prefix: str, manifest: LakeManifest, dry_run: bool) -> None:
    for dataset in manifest.datasets:
        local_path = local_root / dataset.name
        remote_path = f"{remote_prefix.rstrip('/')}/{dataset.name}"
        if manifest.mode == "range" and dataset.partitioned:
            for partition in partition_dirs(local_path):
                relative = partition.relative_to(local_path)
                run_rclone(["sync", str(partition), f"{remote_path}/{relative.as_posix()}", *RCLONE_SYNC_FLAGS], dry_run)
        else:
            run_rclone(["sync", str(local_path), remote_path, *RCLONE_SYNC_FLAGS], dry_run)
    run_rclone(
        [
            "copyto",
            *RCLONE_S3_NO_CHECK_BUCKET,
            str(local_root / MANIFEST_NAME),
            f"{remote_prefix.rstrip('/')}/{MANIFEST_NAME}",
        ],
        dry_run,
    )


def pull_lake(local_root: Path, remote_prefix: str, dry_run: bool) -> None:
    local_root.mkdir(parents=True, exist_ok=True)
    run_rclone(["sync", remote_prefix.rstrip("/"), str(local_root), *RCLONE_SYNC_FLAGS], dry_run)


def restore_lake(
    lake_root: Path,
    duckdb_dir: Path,
    datasets: Sequence[DatasetSpec],
    force: bool,
    allow_missing: bool,
) -> LakeManifest:
    duckdb_dir.mkdir(parents=True, exist_ok=True)
    exports: list[DatasetExport] = []
    for dataset in datasets:
        dataset_dir = lake_root / dataset.name
        if not dataset_dir.exists():
            if allow_missing:
                continue
            raise FileNotFoundError(dataset_dir)
        parquet_files = sorted(path for path in dataset_dir.rglob("*.parquet") if path.is_file())
        if not parquet_files:
            if allow_missing:
                continue
            raise FileNotFoundError(f"No parquet files found under {dataset_dir}")
        db_path = duckdb_dir / dataset.db_file
        if db_path.exists():
            if not force:
                raise RuntimeError(f"{db_path} exists; pass --force to overwrite")
            db_path.unlink()
        conn = duckdb.connect(str(db_path))
        try:
            files_sql = _sql_string_list(parquet_files)
            conn.execute(
                f"""
                CREATE TABLE {quote_ident(dataset.table)} AS
                SELECT *
                FROM read_parquet({files_sql}, hive_partitioning=false)
                {_order_by(dataset.order_by)}
                """
            )
            stats = _dataset_stats(conn, dataset, "")
        finally:
            conn.close()
        files, size = _path_stats(dataset_dir)
        exports.append(
            DatasetExport(
                name=dataset.name,
                db_file=db_path.name,
                table=dataset.table,
                date_column=dataset.date_column,
                rows=stats["rows"],
                min_date=stats["min_date"],
                max_date=stats["max_date"],
                local_path=str(dataset_dir),
                partitioned=dataset.date_column is not None,
                files=files,
                bytes=size,
            )
        )
    return LakeManifest(
        version=1,
        created_at_utc=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        mode="restore",
        start=None,
        end=None,
        datasets=exports,
    )


def run_rclone(args: Sequence[str], dry_run: bool) -> None:
    command = ["rclone", *args]
    print(" ".join(command))
    if dry_run:
        return
    subprocess.run(command, check=True)


def partition_dirs(dataset_dir: Path) -> list[Path]:
    return sorted(path for path in dataset_dir.glob("year=*/month=*/day=*") if path.is_dir())


def ensure_rclone_available() -> None:
    if not shutil.which("rclone"):
        raise RuntimeError("rclone is required for OSS sync; install rclone and configure the OSS remote first")


def ensure_rclone_remote(remote_prefix: str) -> None:
    remote = remote_prefix.split(":", 1)[0]
    if not remote:
        raise ValueError(f"Remote prefix must include an rclone remote name: {remote_prefix}")
    result = subprocess.run(["rclone", "listremotes"], check=True, capture_output=True, text=True)
    remotes = {line.strip().rstrip(":") for line in result.stdout.splitlines() if line.strip()}
    if remote not in remotes:
        raise RuntimeError(f"Rclone remote '{remote}' is not configured")


def validate_duckdb_inputs(duckdb_dir: Path, datasets: Sequence[DatasetSpec], allow_missing: bool) -> None:
    if not duckdb_dir.exists():
        raise FileNotFoundError(duckdb_dir)
    missing = [str(duckdb_dir / dataset.db_file) for dataset in datasets if not (duckdb_dir / dataset.db_file).exists()]
    if missing and not allow_missing:
        raise FileNotFoundError("Missing DuckDB sidecars: " + ", ".join(missing))


def _sql_string_list(paths: Sequence[Path]) -> str:
    return "[" + ", ".join(_sql_string(str(path)) for path in paths) + "]"


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _write_log(path: Optional[Path], message: str) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if not path.exists():
        path.write_text("", encoding="utf-8")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def default_stage_root() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(tempfile.gettempdir()) / "quant_parquet_lake" / stamp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export and publish live DuckDB sidecars as partitioned Parquet")
    parser.add_argument("--duckdb-dir", default=str(DEFAULT_DUCKDB_DIR))
    parser.add_argument("--datasets", default="all", help="Comma-separated dataset names or all")
    parser.add_argument("--start", default=None, help="Export rows on or after YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="Export rows on or before YYYY-MM-DD")
    parser.add_argument("--allow-missing", action="store_true", help="Skip missing DuckDB sidecars")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="Export DuckDB sidecars to local Parquet")
    export_parser.add_argument("--output-root", default=str(DEFAULT_LAKE_ROOT))
    export_parser.add_argument("--mode", choices=("full", "range"), default="full")

    publish_parser = subparsers.add_parser("publish", help="Export to a temporary local lake and upload to OSS")
    publish_parser.add_argument("--output-root", default=None, help="Stage root; defaults to a new /tmp path")
    publish_parser.add_argument("--remote-prefix", default=DEFAULT_REMOTE_PREFIX)
    publish_parser.add_argument("--mode", choices=("full", "range"), default="full")
    publish_parser.add_argument("--keep-stage", action="store_true")
    publish_parser.add_argument("--dry-run", action="store_true")

    snapshot_parser = subparsers.add_parser("snapshot", help="Routine DuckDB snapshot export and OSS sync")
    snapshot_parser.add_argument("--date", default=None, help="Refresh one YYYY-MM-DD day partition")
    snapshot_parser.add_argument("--start", default=None, help="Refresh rows on or after YYYY-MM-DD")
    snapshot_parser.add_argument("--end", default=None, help="Refresh rows on or before YYYY-MM-DD")
    snapshot_parser.add_argument("--output-root", default=None, help="Stage root; defaults to a new /tmp path")
    snapshot_parser.add_argument("--remote-prefix", default=DEFAULT_REMOTE_PREFIX)
    snapshot_parser.add_argument("--mode", choices=("full", "range"), default=None)
    snapshot_parser.add_argument("--keep-stage", action="store_true")
    snapshot_parser.add_argument("--dry-run", action="store_true")
    snapshot_parser.add_argument("--check-only", action="store_true")
    snapshot_parser.add_argument("--log-file", default=None)

    upload_parser = subparsers.add_parser("upload", help="Upload an existing local Parquet lake")
    upload_parser.add_argument("--output-root", default=str(DEFAULT_LAKE_ROOT))
    upload_parser.add_argument("--remote-prefix", default=DEFAULT_REMOTE_PREFIX)
    upload_parser.add_argument("--dry-run", action="store_true")

    pull_parser = subparsers.add_parser("pull", help="Pull the OSS Parquet lake to a local mirror")
    pull_parser.add_argument("--output-root", default=str(DEFAULT_LAKE_ROOT))
    pull_parser.add_argument("--remote-prefix", default=DEFAULT_REMOTE_PREFIX)
    pull_parser.add_argument("--dry-run", action="store_true")

    restore_parser = subparsers.add_parser("restore", help="Restore local DuckDB sidecars from a Parquet lake")
    restore_parser.add_argument("--lake-root", default=str(DEFAULT_LAKE_ROOT))
    restore_parser.add_argument("--output-dir", default=str(DEFAULT_DUCKDB_DIR))
    restore_parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    duckdb_dir = Path(args.duckdb_dir)
    start = parse_date(args.start)
    end = parse_date(args.end)
    if args.command == "snapshot":
        snapshot_date = parse_date(args.date)
        if snapshot_date and (start or end):
            parser.error("snapshot --date cannot be combined with --start or --end")
        if snapshot_date:
            start = snapshot_date
            end = snapshot_date
    if start and end and start > end:
        parser.error("--start must be <= --end")
    if args.command in {"export", "publish"} and args.mode == "range" and not (start or end):
        parser.error("--mode range requires --start or --end")
    datasets = selected_datasets(args.datasets)

    if args.command == "export":
        manifest = export_lake(duckdb_dir, Path(args.output_root), datasets, args.mode, start, end, args.allow_missing)
        print(f"Exported {len(manifest.datasets)} datasets to {args.output_root}")
        return 0

    if args.command == "publish":
        output_root = Path(args.output_root) if args.output_root else default_stage_root()
        if output_root.exists():
            shutil.rmtree(output_root)
        if not args.dry_run:
            ensure_rclone_available()
            ensure_rclone_remote(args.remote_prefix)
        manifest = export_lake(duckdb_dir, output_root, datasets, args.mode, start, end, args.allow_missing)
        upload_lake(output_root, args.remote_prefix, manifest, args.dry_run)
        if not args.keep_stage and not args.dry_run:
            shutil.rmtree(output_root)
        print(f"Published {len(manifest.datasets)} datasets to {args.remote_prefix}")
        return 0

    if args.command == "snapshot":
        log_file = Path(args.log_file) if args.log_file else None
        mode = args.mode or ("range" if start or end else "full")
        if mode == "range" and not (start or end):
            parser.error("snapshot --mode range requires --date, --start, or --end")
        validate_duckdb_inputs(duckdb_dir, datasets, args.allow_missing)
        if not args.dry_run:
            ensure_rclone_available()
            ensure_rclone_remote(args.remote_prefix)
        if args.check_only:
            _write_log(log_file, f"snapshot check-only ok mode={mode} datasets={len(datasets)}")
            print(f"Snapshot check passed for {len(datasets)} datasets")
            return 0
        output_root = Path(args.output_root) if args.output_root else default_stage_root()
        if output_root.exists():
            shutil.rmtree(output_root)
        _write_log(log_file, f"snapshot start mode={mode} start={start} end={end} stage={output_root}")
        manifest = export_lake(duckdb_dir, output_root, datasets, mode, start, end, args.allow_missing)
        upload_lake(output_root, args.remote_prefix, manifest, args.dry_run)
        if not args.keep_stage and not args.dry_run:
            shutil.rmtree(output_root)
        _write_log(log_file, f"snapshot complete datasets={len(manifest.datasets)} remote={args.remote_prefix}")
        print(f"Snapshot published {len(manifest.datasets)} datasets to {args.remote_prefix}")
        return 0

    if args.command == "upload":
        manifest_path = Path(args.output_root) / MANIFEST_NAME
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = LakeManifest(
            version=int(manifest_data["version"]),
            created_at_utc=str(manifest_data["created_at_utc"]),
            mode=str(manifest_data["mode"]),
            start=manifest_data.get("start"),
            end=manifest_data.get("end"),
            datasets=[DatasetExport(**item) for item in manifest_data["datasets"]],
        )
        if not args.dry_run:
            ensure_rclone_available()
            ensure_rclone_remote(args.remote_prefix)
        upload_lake(Path(args.output_root), args.remote_prefix, manifest, args.dry_run)
        return 0

    if args.command == "pull":
        if not args.dry_run:
            ensure_rclone_available()
            ensure_rclone_remote(args.remote_prefix)
        pull_lake(Path(args.output_root), args.remote_prefix, args.dry_run)
        return 0

    if args.command == "restore":
        manifest = restore_lake(Path(args.lake_root), Path(args.output_dir), datasets, args.force, args.allow_missing)
        print(f"Restored {len(manifest.datasets)} datasets to {args.output_dir}")
        return 0

    parser.error(f"Unhandled command {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
