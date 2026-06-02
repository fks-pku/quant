from datetime import date

import pytest

from quant.scripts.publish_parquet_lake import (
    DATASET_BY_NAME,
    LakeManifest,
    DatasetExport,
    export_lake,
    main,
    pull_lake,
    restore_lake,
    selected_datasets,
    upload_lake,
)


def test_export_lake_writes_day_partitioned_parquet_and_manifest(tmp_path):
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
    assert (output / "stock_ohlcv" / "year=2024" / "month=01" / "day=02" / "data.parquet").exists()
    assert (output / "stock_ohlcv" / "year=2024" / "month=02" / "day=03" / "data.parquet").exists()


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
    assert (output / "daily_basic" / "year=2024" / "month=02" / "day=03" / "data.parquet").exists()


def test_snapshot_date_exports_and_uploads_only_one_day(monkeypatch, tmp_path):
    duckdb = pytest.importorskip("duckdb")
    commands = []
    db_dir = tmp_path / "duckdb"
    stage = tmp_path / "stage"
    db_dir.mkdir()
    conn = duckdb.connect(str(db_dir / "cn_ohlcv.duckdb"))
    try:
        conn.execute("CREATE TABLE daily_cn_ochl(timestamp DATE, symbol VARCHAR, close DOUBLE)")
        conn.execute(
            """
            INSERT INTO daily_cn_ochl VALUES
            ('2024-01-02', '600000', 10.0),
            ('2024-01-03', '600000', 11.0)
            """
        )
    finally:
        conn.close()

    def fake_run(command, check, capture_output=False, text=False):
        if command == ["rclone", "listremotes"]:
            assert capture_output is True
            assert text is True

            class Result:
                stdout = "oss:\n"

            return Result()
        commands.append(command)
        assert check is True

    monkeypatch.setattr("quant.scripts.publish_parquet_lake.subprocess.run", fake_run)
    monkeypatch.setattr("quant.scripts.publish_parquet_lake.shutil.which", lambda name: "rclone")

    code = main(
        [
            "--duckdb-dir",
            str(db_dir),
            "--datasets",
            "stock_ohlcv",
            "snapshot",
            "--date",
            "2024-01-03",
            "--output-root",
            str(stage),
            "--remote-prefix",
            "oss:bucket/prefix",
            "--keep-stage",
        ]
    )

    partition = stage / "stock_ohlcv" / "year=2024" / "month=01" / "day=03"
    assert code == 0
    assert partition.exists()
    assert not (stage / "stock_ohlcv" / "year=2024" / "month=01" / "day=02").exists()
    assert commands == [
        [
            "rclone",
            "sync",
            str(partition),
            "oss:bucket/prefix/stock_ohlcv/year=2024/month=01/day=03",
            "--progress",
            "--transfers",
            "16",
            "--checkers",
            "32",
        ],
        ["rclone", "copyto", "--s3-no-check-bucket", str(stage / "_manifest.json"), "oss:bucket/prefix/_manifest.json"],
    ]


def test_snapshot_check_only_rejects_missing_remote(monkeypatch, tmp_path):
    duckdb = pytest.importorskip("duckdb")
    db_dir = tmp_path / "duckdb"
    db_dir.mkdir()
    conn = duckdb.connect(str(db_dir / "cn_ohlcv.duckdb"))
    try:
        conn.execute("CREATE TABLE daily_cn_ochl(timestamp DATE, symbol VARCHAR, close DOUBLE)")
    finally:
        conn.close()

    def fake_run(command, check, capture_output, text):
        assert command == ["rclone", "listremotes"]
        assert check is True
        assert capture_output is True
        assert text is True

        class Result:
            stdout = "other:\n"

        return Result()

    monkeypatch.setattr("quant.scripts.publish_parquet_lake.subprocess.run", fake_run)
    monkeypatch.setattr("quant.scripts.publish_parquet_lake.shutil.which", lambda name: "rclone")

    with pytest.raises(RuntimeError, match="Rclone remote 'oss' is not configured"):
        main(
            [
                "--duckdb-dir",
                str(db_dir),
                "--datasets",
                "stock_ohlcv",
                "snapshot",
                "--date",
                "2024-01-03",
                "--remote-prefix",
                "oss:bucket/prefix",
                "--check-only",
            ]
        )


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
        ["rclone", "copyto", "--s3-no-check-bucket", str(local / "_manifest.json"), "oss:bucket/prefix/_manifest.json"],
    ]


def test_range_upload_only_syncs_exported_partitions(monkeypatch, tmp_path):
    commands = []

    def fake_run(command, check):
        commands.append(command)
        assert check is True

    monkeypatch.setattr("quant.scripts.publish_parquet_lake.subprocess.run", fake_run)
    local = tmp_path / "lake"
    partition = local / "stock_ohlcv" / "year=2026" / "month=06" / "day=01"
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
            "oss:bucket/prefix/stock_ohlcv/year=2026/month=06/day=01",
            "--progress",
            "--transfers",
            "16",
            "--checkers",
            "32",
        ],
        ["rclone", "copyto", "--s3-no-check-bucket", str(local / "_manifest.json"), "oss:bucket/prefix/_manifest.json"],
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


def test_restore_lake_rebuilds_duckdb_sidecar_from_day_partitions(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    db_dir = tmp_path / "duckdb"
    lake = tmp_path / "lake"
    source_db = db_dir / "source.duckdb"
    db_dir.mkdir()
    conn = duckdb.connect(str(source_db))
    try:
        conn.execute("CREATE TABLE daily_cn_ochl(timestamp DATE, symbol VARCHAR, close DOUBLE)")
        conn.execute(
            """
            INSERT INTO daily_cn_ochl VALUES
            ('2024-01-02', '600000', 10.0),
            ('2024-01-03', '600000', 11.0)
            """
        )
        for day in ("02", "03"):
            partition = lake / "stock_ohlcv" / "year=2024" / "month=01" / f"day={day}"
            partition.mkdir(parents=True)
            conn.execute(
                f"""
                COPY (
                    SELECT *
                    FROM daily_cn_ochl
                    WHERE strftime(timestamp, '%d') = '{day}'
                )
                TO ?
                (FORMAT parquet, COMPRESSION zstd)
                """,
                [str(partition / "data.parquet")],
            )
    finally:
        conn.close()

    restored_dir = tmp_path / "restored"
    manifest = restore_lake(
        lake,
        restored_dir,
        [DATASET_BY_NAME["stock_ohlcv"]],
        force=True,
        allow_missing=False,
    )

    restored = duckdb.connect(str(restored_dir / "cn_ohlcv.duckdb"), read_only=True)
    try:
        rows = restored.execute("SELECT symbol, close FROM daily_cn_ochl ORDER BY timestamp").fetchall()
    finally:
        restored.close()
    assert manifest.datasets[0].rows == 2
    assert rows == [("600000", 10.0), ("600000", 11.0)]
