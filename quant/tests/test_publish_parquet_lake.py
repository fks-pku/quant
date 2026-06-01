from datetime import date

import pytest

from quant.scripts.publish_parquet_lake import (
    DATASET_BY_NAME,
    LakeManifest,
    DatasetExport,
    export_lake,
    pull_lake,
    selected_datasets,
    upload_lake,
)


def test_export_lake_writes_partitioned_parquet_and_manifest(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    db_dir = tmp_path / "duckdb"
    output = tmp_path / "lake"
    db_dir.mkdir()
    conn = duckdb.connect(str(db_dir / "cn_ohlcv.duckdb"))
    try:
        conn.execute("CREATE TABLE daily_cn_ochl(timestamp DATE, symbol VARCHAR, close DOUBLE)")
        conn.execute(
            """
            INSERT INTO daily_cn_ochl VALUES
            ('2024-01-02', '600000', 10.0),
            ('2024-02-03', '600000', 11.0)
            """
        )
    finally:
        conn.close()

    manifest = export_lake(
        db_dir,
        output,
        [DATASET_BY_NAME["stock_ohlcv"]],
        "full",
        None,
        None,
        False,
    )

    dataset = manifest.datasets[0]
    assert dataset.rows == 2
    assert dataset.min_date == "2024-01-02"
    assert dataset.max_date == "2024-02-03"
    assert (output / "_manifest.json").exists()
    assert (output / "stock_ohlcv" / "year=2024" / "month=01" / "data.parquet").exists()
    assert (output / "stock_ohlcv" / "year=2024" / "month=02" / "data.parquet").exists()


def test_export_lake_applies_date_range(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    db_dir = tmp_path / "duckdb"
    output = tmp_path / "lake"
    db_dir.mkdir()
    conn = duckdb.connect(str(db_dir / "cn_daily_basic.duckdb"))
    try:
        conn.execute("CREATE TABLE cn_daily_basic(trade_date DATE, symbol VARCHAR, total_mv DOUBLE)")
        conn.execute(
            """
            INSERT INTO cn_daily_basic VALUES
            ('2024-01-02', '600000', 100.0),
            ('2024-02-03', '600000', 110.0)
            """
        )
    finally:
        conn.close()

    manifest = export_lake(
        db_dir,
        output,
        [DATASET_BY_NAME["daily_basic"]],
        "full",
        date(2024, 2, 1),
        None,
        False,
    )

    dataset = manifest.datasets[0]
    assert dataset.rows == 1
    assert dataset.min_date == "2024-02-03"
    assert not (output / "daily_basic" / "year=2024" / "month=01").exists()
    assert (output / "daily_basic" / "year=2024" / "month=02" / "data.parquet").exists()


def test_selected_datasets_rejects_unknown_name():
    with pytest.raises(ValueError, match="Unknown dataset"):
        selected_datasets("stock_ohlcv,missing")


def test_upload_lake_uses_rclone_sync_and_manifest_copy(monkeypatch, tmp_path):
    commands = []

    def fake_run(command, check):
        commands.append(command)
        assert check is True

    monkeypatch.setattr("quant.scripts.publish_parquet_lake.subprocess.run", fake_run)
    local = tmp_path / "lake"
    (local / "stock_ohlcv").mkdir(parents=True)
    (local / "_manifest.json").write_text("{}", encoding="utf-8")
    manifest = LakeManifest(
        version=1,
        created_at_utc="2026-06-01T00:00:00Z",
        mode="full",
        start=None,
        end=None,
        datasets=[
            DatasetExport(
                name="stock_ohlcv",
                db_file="cn_ohlcv.duckdb",
                table="daily_cn_ochl",
                date_column="timestamp",
                rows=1,
                min_date="2024-01-02",
                max_date="2024-01-02",
                local_path=str(local / "stock_ohlcv"),
                partitioned=True,
                files=1,
                bytes=100,
            )
        ],
    )

    upload_lake(local, "oss:bucket/prefix", manifest, dry_run=False)

    assert commands == [
        [
            "rclone",
            "sync",
            str(local / "stock_ohlcv"),
            "oss:bucket/prefix/stock_ohlcv",
            "--progress",
            "--transfers",
            "16",
            "--checkers",
            "32",
        ],
        ["rclone", "copyto", str(local / "_manifest.json"), "oss:bucket/prefix/_manifest.json"],
    ]


def test_range_upload_only_syncs_exported_partitions(monkeypatch, tmp_path):
    commands = []

    def fake_run(command, check):
        commands.append(command)
        assert check is True

    monkeypatch.setattr("quant.scripts.publish_parquet_lake.subprocess.run", fake_run)
    local = tmp_path / "lake"
    partition = local / "stock_ohlcv" / "year=2026" / "month=06"
    partition.mkdir(parents=True)
    (local / "_manifest.json").write_text("{}", encoding="utf-8")
    manifest = LakeManifest(
        version=1,
        created_at_utc="2026-06-01T00:00:00Z",
        mode="range",
        start="2026-06-01",
        end="2026-06-01",
        datasets=[
            DatasetExport(
                name="stock_ohlcv",
                db_file="cn_ohlcv.duckdb",
                table="daily_cn_ochl",
                date_column="timestamp",
                rows=1,
                min_date="2026-06-01",
                max_date="2026-06-01",
                local_path=str(local / "stock_ohlcv"),
                partitioned=True,
                files=1,
                bytes=100,
            )
        ],
    )

    upload_lake(local, "oss:bucket/prefix", manifest, dry_run=False)

    assert commands == [
        [
            "rclone",
            "sync",
            str(partition),
            "oss:bucket/prefix/stock_ohlcv/year=2026/month=06",
            "--progress",
            "--transfers",
            "16",
            "--checkers",
            "32",
        ],
        ["rclone", "copyto", str(local / "_manifest.json"), "oss:bucket/prefix/_manifest.json"],
    ]


def test_pull_lake_uses_rclone_sync(monkeypatch, tmp_path):
    commands = []

    def fake_run(command, check):
        commands.append(command)
        assert check is True

    monkeypatch.setattr("quant.scripts.publish_parquet_lake.subprocess.run", fake_run)
    pull_lake(tmp_path / "lake", "oss:bucket/prefix/", dry_run=False)
    assert commands == [
        [
            "rclone",
            "sync",
            "oss:bucket/prefix",
            str(tmp_path / "lake"),
            "--progress",
            "--transfers",
            "16",
            "--checkers",
            "32",
        ]
    ]
