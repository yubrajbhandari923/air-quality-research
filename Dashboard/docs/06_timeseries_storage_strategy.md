# Time-Series Storage Strategy

## The question

> Is storing every 1-minute sensor reading as a database row too heavy? Is there a better approach?

Short answer: **it depends on scale, and there are multiple good options**. At the scale of this project (a handful of sensors, ~10 million rows/year), PostgreSQL handles it fine with the indexes already in place. The dashboard never queries raw 1-minute rows for charts — it always aggregates first. But as sensors scale up, the architecture should evolve.

---

## Current row volume (reference)

| Sensors | Pollutants | Interval | Rows/day | Rows/year |
|---------|-----------|----------|----------|-----------|
| 1 | 12 | 1 min | 17,280 | 6.3M |
| 5 | 12 | 1 min | 86,400 | 31.5M |
| 20 | 12 | 1 min | 345,600 | 126M |
| 50 | 12 | 1 min | 864,000 | 315M |

PostgreSQL handles 100–300M rows in a single table with good indexes. Beyond that, partitioning or a specialised time-series engine pays off.

---

## Strategy 1 (current): PostgreSQL + pre-aggregated daily stats

**What's in place:**
- `CanonicalReading` table stores every reading with compound index `(sensor, pollutant, original_ts)`
- `DailyAggregate` stores pre-computed per-sensor/pollutant daily stats (mean, median, p25, p75, p95, max, completeness)
- Charts always query aggregates or use a `LIMIT`/`RESAMPLE` on `CanonicalReading`

**Works well up to:** ~5–10 sensors, 2–3 years of 1-minute data (~200M rows) in PostgreSQL.

**How to speed up further:**
```sql
-- Partition by month (PostgreSQL declarative partitioning)
-- Run once when you hit ~50M rows:
ALTER TABLE readings_canonicalreading PARTITION BY RANGE (original_ts);
CREATE TABLE readings_y2025m11 PARTITION OF readings_canonicalreading
  FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
-- etc.
```

---

## Strategy 2 (recommended for scale): TimescaleDB

[TimescaleDB](https://www.timescale.com/) is a PostgreSQL extension that adds automatic time partitioning ("hypertables"), compression, and fast time-series aggregation functions.

```sql
-- Convert existing table to hypertable (one command)
SELECT create_hypertable('readings_canonicalreading', 'original_ts',
                         chunk_time_interval => INTERVAL '1 week');

-- Enable column-store compression (10–20× size reduction for older chunks)
ALTER TABLE readings_canonicalreading SET (
  timescaledb.compress,
  timescaledb.compress_orderby = 'original_ts DESC'
);
SELECT add_compression_policy('readings_canonicalreading', INTERVAL '30 days');
```

After this, all Django ORM queries work unchanged. TimescaleDB handles partitioning transparently. Use the `time_bucket` function for fast aggregations:

```sql
SELECT time_bucket('1 hour', original_ts) AS hour,
       AVG(raw_value) AS mean_pm25
FROM readings_canonicalreading
WHERE sensor_id = 1 AND pollutant = 'PM25'
GROUP BY 1 ORDER BY 1;
```

---

## Strategy 3: DuckDB for ad-hoc analytics (keep Django for the web layer)

DuckDB is an embedded analytical database that can query Parquet files or CSV files directly — without loading data into a server. It is ideal for the research notebook analysis (already done in the Jupyter notebooks) and for one-off deep queries.

**Architecture:**
- Django/PostgreSQL handles the live dashboard (inserts, last-N-day queries)
- Raw CSV files (original Sensirion exports) stored in `Data/Belauri/`
- DuckDB queries the CSVs directly for research analysis

```python
import duckdb

con = duckdb.connect()
result = con.execute("""
    SELECT
        date_trunc('hour', CAST("Timestamp" AS TIMESTAMPTZ)) AS hour,
        AVG("PM2.5") AS pm25_mean
    FROM read_csv_auto('../Data/Belauri/*.csv', skip=1)
    WHERE "Serial Number" = '81432434001'
      AND "Timestamp" >= '2025-12-01'
    GROUP BY 1 ORDER BY 1
""").df()
```

DuckDB scans multi-GB CSV files in seconds using vectorised columnar execution. No server setup required.

**Recommended export pipeline:**
```bash
# Nightly: export yesterday's readings from Django to Parquet
python manage.py shell -c "
import duckdb
from apps.readings.models import CanonicalReading
import pandas as pd
from datetime import date, timedelta

yesterday = date.today() - timedelta(days=1)
qs = CanonicalReading.objects.filter(
    original_ts__date=yesterday, is_duplicate=False
).values('original_ts', 'sensor_id', 'pollutant', 'raw_value', 'quality_flag')
df = pd.DataFrame(list(qs))
df.to_parquet(f'data/parquet/{yesterday}.parquet', index=False)
"
```

---

## Strategy 4: Separate hot/cold storage

For long-running deployments (5+ years, 50+ sensors):

| Layer | Storage | What goes here |
|-------|---------|---------------|
| **Hot** (last 90 days) | PostgreSQL (TimescaleDB) | All reads, real-time dashboard |
| **Warm** (90 days–2 years) | PostgreSQL compressed chunks | Available for chart queries, slower |
| **Cold** (> 2 years) | Parquet files on S3/object storage | Archive, accessible via DuckDB |
| **Aggregates** | PostgreSQL `DailyAggregate` table | All-time daily stats, always fast |

Downsampling policy: after 90 days, delete raw 1-minute rows but keep hourly aggregates. After 2 years, delete hourly aggregates but keep daily aggregates. The `DailyAggregate` table is the permanent record; raw rows are disposable.

---

## Decision guide

| Scale | Recommendation |
|-------|---------------|
| 1–5 sensors, < 3 years | PostgreSQL + `DailyAggregate` (current setup) |
| 5–20 sensors, 3–10 years | TimescaleDB extension (drop-in upgrade) |
| 20+ sensors, multi-year | TimescaleDB + Parquet cold archive + DuckDB for research |
| Pure research / no web | DuckDB directly on CSV/Parquet, no PG at all |

---

## What the dashboard does right now

The charts on the Analysis page **never return raw 1-minute rows to the browser**. Every API endpoint aggregates first:

- `timeseries` chart → hourly median, max 2,000 points returned
- `diurnal` chart → GROUP BY hour-of-day, 24 points returned
- `monthly` chart → GROUP BY month, ≤ 36 points returned
- `export` endpoint → returns raw rows but capped by access tier (500–500k)

So the browser always receives a small, pre-aggregated payload. The database bears the query load, not the network.

---

## Practical recommendation for this project

At the current scale (2–3 sensors, ~18 months of data ≈ 5–10M rows), **the current PostgreSQL setup is perfectly adequate**. The most impactful near-term improvement is:

1. **Run the Celery task** to populate `DailyAggregate` after every CSV upload — this makes monthly/long-range chart queries fast regardless of row count.
2. **Add a nightly Parquet export** of each day's readings to `Data/parquet/` — gives a DuckDB-queryable archive that research notebooks can use without touching the web DB.
3. **Upgrade to TimescaleDB when you exceed 20 sensors** — it's a one-command migration with no code changes.
