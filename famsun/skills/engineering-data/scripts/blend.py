"""Find the minimum cost of a fixed-weight blend under weighted property limits."""

import argparse
import json
import math
import warnings
from pathlib import Path
from tempfile import NamedTemporaryFile

from scipy.optimize import OptimizeWarning, linprog


def number(value, name):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def named_rows(rows, label):
    if not isinstance(rows, list):
        raise TypeError(f"{label} must be a list")
    result = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("name"), str)
            or not row["name"].strip()
        ):
            raise ValueError(f"each {label} entry needs a name")
        name = row["name"]
        if name in result:
            raise ValueError(f"duplicate {label} name: {name}")
        result[name] = row
    return result


def interval(row, low_field, high_field, *, low=None, high=None):
    low = row.get(low_field, low)
    high = row.get(high_field, high)
    low = number(low, low_field) if low is not None else None
    high = number(high, high_field) if high is not None else None
    if low is None and high is None:
        raise ValueError(f"{row['name']} needs at least one bound")
    if low is not None and high is not None and low > high:
        raise ValueError(f"inverted bounds for {row['name']}")
    return low, high


def slacks(value, low, high):
    return {
        "lower_slack": None if low is None else value - low,
        "upper_slack": None if high is None else high - value,
    }


def verify_slacks(result, tolerance):
    for distance in result.values():
        if distance is not None and distance < -tolerance:
            raise ValueError(f"solver candidate violates a bound by {-distance:g}")


def solve(spec):
    if not isinstance(spec, dict):
        raise TypeError("expected a blend specification object")
    total = number(spec["total_weight"], "total_weight")
    basis = number(spec["cost_basis_weight"], "cost_basis_weight")
    if total <= 0 or basis <= 0:
        raise ValueError("total_weight and cost_basis_weight must be positive")
    ingredients = named_rows(spec["ingredients"], "ingredients")
    if not ingredients:
        raise ValueError("at least one ingredient is required")
    nutrients = named_rows(spec.get("nutrients", []), "nutrients")
    cost, weight_bounds = [], []
    for row in ingredients.values():
        cost.append(number(row["unit_cost"], "unit_cost"))
        low, high = interval(row, "min_weight", "max_weight", low=0, high=total)
        if low is None or low < 0 or (high is not None and high < 0):
            raise ValueError("weight bounds must be nonnegative")
        weight_bounds.append((low, high))
    property_rows, matrix, rhs = {}, [], []
    for name, row in nutrients.items():
        low, high = interval(row, "min", "max")
        values = row["coefficients"]
        if not isinstance(values, dict) or set(values) != set(ingredients):
            raise ValueError(
                f"{name}: coefficients must name each ingredient exactly once"
            )
        coeffs = [number(values[key], name) for key in ingredients]
        scale = max(abs(value) for value in [*coeffs, low or 0, high or 0]) or 1
        property_rows[name] = coeffs, low, high, scale
        for bound, sign in ((low, -1), (high, 1)):
            if bound is not None:
                matrix.append([sign * c / scale for c in coeffs])
                rhs.append(sign * bound / scale)

    # Solve for mass fractions. HiGHS receives normalized objective/property
    # rows so the same formulation works in mg, kg or tonnes. Restrict its
    # native worker count for the sandbox's PID and address-space limits.
    cost_scale = max(map(abs, cost)) or 1
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Unrecognized options detected.*threads",
            category=OptimizeWarning,
        )
        opt = linprog(
            [c / cost_scale for c in cost],
            A_ub=matrix or None,
            b_ub=rhs or None,
            A_eq=[[1.0] * len(cost)],
            b_eq=[1.0],
            method="highs",
            bounds=[
                (lo / total, hi / total if hi is not None else None)
                for lo, hi in weight_bounds
            ],
            options={
                "threads": 1,
                "primal_feasibility_tolerance": 1e-9,
                "dual_feasibility_tolerance": 1e-9,
            },
        )
    if not opt.success:
        raise ValueError(
            f"no certified optimum (solver status {opt.status}): {opt.message}"
        )

    weights = [float(fraction * total) for fraction in opt.x]
    weight_map = dict(zip(ingredients, weights, strict=True))
    achieved = math.fsum(weights)
    residual = achieved - total
    if abs(residual) > 1e-8 * total:
        raise ValueError("independent total-weight check failed")
    weight_checks = {}
    for key, (lo, hi) in zip(ingredients, weight_bounds, strict=True):
        check = slacks(weight_map[key], lo, hi)
        verify_slacks(check, 1e-8 * total)
        weight_checks[key] = check

    # Recompute from the returned physical weights and raw input coefficients.
    properties = {}
    for name, (coeffs, low, high, scale) in property_rows.items():
        weighted_total = math.fsum(w * c for w, c in zip(weights, coeffs, strict=True))
        mean = weighted_total / achieved
        check = slacks(mean, low, high)
        verify_slacks(check, 1e-8 * scale)
        properties[name] = {
            "weighted_mean": mean,
            "weighted_total": weighted_total,
            **check,
        }
    total_cost = math.fsum(w / basis * c for w, c in zip(weights, cost, strict=True))
    avg_cost = total_cost / achieved * basis
    if not math.isclose(
        float(opt.fun) * cost_scale, avg_cost, rel_tol=1e-8, abs_tol=1e-8 * cost_scale
    ):
        raise ValueError("independent objective check failed")
    return {
        "status": "optimal",
        "weights": weight_map,
        "total_weight": achieved,
        "total_cost": total_cost,
        "cost_per_basis_weight": avg_cost,
        "weight_residual": residual,
        "weight_slacks": weight_checks,
        "nutrients": properties,
    }


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("input", type=Path)
    cli.add_argument("output", type=Path)
    args = cli.parse_args()
    if args.input.resolve() == args.output.resolve():
        cli.error("input and output must be distinct")
    tmp = None
    try:
        result = solve(json.loads(args.input.read_text(encoding="utf-8")))
        payload = (
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        )
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.output.parent, delete=False
        ) as f:
            tmp = Path(f.name)
            f.write(payload)
        tmp.replace(args.output)
        print(
            f"Verified optimal solution for {len(result['weights'])} ingredients: {args.output}"
        )
    except (ValueError, TypeError, KeyError, OSError, OverflowError) as exc:
        cli.exit(2, f"Blending failed: {exc}\n")
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
