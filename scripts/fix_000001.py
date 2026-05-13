"""Delete stale 000001 data and re-ingest as Ping An Bank."""
import duckdb
from datetime import datetime

db_path = "quant/infrastructure/var/duckdb/quant.duckdb"
conn = duckdb.connect(db_path)

tables = conn.execute("SHOW TABLES").fetchall()
print("Tables:", tables)

for t in tables:
    tname = t[0]
    try:
        cnt = conn.execute(f"SELECT count(*) FROM {tname} WHERE symbol='000001'").fetchone()
        if cnt and cnt[0] > 0:
            print(f"  {tname}: {cnt[0]} rows for 000001")
    except Exception:
        pass

conn.execute("DELETE FROM daily_cn_ochl WHERE symbol='000001'")
print("Deleted from daily_cn_ochl")
conn.close()

from quant.infrastructure.data.providers.tushare import TushareProvider
from quant.infrastructure.data.storage_duckdb import DuckDBStorage
storage = DuckDBStorage()
provider = TushareProvider(storage=storage)
provider.connect()

df = provider.fetch_daily_with_hfq("000001", datetime(2020, 1, 1), datetime(2025, 5, 1))
print(f"Fetched {len(df)} rows for 000001 (Ping An Bank)")
print(f"Price range: {df['close'].min():.2f} ~ {df['close'].max():.2f}")
print(df.head(3).to_string())

storage.save_bars(df)
print("Saved to DuckDB")
provider.disconnect()
storage.close()
