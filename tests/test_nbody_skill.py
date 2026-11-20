"""Independent physics and output checks for the reusable simulation skill."""

import csv
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from PIL import Image

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "famsun/skills/scientific-simulation/scripts/nbody.py"
)
loader_spec = importlib.util.spec_from_file_location("nbody_skill", SCRIPT)
solver = importlib.util.module_from_spec(loader_spec)
loader_spec.loader.exec_module(solver)


def binary():
    # Equal masses m=2 at separation d=4, with G=3. Each circular orbit
    # has radius d/2 and speed sqrt(G*m/(2*d)). Tilt to the x-z plane.
    speed = np.sqrt(3 / 4)
    return {
        "masses": [2, 2],
        "positions": [[2, 0, 0], [-2, 0, 0]],
        "velocities": [[0, 0.25, speed], [0, 0.25, -speed]],
        "G": 3,
        "t_start": 4.0,
        "t_end": 4.8,
        "samples": 17,
        "references": ["analytic circular binary with uniform center-of-mass drift"],
    }


def test_cli_exports_inclined_binary_and_inertial_center_motion(tmp_path):
    source = tmp_path / "orbit.json"
    spec = binary()
    source.write_text(json.dumps(spec))
    csv_path, png, report = [
        tmp_path / name for name in ("custom.csv", "custom.png", "diagnostics.json")
    ]
    cmd = [
        sys.executable,
        str(SCRIPT),
        str(source),
        "--csv",
        str(csv_path),
        "--plot",
        str(png),
        "--checks",
        str(report),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    checks = json.loads(report.read_text())
    assert json.loads(result.stdout) == checks
    assert checks["references"] == spec["references"]
    assert checks["sampled_min_pair_distance"] == pytest.approx(4, abs=1e-8)
    assert checks["max_com_inertial_drift"] < 1e-12
    assert checks["max_com_displacement"] == pytest.approx(0.2)
    assert 0 <= checks["max_relative_energy_drift"] < 1e-8
    assert 0 <= checks["max_relative_momentum_drift"] < 1e-12
    with csv_path.open() as stream:
        rows = list(csv.DictReader(stream))
        assert list(rows[0]) == ["t", "body", "x", "y", "z", "vx", "vy", "vz"]
        assert len(rows) == 2 * spec["samples"]
        assert [int(row["body"]) for row in rows] == [1, 2] * spec["samples"]
        assert (
            float(rows[0]["t"]) == spec["t_start"]
            and float(rows[-1]["t"]) == spec["t_end"]
        )
        dt = float(rows[-2]["t"]) - float(rows[0]["t"])
        speed = np.sqrt(3 / 4)
        phase = dt * speed / 2
        assert np.allclose(
            [float(rows[-2][k]) for k in ("x", "y", "z", "vx", "vy", "vz")],
            [
                2 * np.cos(phase),
                0.25 * dt,
                2 * np.sin(phase),
                -speed * np.sin(phase),
                0.25,
                speed * np.cos(phase),
            ],
            atol=1e-8,
            rtol=0,
        )
    with Image.open(png) as img:
        assert img.format == "PNG" and img.width >= 640 and img.height >= 360
        img.verify()


def test_zero_initial_energy_and_momentum_have_absolute_drift():
    spec = {
        "masses": [1, 1],
        "positions": [[-0.5, 0, 0], [0.5, 0, 0]],
        "velocities": [[-1, 0, 0], [1, 0, 0]],
        "G": 1,
        "t_start": 0,
        "t_end": 0.5,
        "samples": 12,
    }
    _, _, _, report = solver.integrate(spec)
    assert report["initial_energy"] == 0
    assert (
        report["max_relative_energy_drift"] is None
        and report["max_relative_momentum_drift"] is None
    )
    assert (
        report["max_abs_energy_drift"] < 1e-8
        and report["max_abs_momentum_drift"] < 1e-12
    )


def test_arbitrary_four_body_inputs_and_validation():
    spec = {
        "masses": [1, 2, 3, 4],
        "positions": [[0, 0, 0], [1, 2, 0], [2, 0, 3], [-1, 0.5, 4]],
        "velocities": [[0, 0, 0], [0.23, 0, 0], [0, 0.17, 0], [0, 0, -0.1]],
        "G": 0.6,
        "t_start": 3,
        "t_end": 3.01,
        "samples": 6,
    }
    t, pos, vel, report = solver.integrate(spec)
    assert len(t) == 6 and pos.shape == vel.shape == (6, 4, 3)
    assert (
        np.allclose(pos[0], spec["positions"])
        and report["max_abs_energy_drift"] < 1e-10
    )
    for overrides in (
        {"positions": [[0, 0, 0]] * 4},
        {"G": 0},
        {"samples": True},
        {"t_end": 3},
        {"masses": [1, 2, -1, 4]},
        {"velocities": [[0, 0, float("nan")]] * 4},
    ):
        with pytest.raises(ValueError):
            solver.integrate({**spec, **overrides})


@pytest.mark.skipif(
    os.getenv("FAMSUN_REQUIRE_DOCKER_TESTS") != "true",
    reason="requires the built FAMSUN sandbox image",
)
def test_installed_skill_runs_lagrange_triangle_inside_real_sandbox():
    from famsun.sandbox import DockerSandbox

    # The Lagrange equilateral triangle is an independent three-body
    # solution. Rotate its plane so all three output axes matter.
    radius, mass, g, tilt = 1.2, 0.5, 2.0, 0.66
    omega = np.sqrt(g * mass / (np.sqrt(3) * radius**3))
    angles = np.arange(3) * 2 * np.pi / 3

    def orient(x, y):
        return np.column_stack((x, y * np.cos(tilt), y * np.sin(tilt)))

    spec = {
        "G": g,
        "masses": [mass] * 3,
        "t_start": 0.0,
        "t_end": 0.73,
        "samples": 35,
        "positions": orient(radius * np.cos(angles), radius * np.sin(angles)).tolist(),
        "velocities": orient(
            -radius * omega * np.sin(angles), radius * omega * np.cos(angles)
        ).tolist(),
        "references": ["Lagrange uniformly rotating equilateral central configuration"],
    }
    expected_angle = angles + omega * spec["t_end"]
    expected = orient(radius * np.cos(expected_angle), radius * np.sin(expected_angle))
    sandbox = DockerSandbox(
        "nbody-ci",
        uuid4().hex,
        image=os.getenv("FAMSUN_SANDBOX_IMAGE", "famsun-sandbox:local"),
        allow_unenforced_cgroups=True,
    )
    script = "/opt/famsun/skills/scientific-simulation/scripts/nbody.py"
    try:
        assert sandbox.write("/workspace/initial.json", json.dumps(spec)).error is None
        result = sandbox.execute(
            f"sha256sum {script} && python {script} initial.json", timeout=60
        )
        assert result.exit_code == 0, result.output
        assert (
            result.output.split()[0] == hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
        )
        sandbox.stop()
        rows = list(
            csv.DictReader(
                io.StringIO(sandbox.read_artifact("trajectory.csv").decode())
            )
        )
        assert len(rows) == 3 * spec["samples"]
        final = np.array(
            [[float(row[k]) for k in ("x", "y", "z")] for row in rows[-3:]]
        )
        assert np.allclose(final, expected, rtol=0, atol=1e-8)
        report = json.loads(sandbox.read_artifact("checks.json"))
        assert (
            report["max_relative_energy_drift"] < 1e-8
            and report["max_abs_momentum_drift"] < 1e-12
        )
        with Image.open(io.BytesIO(sandbox.read_artifact("orbits.png"))) as image:
            assert image.size == (1080, 720)
            image.verify()
    finally:
        sandbox.delete()
