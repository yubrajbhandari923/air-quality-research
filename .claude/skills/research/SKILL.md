# Air Quality Research Project Guidelines

## Research Principles

- Preserve scientific reproducibility
- Never overwrite raw datasets
- Document all assumptions
- Prefer transparent models over black-box approaches
- All visualizations must include units and timestamps

## Coding Principles

- Use small modular functions
- Prefer pandas/duckdb over custom loops
- Use geopandas for vector GIS operations
- Avoid CRS mismatches
- Always validate datetime parsing

## Visualization Standards

- Use perceptually uniform colormaps
- Label PM2.5, PM10, NO2 units explicitly
- Include coordinate reference systems on maps
- Add uncertainty notes when relevant

## Data Validation

- Detect outliers
- Check sensor drift
- Flag missing timestamps
- Log cleaning operations

## Deliverables

- publication-quality figures
- reproducible notebooks
- exportable GIS layers