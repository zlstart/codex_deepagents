"""Integrate Newtonian point masses in a 3D inertial frame from a JSON specification."""

import argparse
import csv
import json
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib
import numpy as np
from scipy.integrate import solve_ivp

matplotlib.use("Agg")
from matplotlib import pyplot as plt


def numeric(value, name):
    array = np.asarray(value)
    if array.dtype.kind not in "ifu" or not np.isfinite(array).all():
        raise ValueError(f"{name} must contain finite numbers")
    return array.astype(float)


def scalar(value, name):
    result = numeric(value, name)
    if result.ndim:
        raise ValueError(f"{name} must be a scalar")
    return result.item()


def integrate(spec):
    mass = numeric(spec["masses"], "masses")
    if mass.ndim != 1 or not 2 <= mass.size <= 128 or np.any(mass <= 0):
        raise ValueError("masses must be 2–128 positive point masses")
    n = len(mass)
    pos = numeric(spec["positions"], "positions")
    vel = numeric(spec["velocities"], "velocities")
    if pos.shape != (n, 3) or vel.shape != (n, 3):
        raise ValueError("positions and velocities must each have one [x,y,z] per mass")
    g, begin, end = (scalar(spec[key], key) for key in ("G", "t_start", "t_end"))
    if g <= 0 or end <= begin:
        raise ValueError("require positive G and t_end greater than t_start")
    count = spec["samples"]
    if type(count) is not int or count < 2 or count * n > 100_000:
        raise ValueError("require >=2 samples and <=100000 body-samples")
    references = spec.get("references", [])
    if not isinstance(references, list) or not all(
        isinstance(ref, str) for ref in references
    ):
        raise ValueError("references must be a list of source descriptions")
    rtol = scalar(spec.get("rtol", 1e-10), "rtol")
    atol = scalar(spec.get("atol", 1e-12), "atol")
    if not (100 * np.finfo(float).eps <= rtol < 1 and 0 < atol < 1):
        raise ValueError("require 100*machine_epsilon <= rtol < 1 and 0 < atol < 1")
    a, b = np.triu_indices(n, k=1)
    if np.any(np.linalg.norm(pos[a] - pos[b], axis=1) == 0):
        raise ValueError("coincident masses have singular gravity")

    def dynamics(_t, y):
        positions, velocities = y.reshape(2, n, 3)
        radii = positions[b] - positions[a]
        lengths = np.linalg.norm(radii, axis=1)
        if np.any(lengths == 0):
            raise ValueError("encountered a point-mass collision")
        direction = g * radii / lengths[:, None] ** 3
        acc = np.zeros_like(positions)
        np.add.at(acc, a, direction * mass[b, None])
        np.add.at(acc, b, -direction * mass[a, None])
        return np.stack((velocities, acc)).ravel()

    t = np.linspace(begin, end, count)
    solved = solve_ivp(
        dynamics,
        (begin, end),
        np.stack((pos, vel)).ravel(),
        t_eval=t,
        method="DOP853",
        rtol=rtol,
        atol=atol,
    )
    if not solved.success or len(solved.t) != count or not np.isfinite(solved.y).all():
        raise ValueError(f"solver did not return all finite samples: {solved.message}")
    positions, velocities = solved.y.reshape(2, n, 3, count).transpose(0, 3, 1, 2)
    kinetic = 0.5 * np.einsum("b,tbc,tbc->t", mass, velocities, velocities)
    potential = np.zeros(count)
    min_distance = float("inf")
    # Process one pair's time series at a time, bounding peak array size.
    for j, k in zip(a, b, strict=True):
        distance = np.linalg.norm(positions[:, j] - positions[:, k], axis=1)
        min_distance = min(min_distance, float(distance.min()))
        if distance.min() == 0:
            raise ValueError("two point masses coincide in a sampled state")
        potential -= g * mass[j] * mass[k] / distance
    energy = kinetic + potential
    momentum = np.einsum("b,tbc->tc", mass, velocities)
    center = np.einsum("b,tbc->tc", mass, positions) / mass.sum()
    momentum_err = float(np.linalg.norm(momentum - momentum[0], axis=1).max())
    energy_err = float(np.abs(energy - energy[0]).max())
    # Cancellation around zero makes a relative error meaningless. Compare
    # against a roundoff scale derived from the constituent magnitudes.
    eps = 64 * np.finfo(float).eps
    p0 = np.linalg.norm(momentum[0])
    com_expected = center[0] + (t - t[0])[:, None] * momentum[0] / mass.sum()
    checks = {
        "method": "DOP853",
        "G": g,
        "rtol": rtol,
        "atol": atol,
        "t_start": begin,
        "t_end": end,
        "samples": count,
        "body_count": n,
        "references": references,
        "sampled_min_pair_distance": min_distance,
        "initial_energy": float(energy[0]),
        "max_abs_energy_drift": energy_err,
        "max_relative_energy_drift": float(energy_err / abs(energy[0]))
        if abs(energy[0]) > eps * (kinetic[0] + abs(potential[0]))
        else None,
        "initial_momentum": momentum[0].tolist(),
        "max_abs_momentum_drift": momentum_err,
        "max_relative_momentum_drift": float(momentum_err / p0)
        if p0 > eps * np.sum(mass * np.linalg.norm(vel, axis=1))
        else None,
        "max_com_displacement": float(np.linalg.norm(center - center[0], axis=1).max()),
        "max_com_inertial_drift": float(
            np.linalg.norm(center - com_expected, axis=1).max()
        ),
    }
    return t, positions, velocities, checks


