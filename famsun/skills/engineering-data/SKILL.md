---
name: engineering-data
description: Use for tabular process data, minimum-cost ingredient blending, nutrient constraints, mass balances, production-line measurements, trend plots and quality reports.
---

Preserve source files. Record the sample interval, units, time zone and measurement definitions. Identify missing or duplicated rows before aggregating. Keep reference data clearly labeled when the user requests a generated example. For rates, compute totals using interval durations and explain the endpoint convention. Calculate validation checks from independently observable totals.

Export the requested report, table, JSON or chart to /workspace. Follow any supplied filenames, units and schema. Reopen the exported tables and check that row counts, finite values, group totals and labels match the reported results. State uncertainty or data limitations in the final answer.

## Minimum-cost blending

Use the bundled linear-programming helper for a fixed total weight with ingredient bounds, per-weight costs and minimum or maximum weighted properties. Write a JSON manifest in the workspace. Set `total_weight` in the user's weight unit and `cost_basis_weight` to the number of those weight units covered by each quoted ingredient price. Give each entry of `ingredients` a unique `name`, a numeric `unit_cost` per cost basis, and optional `min_weight` and `max_weight` in the original weight unit. By default each ingredient may range from zero to the total batch weight. Each entry of `nutrients` has a unique `name`, at least one of `min` or `max` (both inclusive), and a `coefficients` object mapping every ingredient name to its concentration in the same units as the bounds. Give explicit zeros where appropriate. Percentages may be entered directly as percentages. For a property specified as an absolute weighted total, divide its limits by batch weight to express them as weighted means.

```bash
python /opt/famsun/skills/engineering-data/scripts/blend.py /workspace/ingredients.json /workspace/blend-result.json
```

Read the result once. It contains `weights` by ingredient name, `total_weight`, `total_cost`, `cost_per_basis_weight`, the independently calculated `weight_residual`, and original-unit `weight_slacks`. Each `nutrients[name]` reports `weighted_mean`, `weighted_total` (weight × coefficient), `lower_slack` and `upper_slack`. A near-zero slack identifies a binding limit; null identifies an omitted bound. Where a requested export has custom field names, map these values into that schema in one short script and reopen the exported file. Once its schema, units and requested checks are verified, return the answer.
