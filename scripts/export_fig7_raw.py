"""
Export raw minute-by-minute readings for both sensors used in Figure 7,
filtered to the figure 7 date window, into a single CSV.

Outdoor sensor 81432434001 : from 2025-12-01 00:00 UTC
Indoor  sensor 81442406076 : from 2025-12-04 11:00 UTC

No aggregation — every original row is kept as-is.
Row 1 of each source CSV is the units row and is skipped.

Output: Data/fig7_raw.csv
"""
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "Data" / "Belauri"
OUT  = ROOT / "Data" / "fig7_raw.csv"

OUT_START = pd.Timestamp("2025-12-01 00:00:00", tz="UTC")
IN_START  = pd.Timestamp("2025-12-04 11:00:00", tz="UTC")

SOURCES = [
    (sorted(DATA.glob("81432434001-20*.csv")),                                   OUT_START),
    (sorted((DATA / "81442326017-81442406076-81442410021").glob("81442406076-20*.csv")), IN_START),
]

parts = []
for files, start in SOURCES:
    for f in files:
        print(f"  reading {f.name} …")
        df = pd.read_csv(f, skiprows=[1], low_memory=False)
        df["Timestamp"] = pd.to_datetime(df["Timestamp"], format="mixed", utc=True)
        df = df[df["Timestamp"] >= start]
        df.insert(0, "location", "Belauri, Nepal")
        parts.append(df)

combined = pd.concat(parts, ignore_index=True).sort_values("Timestamp").reset_index(drop=True)

combined.to_csv(OUT, index=False)
print(f"\nSaved → {OUT}")
print(f"Rows   : {len(combined):,}")
print(f"Window : {combined['Timestamp'].min()}  →  {combined['Timestamp'].max()}")
print(f"Sensors: {combined['Serial Number'].unique().tolist()}")
