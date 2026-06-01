# Parquet Lake Publishing

This project keeps mutable DuckDB sidecars under `quant/infrastructure/var/duckdb/live/`.
For cross-machine data sharing, publish those sidecars as partitioned Parquet to Alibaba OSS with:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/quant_pycache python3 quant/scripts/publish_parquet_lake.py publish
```

The default remote is:

```text
oss:quant-duckdb-backup/vk-quant/parquet-lake
```

The publisher exports date-bearing datasets as Hive-style partitions:

```text
stock_ohlcv/year=2026/month=05/data.parquet
daily_basic/year=2026/month=05/data.parquet
security_status/year=2026/month=05/data.parquet
```

Run the publisher only after live data ingestion has finished. It opens the DuckDB files read-only, but full export still scans large files and should not compete with ingestion jobs.

## First Full Publish

```bash
PYTHONPYCACHEPREFIX=/private/tmp/quant_pycache python3 quant/scripts/publish_parquet_lake.py publish \
  --mode full \
  --keep-stage
```

`--keep-stage` keeps the temporary local Parquet directory for inspection. Remove it after checking if disk space matters.

## Daily Range Publish

After a daily update, publish only the affected date range:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/quant_pycache python3 quant/scripts/publish_parquet_lake.py \
  --start 2026-06-01 \
  --end 2026-06-01 \
  publish \
  --mode range
```

The range mode exports only matching rows into a temporary local lake, then `rclone sync`s the exported dataset directories to OSS.

## Pull A Local Mirror

On another machine:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/quant_pycache python3 quant/scripts/publish_parquet_lake.py pull
```

This mirrors OSS into `quant/infrastructure/var/parquet_lake/`.

## Query With DuckDB

After pulling a local mirror:

```sql
SELECT *
FROM read_parquet(
  'quant/infrastructure/var/parquet_lake/stock_ohlcv/year=*/month=*/*.parquet',
  hive_partitioning = true
)
WHERE symbol = '600519';
```

For direct OSS reads, configure DuckDB `httpfs` with the Alibaba OSS S3-compatible endpoint and read the same `s3://` bucket prefix.
