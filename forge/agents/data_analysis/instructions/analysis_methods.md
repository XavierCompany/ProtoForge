# Analysis Methods Reference

## Statistical Methods
| Method | Use Case | Output |
|--------|---------|--------|
| Descriptive stats | Summarize a dataset | count, mean, median, std, min, max |
| Percentiles | Latency / performance | p50, p75, p90, p95, p99 |
| Moving average | Smooth noisy time series | Trend line |
| Z-score | Outlier detection | Points with |z| > 3 flagged |
| Regression | Trend estimation | Slope + R² |
| Seasonal decomposition | Recurring patterns | Trend + Seasonal + Residual |

## Anomaly Detection
1. **Static thresholds** — compare against hardcoded limits
2. **Dynamic thresholds** — mean ± 2σ over a rolling window
3. **Isolation Forest** — for multivariate anomalies
4. **Change-point detection** — identify regime shifts

## Visualization Types
| Chart | Best For |
|-------|---------|
| Line | Time series, trends |
| Bar | Comparisons across categories |
| Histogram | Distribution shape |
| Heatmap | Correlation matrix, time × category |
| Scatter | Relationship between two variables |

## Data Handling Rules
- **Missing values:** Report % missing; impute only with disclosure
- **Large datasets:** Use sampling (min 1000 rows) or aggregation
- **Mixed types:** Coerce or separate; never silently drop columns
- **Time zones:** Normalize to UTC; report original TZ
