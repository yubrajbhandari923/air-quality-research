"""
Export the exact data underlying Figure 7 (24-h rolling average PM2.5)
from belauri_indoor_outdoor.ipynb to a single CSV.

Replicates cell 53 of the notebook exactly:
  - Outdoor sensor 81432434001  from 2025-12-01 00:00 UTC
  - Indoor sensor  81442406076  from 2025-12-04 11:00 UTC
  - PM2.5 capped at 500 µg/m³ (suspect readings → NaN)
  - Hourly median resampled
  - Short dropouts ≤ 12 h linearly interpolated (honest longer gaps kept as NaN)
  - 24-h rolling mean (window=24, min_periods=18)

Output columns:
  timestamp_utc            — hourly index, UTC-aware ISO 8601
  pm25_outdoor_hourly      — hourly median before rolling (µg/m³)
  pm25_indoor_hourly       — hourly median before rolling (µg/m³)
  pm25_outdoor_rolling_24h — 24-h rolling mean plotted in fig7 (µg/m³)
  pm25_indoor_rolling_24h  — 24-h rolling mean plotted in fig7 (µg/m³)
  who_24h_guideline        — 15 µg/m³ (constant reference line)

Usage:
    python scripts/export_fig7_data.py
"""
from pathlib import Path

import pandas as pd

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT    = Path(__file__).resolve().parent.parent
DATA    = ROOT / "Data" / "Belauri"
OUT_CSV = ROOT / "Data" / "fig7_24h_rolling_avg.csv"

OUT_FILES = sorted(DATA.glob("81432434001-20*.csv"))
IN_FILES  = sorted((DATA / "81442326017-81442406076-81442410021").glob("81442406076-20*.csv"))

# ── Constants (match notebook cell 1 / cell 53) ───────────────────────────────
PM25_OUTLIER_CAP = 500   # µg/m³
WHO_24H          = 15    # µg/m³
OUT_START = pd.Timestamp("2025-12-01 00:00:00", tz="UTC")
IN_START  = pd.Timestamp("2025-12-04 11:00:00", tz="UTC")

# ── Loader (matches notebook cell 3) ─────────────────────────────────────────
KEEP = {
    "Timestamp":   "ts",
    "Serial Number": "serial",
    "PM2.5":       "pm25",
}

def load_sensor(paths: list[Path]) -> pd.DataFrame:
    parts = []
    for p in paths:
        df = pd.read_csv(p, skiprows=[1], low_memory=False)
        df["Timestamp"] = pd.to_datetime(df["Timestamp"], format="mixed", utc=True)
        df = df.rename(columns={c: KEEP[c] for c in df.columns if c in KEEP})
        cols = [v for v in KEEP.values() if v in df.columns]
        parts.append(df[cols])
    out = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["ts"])
    out = out.sort_values("ts").reset_index(drop=True)
    out["pm25"] = pd.to_numeric(out["pm25"], errors="coerce")
    return out

# ── Load raw data ─────────────────────────────────────────────────────────────
print(f"Loading outdoor sensor from {len(OUT_FILES)} file(s)…")
out_raw = load_sensor(OUT_FILES)

print(f"Loading indoor sensor from {len(IN_FILES)} file(s)…")
in_raw = load_sensor(IN_FILES)

# ── Cap outliers (matches notebook cell 3) ────────────────────────────────────
out_raw["pm25_clean"] = out_raw["pm25"].where(out_raw["pm25"] <= PM25_OUTLIER_CAP)
in_raw["pm25_clean"]  = in_raw["pm25"].where(in_raw["pm25"]  <= PM25_OUTLIER_CAP)

# ── Filter to figure 7 start dates (matches notebook cell 53) ─────────────────
out_fig = out_raw[out_raw["ts"] >= OUT_START].copy()
in_fig  = in_raw[in_raw["ts"]   >= IN_START].copy()

# ── Hourly median (matches cell 53) ───────────────────────────────────────────
pm_out_h = out_fig.set_index("ts")["pm25_clean"].resample("1h").median()
pm_in_h  = in_fig.set_index("ts")["pm25_clean"].resample("1h").median()

# ── Interpolate short dropouts ≤ 12 h (matches cell 53) ──────────────────────
pm_out_h_interp = pm_out_h.interpolate(method="time", limit=12)
pm_in_h_interp  = pm_in_h.interpolate(method="time", limit=12)

# ── 24-h rolling mean (matches cell 53: window=24, min_periods=18) ────────────
roll_out = pm_out_h_interp.rolling(window=24, min_periods=18).mean()
roll_in  = pm_in_h_interp.rolling(window=24, min_periods=18).mean()

# ── Combine on a shared hourly index ─────────────────────────────────────────
idx = pm_out_h.index.union(pm_in_h.index)

df_out = pd.DataFrame({
    "pm25_outdoor_hourly":      pm_out_h.reindex(idx),
    "pm25_outdoor_rolling_24h": roll_out.reindex(idx),
}, index=idx)

df_in = pd.DataFrame({
    "pm25_indoor_hourly":      pm_in_h.reindex(idx),
    "pm25_indoor_rolling_24h": roll_in.reindex(idx),
}, index=idx)

result = df_out.join(df_in)
result.index.name = "timestamp_utc"
result["who_24h_guideline"] = WHO_24H

# Round to 2 dp — matches display precision in the notebook
result = result.round(2)

# ── Save ──────────────────────────────────────────────────────────────────────
OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
result.to_csv(OUT_CSV)

print(f"\nSaved → {OUT_CSV}")
print(f"Rows  : {len(result):,}  ({result.index[0]}  →  {result.index[-1]})")
print(f"Columns: {list(result.columns)}")
print(f"\nOutdoor rolling NaN hours (large outages kept as breaks): {roll_out.isna().sum()}")
print(f"Indoor  rolling NaN hours: {roll_in.isna().sum()}")
print(f"\nSample (first 3 rows):")
print(result.head(3).to_string())
