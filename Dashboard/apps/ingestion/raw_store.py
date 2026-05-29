"""
RawDataStore — DuckDB-backed file storage for raw minute-level sensor readings.

Why not the Django/SQLite database?
  - SQLite takes an exclusive write lock for bulk INSERTs; a large CSV upload
    blocks every other request for minutes (the "database is locked" error).
  - DuckDB is columnar and analytical — hourly/daily aggregations run 10-100×
    faster than equivalent SQLite GROUP BY queries.
  - The file lives outside Django's database and can be backed up, zipped, or
    handed to researchers independently.

File location (set in settings.py):
    RAW_DUCKDB_PATH  — defaults to <project_root>/raw_data/readings.duckdb

Thread safety:
    DuckDB 1.x supports multiple concurrent readers and serialises writers
    automatically via file-level locking.  We add a per-instance threading.Lock
    around writes as a second layer of safety inside a single Django process.

Typical usage:
    from apps.ingestion.raw_store import raw_store   # module-level singleton

    saved, dups = raw_store.append_batch(records)
    df           = raw_store.query_range(sensor_id=1, pollutant="PM25",
                                         start="2026-01-01", end="2026-03-01")
"""
import logging
import threading
from pathlib import Path
from typing import Optional

import pandas as pd


def _utc_ts(v) -> pd.Timestamp:
    """Convert any datetime-like to a UTC-aware pd.Timestamp without the tz= conflict."""
    ts = pd.Timestamp(v)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")

logger = logging.getLogger(__name__)


def _default_path() -> Path:
    try:
        from django.conf import settings
        p = getattr(settings, "RAW_DUCKDB_PATH", None)
        if p:
            return Path(p)
        return Path(settings.BASE_DIR) / "raw_data" / "readings.duckdb"
    except Exception:
        return Path("raw_data") / "readings.duckdb"


