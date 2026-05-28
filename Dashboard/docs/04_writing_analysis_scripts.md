# Writing Custom Analysis Scripts

## Overview

The **Analysis Scripts** system lets admins and maintainers write Python functions that query the live database and render results as interactive charts, tables, or metrics on the Analysis page — without editing templates or deploying code.

Scripts are stored in the database and executed server-side. Results are cached in JSON and displayed automatically.

---

## Accessing the script editor

Go to: `/django-admin/analysis/analysisscript/add/`

Or: Django admin → Analysis → Analysis Scripts → Add.

---

## The `run(db)` contract

Every script must define exactly one function named `run` that accepts a single `db` argument:

```python
def run(db):
    # db gives access to Django models:
    #   db.CanonicalReading
    #   db.Sensor
    #   db.Site
    
    # ... your analysis ...
    
    return {
        "title": "My analysis",
        "labels": [...],        # x-axis labels or column names
        "datasets": [...],      # Chart.js dataset dicts
        "summary": "...",       # optional text displayed below chart
    }
```

The function must return a dict. The structure depends on the `chart_type` selected.

---

## Return dict by chart type

### TIMESERIES / BAR / SCATTER

```python
return {
    "title": "PM₂.₅ — last 30 days",
    "labels": ["2026-01-01", "2026-01-02", ...],   # x-axis
    "datasets": [
        {
            "label": "Outdoor sensor",
            "data": [42.3, 38.1, ...],
            "borderColor": "#2563eb",               # optional; auto-assigned if omitted
        },
        {
            "label": "Indoor sensor",
            "data": [58.7, 61.2, ...],
            "borderColor": "#dc2626",
        },
    ],
    "summary": "24-hour median PM₂.₅. WHO guideline: 15 µg/m³.",
}
```

### TABLE

```python
return {
    "title": "Monthly averages",
    "columns": ["Month", "Sensor", "PM₂.₅ avg (µg/m³)", "Max (µg/m³)"],
    "table": [
        ["2025-12", "Outdoor", 52.4, 184.2],
        ["2026-01", "Outdoor", 61.1, 210.3],
        ...
    ],
    "summary": "Monthly statistics for all active sensors.",
}
```

### METRIC

```python
return {
    "title": "Overall median I/O ratio",
    "value": 1.42,
    "unit": "",
    "summary": "Median indoor/outdoor PM₂.₅ ratio over the full overlap period. Values > 1 indicate indoor air is worse.",
}
```

---

## Run modes

| Mode | Behaviour |
|------|-----------|
| **CONTINUOUS** | Auto-re-runs every `interval_minutes`. Refreshed when the analysis page loads (if stale). Good for live dashboards. |
| **CACHED** | Runs once (manually, or on first load). Result cached until you click "Run now". Good for long computations or research outputs. |

---

## Available imports

Your code runs in a restricted namespace. Bring in anything you need:

```python
def run(db):
    from django.utils import timezone
    from datetime import timedelta
    from django.db.models import Avg, Count, Max, Min
    from django.db.models.functions import TruncDate, ExtractHour, ExtractMonth
    import statistics
    
    ...
```

The standard library and all installed packages are available. You can import numpy, pandas, scipy etc. if they are installed in the virtual environment.

---

## Example: Diurnal PM₂.₅ profile

```python
def run(db):
    from django.db.models import Avg
    from django.db.models.functions import ExtractHour
    from django.utils import timezone
    from datetime import timedelta

    NST_OFFSET = 5 * 60 + 45  # minutes — Nepal Standard Time = UTC+5:45

    cutoff = timezone.now() - timedelta(days=90)
    sensors = db.Sensor.objects.filter(status="ACTIVE")

    datasets = []
    colors = ["#2563eb", "#dc2626", "#16a34a", "#d97706"]
    labels = list(range(24))  # hours 0-23 NST

    for i, sensor in enumerate(sensors):
        # Group by NST hour — shift UTC by 5h45m = 345 minutes
        rows = (
            db.CanonicalReading.objects
            .filter(sensor=sensor, pollutant="PM25", is_duplicate=False,
                    quality_flag__in=["GOOD", "UNVALIDATED"],
                    original_ts__gte=cutoff)
            .extra(select={"nst_hour": "CAST((strftime('%H', original_ts) * 60 + strftime('%M', original_ts) + 345) / 60 %% 24 AS INTEGER)"})
            .values("nst_hour")
            .annotate(avg=Avg("raw_value"))
            .order_by("nst_hour")
        )
        by_hour = {r["nst_hour"]: round(r["avg"] or 0, 1) for r in rows}
        datasets.append({
            "label": sensor.friendly_name,
            "data": [by_hour.get(h, None) for h in labels],
            "borderColor": colors[i % len(colors)],
        })

    return {
        "title": "Diurnal PM₂.₅ profile (NST)",
        "labels": [f"{h:02d}:00" for h in labels],
        "datasets": datasets,
        "summary": "Hour-of-day median PM₂.₅ in Nepal Standard Time (UTC+5:45). Peaks typically correspond to morning and evening cooking.",
    }
```

---

## Example: Monthly comparison table

```python
def run(db):
    from django.db.models import Avg, Max
    from django.db.models.functions import TruncMonth

    rows = (
        db.CanonicalReading.objects
        .filter(pollutant="PM25", is_duplicate=False, quality_flag="GOOD")
        .annotate(month=TruncMonth("original_ts"))
        .values("month", "sensor__friendly_name")
        .annotate(mean=Avg("raw_value"), maximum=Max("raw_value"))
        .order_by("month", "sensor__friendly_name")
    )
    return {
        "title": "Monthly PM₂.₅ statistics",
        "columns": ["Month", "Sensor", "Mean (µg/m³)", "Max (µg/m³)"],
        "table": [
            [str(r["month"])[:7], r["sensor__friendly_name"],
             round(r["mean"] or 0, 1), round(r["maximum"] or 0, 1)]
            for r in rows
        ],
    }
```

---

## Running and debugging

1. In Django admin, click **Save** on a script, then the **Run now** link (in the Result section) appears.
2. On the Analysis page, maintainers/admins see a **↺ Run now** button per script.
3. If a script errors, the full traceback is saved to `last_error` and displayed in red on the analysis page.
4. Check the `run_duration_ms` field — queries over ~5 seconds may need optimization.

---

## Performance tips

- Always filter on `is_duplicate=False` — duplicates roughly double row count.
- Add `quality_flag__in=["GOOD", "UNVALIDATED"]` to exclude junk.
- Aggregate in the database (`.annotate(avg=Avg(...)).values(...)`) rather than pulling raw rows into Python.
- For CONTINUOUS scripts, use narrow time windows (last 30 days, not all-time).
- If you need all-time statistics, use CACHED mode and run it once; it won't re-run until you click "Run now".
- The `DailyAggregate` table (pre-computed per-day stats) is much faster for long-range queries than reading `CanonicalReading`.

```python
# Fast: use pre-computed daily aggregates
from apps.readings.models import DailyAggregate
rows = DailyAggregate.objects.filter(
    sensor=my_sensor, pollutant="PM25"
).values("date", "mean", "median", "p25", "p75")

# Slow for long ranges: direct scan of CanonicalReading
rows = CanonicalReading.objects.filter(sensor=my_sensor, pollutant="PM25").all()
```