def export(t, positions, velocities, checks, csv_path, image_path, check_path):
    # Validate the diagnostics before creating any output.
    summary = json.dumps(checks, ensure_ascii=False, indent=2, allow_nan=False)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(("t", "body", "x", "y", "z", "vx", "vy", "vz"))
        for step, stamp in enumerate(t):
            for body in range(positions.shape[1]):
                writer.writerow(
                    (stamp, body + 1, *positions[step, body], *velocities[step, body])
                )
    fig = plt.figure(figsize=(9, 6), dpi=120, layout="constrained")
    try:
        ax = fig.add_subplot(projection="3d")
        for i, points in enumerate(positions.transpose(1, 0, 2)):
            (line,) = ax.plot(*points.T, label=f"Body {i + 1}")
            ax.scatter(*points[0], color=line.get_color(), s=14)
        lo, hi = positions.min(axis=(0, 1)), positions.max(axis=(0, 1))
        center, half_width = (lo + hi) / 2, float((hi - lo).max()) * 0.53
        for value, setter in zip(
            center, [ax.set_xlim, ax.set_ylim, ax.set_zlim], strict=True
        ):
            setter(value - half_width, value + half_width)
        ax.set_box_aspect((1, 1, 1))
        ax.set(xlabel="x", ylabel="y", zlabel="z", title="Newtonian trajectories")
        if positions.shape[1] <= 12:
            ax.legend()
        fig.savefig(image_path, format="png", facecolor="white")
    finally:
        plt.close(fig)
    check_path.write_text(summary + "\n", encoding="utf-8")
    return summary


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("input", type=Path)
    cli.add_argument("--csv", type=Path, default=Path("trajectory.csv"))
    cli.add_argument("--plot", type=Path, default=Path("orbits.png"))
    cli.add_argument("--checks", type=Path, default=Path("checks.json"))
    args = cli.parse_args()
    paths = (args.input, args.csv, args.plot, args.checks)
    if len({p.resolve() for p in paths}) != len(paths):
        cli.error("input, CSV, PNG and checks must be distinct paths")
    try:
        spec = json.loads(args.input.read_text(encoding="utf-8"))
        simulation = integrate(spec)
        # Prepare all files before publishing the batch. Each rename is atomic
        # within its target filesystem, including when rerunning a simulation.
        with ExitStack() as stack:
            staged = []
            for target in paths[1:]:
                if target.is_dir():
                    raise ValueError(f"expected output file, got directory: {target}")
                folder = stack.enter_context(
                    TemporaryDirectory(prefix=".nbody-", dir=target.parent)
                )
                staged.append(Path(folder) / target.name)
            summary = export(*simulation, *staged)
            for prepared, target in zip(staged, paths[1:], strict=True):
                prepared.replace(target)
        print(summary)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        cli.exit(2, f"Simulation failed: {exc}\n")


if __name__ == "__main__":
    main()