class RawDataStore:
    """
    Manages raw minute-level sensor readings in a DuckDB file.

    Schema — long format (one row per pollutant per timestamp):
        sensor_id    INTEGER      NOT NULL
        site_id      INTEGER
        is_indoor    BOOLEAN      DEFAULT FALSE
        pollutant    VARCHAR      NOT NULL
        unit         VARCHAR      DEFAULT ''
        value        DOUBLE
        quality_flag VARCHAR      DEFAULT 'UNVALIDATED'
        source_type  VARCHAR      DEFAULT 'CSV_UPLOAD'
        ts           TIMESTAMPTZ  NOT NULL

    Uniqueness is enforced on (sensor_id, pollutant, ts).
    """

    _DDL = [
        """
        CREATE TABLE IF NOT EXISTS readings (
            sensor_id    INTEGER     NOT NULL,
            site_id      INTEGER,
            is_indoor    BOOLEAN     DEFAULT FALSE,
            pollutant    VARCHAR     NOT NULL,
            unit         VARCHAR     DEFAULT '',
            value        DOUBLE,
            quality_flag VARCHAR     DEFAULT 'UNVALIDATED',
            source_type  VARCHAR     DEFAULT 'CSV_UPLOAD',
            ts           TIMESTAMPTZ NOT NULL
        )
        """,
        # DuckDB enforces uniqueness via the index
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_readings ON readings (sensor_id, pollutant, ts)",
    ]

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else _default_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._local = threading.local()
        self._init_schema()

    def _conn(self):
        """Return a thread-local DuckDB connection, creating it if needed."""
        import duckdb
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = duckdb.connect(str(self._path))
        return self._local.conn

    def _init_schema(self):
        """Create table and index if they don't already exist."""
        try:
            with self._write_lock:
                conn = self._conn()
                for stmt in self._DDL:
                    conn.execute(stmt.strip())
        except Exception as exc:
            logger.error("RawDataStore: schema init failed: %s", exc)
            raise

    # ── Write ─────────────────────────────────────────────────────────────────

    def append_batch(self, records: list[dict]) -> tuple[int, int]:
        """
        Insert records, silently skipping duplicates (same sensor/pollutant/ts).

        Accepted record keys (either original_ts or ts, either raw_value or value):
            sensor_id, site_id, is_indoor, pollutant, unit,
            raw_value / value, quality_flag, source_type,
            original_ts / ts

        Returns:
            (saved, duplicates)
        """
        if not records:
            return 0, 0

        df = pd.DataFrame(records)

        # Normalise column names to match the DB schema
        if "raw_value" in df.columns and "value" not in df.columns:
            df = df.rename(columns={"raw_value": "value"})
        if "original_ts" in df.columns and "ts" not in df.columns:
            df = df.rename(columns={"original_ts": "ts"})

        keep = ["sensor_id", "site_id", "is_indoor", "pollutant",
                "unit", "value", "quality_flag", "source_type", "ts"]
        for col in keep:
            if col not in df.columns:
                df[col] = None
        df = df[keep].copy()

        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        df["sensor_id"] = df["sensor_id"].astype("Int64")

        with self._write_lock:
            conn = self._conn()

            # Count rows that already exist in the window we're about to insert
            ts_min = df["ts"].min()
            ts_max = df["ts"].max()
            sensor_ids = df["sensor_id"].dropna().unique().tolist()

            before = conn.execute(
                "SELECT COUNT(*) FROM readings WHERE sensor_id = ANY(?) AND ts >= ? AND ts <= ?",
                [sensor_ids, ts_min, ts_max],
            ).fetchone()[0]

            conn.register("_incoming_batch", df)
            conn.execute("""
                INSERT INTO readings
                    (sensor_id, site_id, is_indoor, pollutant,
                     unit, value, quality_flag, source_type, ts)
                SELECT
                    b.sensor_id, b.site_id, b.is_indoor, b.pollutant,
                    b.unit, b.value, b.quality_flag, b.source_type, b.ts
                FROM _incoming_batch b
                WHERE NOT EXISTS (
                    SELECT 1 FROM readings r
                    WHERE r.sensor_id = b.sensor_id
                      AND r.pollutant  = b.pollutant
                      AND r.ts         = b.ts
                )
            """)

            after = conn.execute(
                "SELECT COUNT(*) FROM readings WHERE sensor_id = ANY(?) AND ts >= ? AND ts <= ?",
                [sensor_ids, ts_min, ts_max],
            ).fetchone()[0]

        saved = after - before
        duplicates = len(df) - saved
        logger.debug("RawDataStore.append_batch: saved=%d dup=%d", saved, duplicates)
        return saved, duplicates

    # ── Read — for chart views ─────────────────────────────────────────────────

    def query_range(
        self,
        sensor_id: int,
        pollutant: str,
        start=None,
        end=None,
        quality_flags=("GOOD", "UNVALIDATED"),
        limit: int = 5_000,
    ) -> pd.DataFrame:
        """
        Return minute readings in a date range as a DataFrame with columns
        (ts, value, quality_flag), ordered by ts ascending.
        """
        where = ["sensor_id = ?", "pollutant = ?"]
        params: list = [int(sensor_id), pollutant]

        if quality_flags:
            ph = ",".join("?" * len(quality_flags))
            where.append(f"quality_flag IN ({ph})")
            params.extend(quality_flags)
        if start is not None:
            where.append("ts >= ?")
            params.append(_utc_ts(start))
        if end is not None:
            where.append("ts <= ?")
            params.append(_utc_ts(end))

        sql = (
            f"SELECT ts, value, quality_flag FROM readings"
            f" WHERE {' AND '.join(where)} ORDER BY ts LIMIT {int(limit)}"
        )
        return self._conn().execute(sql, params).df()

    # ── Read — for download / export ──────────────────────────────────────────

    def query_for_export(
        self,
        sensor_id: int,
        start=None,
        end=None,
        pollutants: Optional[list] = None,
        quality_flags: Optional[tuple] = ("GOOD", "UNVALIDATED"),
        limit: int = 500_000,
    ) -> pd.DataFrame:
        """
        Return long-format DataFrame for CSV/JSON download.
        Columns: ts, pollutant, unit, value, quality_flag, is_indoor, source_type
        """
        where = ["sensor_id = ?"]
        params: list = [int(sensor_id)]

        if pollutants:
            ph = ",".join("?" * len(pollutants))
            where.append(f"pollutant IN ({ph})")
            params.extend(pollutants)
        if quality_flags:
            ph = ",".join("?" * len(quality_flags))
            where.append(f"quality_flag IN ({ph})")
            params.extend(quality_flags)
        if start is not None:
            where.append("ts >= ?")
            params.append(_utc_ts(start))
        if end is not None:
            where.append("ts <= ?")
            params.append(_utc_ts(end))

        sql = (
            "SELECT sensor_id, ts, pollutant, unit, value, quality_flag, is_indoor, source_type"
            f" FROM readings WHERE {' AND '.join(where)}"
            f" ORDER BY ts, pollutant LIMIT {int(limit)}"
        )
        return self._conn().execute(sql, params).df()

    def query_multi_sensor_export(
        self,
        sensor_ids: list[int],
        start=None,
        end=None,
        pollutants: Optional[list] = None,
        quality_flags: Optional[tuple] = ("GOOD", "UNVALIDATED"),
        limit: int = 500_000,
    ) -> pd.DataFrame:
        """Like query_for_export but accepts a list of sensor IDs."""
        if not sensor_ids:
            return pd.DataFrame()
        ph = ",".join("?" * len(sensor_ids))
        where = [f"sensor_id IN ({ph})"]
        params: list = [int(s) for s in sensor_ids]

        if pollutants:
            ph2 = ",".join("?" * len(pollutants))
            where.append(f"pollutant IN ({ph2})")
            params.extend(pollutants)
        if quality_flags:
            ph3 = ",".join("?" * len(quality_flags))
            where.append(f"quality_flag IN ({ph3})")
            params.extend(quality_flags)
        if start is not None:
            where.append("ts >= ?")
            params.append(_utc_ts(start))
        if end is not None:
            where.append("ts <= ?")
            params.append(_utc_ts(end))

        sql = (
            "SELECT sensor_id, ts, pollutant, unit, value, quality_flag, is_indoor, source_type"
            f" FROM readings WHERE {' AND '.join(where)}"
            f" ORDER BY ts, sensor_id, pollutant LIMIT {int(limit)}"
        )
        return self._conn().execute(sql, params).df()

    def count_by_day(
        self,
        sensor_id: int,
        pollutant: str,
        start=None,
        end=None,
    ) -> pd.DataFrame:
        """Return (date, cnt) for the completeness chart."""
        where = ["sensor_id = ?", "pollutant = ?"]
        params: list = [int(sensor_id), pollutant]
        if start is not None:
            where.append("ts >= ?")
            params.append(_utc_ts(start))
        if end is not None:
            where.append("ts <= ?")
            params.append(_utc_ts(end))
        sql = (
            f"SELECT ts::DATE AS date, COUNT(*) AS cnt"
            f" FROM readings WHERE {' AND '.join(where)}"
            f" GROUP BY 1 ORDER BY 1"
        )
        return self._conn().execute(sql, params).df()

    # ── Read — for aggregation task ───────────────────────────────────────────

    def aggregate_hourly(
        self, sensor_id: int, since, until
    ) -> pd.DataFrame:
        """
        Compute per-hour, per-pollutant stats across all pollutants for one sensor.
        Called by compute_aggregates() in tasks.py.
        """
        sql = """
            SELECT
                date_trunc('hour', ts) AS hour,
                pollutant,
                is_indoor,
                COUNT(*)               AS cnt,
                AVG(value)             AS avg,
                STDDEV_SAMP(value)     AS std,
                MIN(value)             AS lo,
                MAX(value)             AS hi
            FROM readings
            WHERE sensor_id = ?
              AND quality_flag IN ('GOOD', 'UNVALIDATED')
              AND ts >= ?
              AND ts < ?
            GROUP BY 1, 2, 3
            ORDER BY 1, 2
        """
        return self._conn().execute(
            sql, [int(sensor_id), pd.Timestamp(since), pd.Timestamp(until)]
        ).df()

    def aggregate_daily(
        self, sensor_id: int, since, until
    ) -> pd.DataFrame:
        """
        Compute per-day, per-pollutant stats including percentiles.
        Called by compute_aggregates() in tasks.py.
        """
        sql = """
            SELECT
                ts::DATE                                                   AS date,
                pollutant,
                is_indoor,
                COUNT(*)                                                   AS cnt,
                AVG(value)                                                 AS avg,
                STDDEV_SAMP(value)                                         AS std,
                MIN(value)                                                 AS lo,
                MAX(value)                                                 AS hi,
                PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY value)        AS p25,
                PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY value)        AS median,
                PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY value)        AS p75,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY value)        AS p95
            FROM readings
            WHERE sensor_id = ?
              AND quality_flag IN ('GOOD', 'UNVALIDATED')
              AND ts >= ?
              AND ts < ?
            GROUP BY 1, 2, 3
            ORDER BY 1, 2
        """
        return self._conn().execute(
            sql, [int(sensor_id), pd.Timestamp(since), pd.Timestamp(until)]
        ).df()

    def earliest_ts(self, sensor_id: int):
        """Return the earliest timestamp for a sensor, or None."""
        row = self._conn().execute(
            "SELECT MIN(ts) FROM readings WHERE sensor_id = ?", [int(sensor_id)]
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    def has_data(self, sensor_id: int) -> bool:
        return self._conn().execute(
            "SELECT COUNT(*) FROM readings WHERE sensor_id = ? LIMIT 1", [int(sensor_id)]
        ).fetchone()[0] > 0

    def close(self):
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None


# Module-level singleton — import this in views, tasks, and converters
raw_store = RawDataStore()
