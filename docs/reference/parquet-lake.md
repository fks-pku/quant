# Parquet Lake Publishing

This project keeps mutable DuckDB sidecars under `quant/infrastructure/var/duckdb/live/`.
For cross-machine data sharing, publish those sidecars as day-partitioned Parquet to Alibaba OSS with:

```bash
python -m quant.scripts.publish_parquet_lake snapshot --mode full
```

The default remote is:

```text
oss:quant-duckdb-backup/vk-quant/parquet-lake
```

Configure that `oss` rclone remote once per machine before publishing or pulling:

```bash
rclone config create oss s3 provider Alibaba \
  access_key_id <ALIYUN_ACCESS_KEY_ID> \
  secret_access_key <ALIYUN_ACCESS_KEY_SECRET> \
  endpoint <OSS_ENDPOINT>
```

`snapshot --check-only` verifies that the `oss` remote exists before it reports success.

The publisher exports date-bearing datasets as Hive-style partitions:

```text
stock_ohlcv/year=2026/month=05/day=29/data.parquet
daily_basic/year=2026/month=05/day=29/data.parquet
security_status/year=2026/month=05/day=29/data.parquet
```

Run the publisher only after live data ingestion has finished. It opens the DuckDB files read-only, but full export still scans large files and should not compete with ingestion jobs.

## First Full Publish

```bash
python -m quant.scripts.publish_parquet_lake snapshot --mode full --keep-stage
```

`--keep-stage` keeps the temporary local Parquet directory for inspection. Remove it after checking if disk space matters.

## Daily Publish

After a daily update, publish only the affected day partition:

```bash
python -m quant.scripts.publish_parquet_lake snapshot --date 2026-06-01
```

The snapshot command exports only matching rows into a temporary local lake, then `rclone sync`s the exported `year=/month=/day=` directories to OSS. Re-running the same day overwrites that day partition only.

To backfill a date range, use:

```bash
python -m quant.scripts.publish_parquet_lake snapshot --start 2026-05-20 --end 2026-05-31
```

Use `--check-only` before installing a scheduled task to verify the DuckDB sidecars and `rclone` are available:

```bash
python -m quant.scripts.publish_parquet_lake snapshot --date 2026-06-01 --check-only
```

## Pull A Local Mirror

On another machine:

```bash
python -m quant.scripts.publish_parquet_lake pull
```

This mirrors OSS into `quant/infrastructure/var/parquet_lake/`.

## Restore DuckDB Sidecars

On a new machine, rebuild local DuckDB files from the pulled Parquet lake:

```bash
python -m quant.scripts.publish_parquet_lake restore --force
```

This writes sidecars under `quant/infrastructure/var/duckdb/live/`.

## Query With DuckDB

After pulling a local mirror:

```sql
SELECT *
FROM read_parquet(
  'quant/infrastructure/var/parquet_lake/stock_ohlcv/year=*/month=*/day=*/*.parquet',
  hive_partitioning = false
)
WHERE symbol = '600519';
```

For direct OSS reads, configure DuckDB `httpfs` with the Alibaba OSS S3-compatible endpoint and read the same `s3://` bucket prefix.
