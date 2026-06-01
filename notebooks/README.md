# notebooks

Exploratory analysis on the research data warehouse. Read-only.

## Launch

```bash
uv sync --group research
uv run jupyter lab notebooks/
```

JupyterLab opens at `http://localhost:8888`. Each notebook opens
`research.db` (path resolved from `$PREDMARKBOT_RESEARCH_DB` or the default
`~/.local/share/predmarkbot/research.db`) **read-only**.

## What's here

| Notebook | Purpose |
|---|---|
| `01_favorite_longshot_explore.ipynb` | Custom buckets, filters, alternate test stats |
| `02_category_drilldown.ipynb` | Per-category time series + comparisons |
| `03_market_inspector.ipynb` | Pick a single ticker; plot its candlestick history + horizon-price markers |
| `04_stratified_explore.ipynb` | Per-series heatmaps + top stratified cells from `strat_bucket_stats` |

## Output stripping

A `nbstripout` pre-commit hook strips notebook outputs before commit
(installed by `uv sync --group research`). Run it once to register:

```bash
uv run nbstripout --install
```

`.ipynb_checkpoints/` is gitignored.
