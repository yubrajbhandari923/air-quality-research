"""
Export raw minute-by-minute readings for both sensors used in Figure 7,
filtered to the figure 7 date window, into two separate CSVs.

Outdoor sensor 81432434001 : from 2025-12-01 00:00 UTC
Indoor  sensor 81442406076 : from 2025-12-04 11:00 UTC

No aggregation — every original row is kept as-is, in the original H1/H2
column format (no extra columns added).
Row 1 of each source CSV is the units row and is skipped.

Outputs:
    Data/fig7_outdoor.csv
    Data/fig7_indoor.csv
"""
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "Data" / "Belauri"

OUT_START = pd.Timestamp("2025-12-01 00:00:00", tz="UTC")
IN_START  = pd.Timestamp("2025-12-04 11:00:00", tz="UTC")

SOURCES = [
    (
        sorted(DATA.glob("81432434001-20*.csv")),
        OUT_START,
        ROOT / "Data" / "fig7_outdoor.csv",
        "Outdoor 81432434001",
    ),
    (
        sorted((DATA / "81442326017-81442406076-81442410021").glob("81442406076-20*.csv")),
        IN_START,
        ROOT / "Data" / "fig7_indoor.csv",
        "Indoor 81442406076",
    ),
]

for files, start, out_path, label in SOURCES:
    print(f"\n{label}")
    parts = []
    for f in files:
        print(f"  reading {f.name} …")
        df = pd.read_csv(f, skiprows=[1], low_memory=False)
        ts = pd.to_datetime(df["Timestamp"], format="mixed", utc=True)
        df = df[ts >= start]
        parts.append(df)

    combined = pd.concat(parts, ignore_index=True).sort_values("Timestamp").reset_index(drop=True)
    combined.to_csv(out_path, index=False)
    print(f"  Saved → {out_path}")
    print(f"  Rows  : {len(combined):,}")
    print(f"  Window: {combined['Timestamp'].min()}  →  {combined['Timestamp'].max()}")
