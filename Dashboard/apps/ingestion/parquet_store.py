"""
R2 Parquet Store — wide-format time-series storage for sensor readings.

Layout in R2
────────────
  raw/<serial>/<YYYY-MM-DD>_<original_filename>.csv       uploaded CSV backup
  processed/<serial>/<YYYY>/<MM>/readings.parquet         wide, deduplicated readings

Each parquet file covers one calendar month for one sensor.

Wide-format schema (one row per timestamp)
──────────────────────────────────────────
  ts_utc       datetime64[us, UTC]   — sample timestamp
  is_indoor    bool
  source_type  string                — e.g. "CSV_UPLOAD"
  <POLLUTANT>  float32               — one column per pollutant present in the data
  qf_<POLL>    string                — quality flag for that pollutant (GOOD/SUSPECT/BAD/MISSING/UNVALIDATED)

Why wide format?
  Columnar parquet is most efficient when each column is homogeneous.  A wide
  layout lets tools like DuckDB, pandas, and R/Arrow read only the columns they
  need without touching the others.  The file size for one month of 1-minute
  data from one sensor is typically 1–4 MB (vs ~30 MB CSV, ~200 MB Postgres).

Public API
──────────
  long_to_wide(records)              convert list[dict] long records → wide DataFrame
  upsert(serial, df_wide)            merge new rows into R2; deduplicate on ts_utc
  read(serial, start_dt, end_dt)     return wide DataFrame for a date range
  list_partitions(serial)            list (year, month) tuples available in R2
  upload_raw_csv(serial, path, name) save original CSV under raw/<serial>/
"""

import io
import logging
from datetime import datetime, timezone as dt_tz
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# All canonical pollutant codes — determines column ordering in parquet files.
POLLUTANTS = [
    "PM1", "PM25", "PM4", "PM10",
    "NC05", "NC1", "NC25", "NC4", "NC10",
    "CO2", "CO", "SO2", "O3", "NO2", "CH2O", "TVOC",
    "BARO", "TEMP", "RH",
]


# ── R2 key helpers ─────────────────────────────────────────────────────────────

def _processed_key(serial: str, year: int, month: int) -> str:
    return f"processed/{serial}/{year:04d}/{month:02d}/readings.parquet"


def _raw_key(serial: str, date_str: str, filename: str) -> str:
    safe = "".join(c for c in filename[-80:] if c.isalnum() or c in "._-")
    return f"raw/{serial}/{date_str}_{safe or 'upload.csv'}"


# ── R2 availability check ──────────────────────────────────────────────────────

def _r2_available() -> bool:
    from django.conf import settings
    return bool(
        getattr(settings, "R2_ACCOUNT_ID", "")
        and getattr(settings, "R2_ACCESS_KEY_ID", "")
    )


# ── Low-level R2 operations ────────────────────────────────────────────────────

def _client():
    from apps.ingestion.r2 import _client as r2_client
    return r2_client()


def _bucket() -> str:
    from apps.ingestion.r2 import _bucket as r2_bucket
    return r2_bucket()


def _download_parquet(key: str) -> pd.DataFrame | None:
    """Return DataFrame if key exists, None if the object does not exist."""
    import pyarrow.parquet as pq
    from botocore.exceptions import ClientError

    buf = io.BytesIO()
    try:
        _client().download_fileobj(_bucket(), key, buf)
        buf.seek(0)
        return pq.read_table(buf).to_pandas()
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NoSuchBucket"):
            return None
        logger.warning("R2 download failed for %s: %s", key, exc)
        return None
    except Exception as exc:
        logger.warning("Parquet read failed for %s: %s", key, exc)
        return None


def _upload_parquet(key: str, df: pd.DataFrame) -> None:
    """Serialize df as snappy-compressed parquet and upload to R2."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    # pq.write_table closes a plain io.BytesIO in PyArrow >= 15.
    # Use pa.BufferOutputStream which stays open after the write.
    sink = pa.BufferOutputStream()
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, sink, compression="snappy")
    data = sink.getvalue().to_pybytes()
    _client().upload_fileobj(
        io.BytesIO(data), _bucket(), key,
        ExtraArgs={"ContentType": "application/octet-stream"},
    )
    logger.info("R2 parquet write: %s (%d rows, %.1f KB)", key, len(df), len(data) / 1024)


# ── Format conversion ──────────────────────────────────────────────────────────

def long_to_wide(records: list[dict]) -> pd.DataFrame:
    """
    Convert long-format ingestion records (one row per ts × pollutant) to a
    wide DataFrame (one row per ts, one column per pollutant).

    Input record keys (minimum required):
        original_ts   — tz-aware datetime
        pollutant     — canonical pollutant code
        raw_value     — float or None
        quality_flag  — string
        is_indoor     — bool (defaults to False if missing/None)
        source_type   — string

    Output columns:
        ts_utc, is_indoor, source_type
        <POLLUTANT>    (float32, NaN when not measured)
        qf_<POLLUTANT> (string)
    """
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame()

    df["ts_utc"] = pd.to_datetime(df["original_ts"], utc=True).dt.floor("s")
    # is_indoor may be None/NaN (e.g. API records before the FK is attached)
    df["is_indoor"] = df["is_indoor"].fillna(False).astype(bool)

    # Pivot raw values on ts_utc only — source_type must NOT be in the index.
    # Including source_type would create two rows for the same timestamp when
    # one came via API (LIVE_API) and another via CSV (CSV_UPLOAD), breaking
    # deduplication.  Take the first non-null value per (ts, pollutant).
    val_pivot = df.pivot_table(
        index="ts_utc",
        columns="pollutant",
        values="raw_value",
        aggfunc="first",
    ).reset_index()
    val_pivot.columns.name = None

    # Carry is_indoor and source_type from the first record at each timestamp
    meta = (
        df.groupby("ts_utc", sort=False)[["is_indoor", "source_type"]]
        .first()
        .reset_index()
    )

    # Pivot quality flags on ts_utc only
    qf_pivot = df.pivot_table(
        index="ts_utc",
        columns="pollutant",
        values="quality_flag",
        aggfunc="first",
    ).reset_index()
    qf_pivot.columns = ["ts_utc"] + [f"qf_{c}" for c in qf_pivot.columns[1:]]
    qf_pivot.columns.name = None

    wide = val_pivot.merge(meta, on="ts_utc", how="left").merge(qf_pivot, on="ts_utc", how="left")

    # Cast pollutant columns to float32 (halves storage vs float64)
    for col in POLLUTANTS:
        if col in wide.columns:
            wide[col] = wide[col].astype("float32")

    return wide


def wide_to_long(df_wide: pd.DataFrame) -> pd.DataFrame:
    """
    Convert a wide DataFrame back to long format for aggregation or DB writes.

    Output columns: ts_utc, is_indoor, source_type, pollutant, raw_value, quality_flag
    """
    if df_wide.empty:
        return pd.DataFrame(columns=["ts_utc", "is_indoor", "source_type",
                                      "pollutant", "raw_value", "quality_flag"])

    id_cols = ["ts_utc", "is_indoor", "source_type"]
    poll_cols = [c for c in POLLUTANTS if c in df_wide.columns]

    # Melt raw values
    long_val = df_wide[id_cols + poll_cols].melt(
        id_vars=id_cols,
        value_vars=poll_cols,
        var_name="pollutant",
        value_name="raw_value",
    )

    # Melt quality flags
    qf_cols = [f"qf_{p}" for p in poll_cols if f"qf_{p}" in df_wide.columns]
    if qf_cols:
        long_qf = df_wide[["ts_utc"] + qf_cols].melt(
            id_vars=["ts_utc"],
            value_vars=qf_cols,
            var_name="_qf_col",
            value_name="quality_flag",
        )
        long_qf["pollutant"] = long_qf["_qf_col"].str.removeprefix("qf_")
        long_qf = long_qf.drop(columns=["_qf_col"])
        long_val = long_val.merge(long_qf, on=["ts_utc", "pollutant"], how="left")
    else:
        long_val["quality_flag"] = "UNVALIDATED"

    return long_val.reset_index(drop=True)


# ── Public API ─────────────────────────────────────────────────────────────────

def upsert(serial: str, df_wide: pd.DataFrame) -> dict:
    """
    Merge df_wide into R2 parquet partitions (one file per sensor per month).

    Deduplication: newer rows win on identical ts_utc within a partition.

    Returns:
        {
            "partitions_updated": int,
            "rows_new":       int,   # net new rows added to parquet
            "rows_duplicate": int,   # rows already present (overwritten with latest values)
        }
    """
    if not _r2_available():
        logger.info("R2 not configured — parquet upsert skipped for %s", serial)
        return {"partitions_updated": 0, "rows_new": 0, "rows_duplicate": 0}

    df_wide = df_wide.copy()
    df_wide["ts_utc"] = pd.to_datetime(df_wide["ts_utc"], utc=True)
    df_wide["_year"]  = df_wide["ts_utc"].dt.year
    df_wide["_month"] = df_wide["ts_utc"].dt.month

    partitions_updated = rows_new = rows_duplicate = 0

    for (year, month), group in df_wide.groupby(["_year", "_month"]):
        key     = _processed_key(serial, int(year), int(month))
        new_rows = group.drop(columns=["_year", "_month"]).reset_index(drop=True)

        existing = _download_parquet(key)
        if existing is not None:
            existing["ts_utc"] = pd.to_datetime(existing["ts_utc"], utc=True)
            before_count = len(existing)

            # Align columns — parquet may not have all pollutant columns if
            # earlier uploads didn't include certain sensors/pollutants.
            all_cols = list(dict.fromkeys(list(existing.columns) + list(new_rows.columns)))
            existing  = existing.reindex(columns=all_cols)
            new_rows  = new_rows.reindex(columns=all_cols)

            # Column-wise merge: for rows at the same ts_utc, take the last
            # non-NaN value per column.  Sorting existing first / new last means
            # new values win where present; existing values fill in where new is NaN.
            # groupby.last(skipna=True) is a C-level op — no Python call per group.
            combined = pd.concat([existing, new_rows], ignore_index=True)
            combined = combined.sort_values(["ts_utc", "source_type"], na_position="first")
            try:
                merged = (
                    combined
                    .groupby("ts_utc", sort=True)
                    .last(skipna=True)
                    .reset_index()
                )
            except TypeError:
                # pandas < 2.2 — last() skips NaN by default (no skipna param)
                merged = (
                    combined
                    .groupby("ts_utc", sort=True)
                    .last()
                    .reset_index()
                )
            merged = merged.sort_values("ts_utc").reset_index(drop=True)

            rows_new       += len(merged) - before_count
            rows_duplicate += len(new_rows) - max(0, len(merged) - before_count)
        else:
            merged         = new_rows.sort_values("ts_utc").reset_index(drop=True)
            rows_new       += len(merged)

        _upload_parquet(key, merged)
        partitions_updated += 1

    return {
        "partitions_updated": partitions_updated,
        "rows_new":       rows_new,
        "rows_duplicate": rows_duplicate,
    }


def read(serial: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    """
    Download and concatenate all parquet partitions covering [start_dt, end_dt].

    Returns a wide DataFrame (possibly empty if no data or R2 not configured).
    """
    if not _r2_available():
        return pd.DataFrame()

    start = pd.Timestamp(start_dt).tz_convert("UTC") if hasattr(start_dt, "tzinfo") and start_dt.tzinfo else pd.Timestamp(start_dt, tz="UTC")
    end   = pd.Timestamp(end_dt).tz_convert("UTC")   if hasattr(end_dt,   "tzinfo") and end_dt.tzinfo   else pd.Timestamp(end_dt,   tz="UTC")

    parts = []
    period     = start.to_period("M")
    end_period = end.to_period("M")

    while period <= end_period:
        key = _processed_key(serial, period.year, period.month)
        df  = _download_parquet(key)
        if df is not None:
            df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
            parts.append(df)
        period += 1

    if not parts:
        return pd.DataFrame()

    combined = pd.concat(parts, ignore_index=True)
    mask = (combined["ts_utc"] >= start) & (combined["ts_utc"] <= end)
    return combined[mask].sort_values("ts_utc").reset_index(drop=True)


def list_partitions(serial: str) -> list[tuple[int, int]]:
    """
    List available (year, month) tuples for a sensor in R2.

    Returns an empty list if R2 is not configured or the sensor has no data.
    """
    if not _r2_available():
        return []

    from botocore.exceptions import ClientError

    prefix    = f"processed/{serial}/"
    paginator = _client().get_paginator("list_objects_v2")
    result    = []

    try:
        for page in paginator.paginate(Bucket=_bucket(), Prefix=prefix):
            for obj in page.get("Contents", []):
                # key: processed/<serial>/<YYYY>/<MM>/readings.parquet
                parts = obj["Key"].split("/")
                if len(parts) >= 5 and parts[-1] == "readings.parquet":
                    try:
                        result.append((int(parts[-3]), int(parts[-2])))
                    except ValueError:
                        pass
    except ClientError as exc:
        logger.warning("R2 list_partitions failed for %s: %s", serial, exc)

    return sorted(set(result))


def upload_raw_csv(serial: str, local_path: Path, original_name: str) -> str:
    """
    Upload a raw CSV file to R2 under raw/<serial>/<date>_<name>.csv.

    Returns the R2 key, or "" if R2 is not configured.
    """
    if not _r2_available():
        return ""

    date_str = datetime.now(dt_tz.utc).strftime("%Y-%m-%d")
    key      = _raw_key(serial, date_str, original_name)

    from apps.ingestion.r2 import upload_path
    upload_path(local_path, key, content_type="text/csv")
    return key
