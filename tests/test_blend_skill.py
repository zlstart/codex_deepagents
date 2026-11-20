"""Check the blending CLI against an analytically solvable two-material mix."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


def test_blend_primal_cost_and_constraint_checks(tmp_path):
    script = (
        Path(__file__).resolve().parents[1]
        / "famsun/skills/engineering-data/scripts/blend.py"
    )
    source, target = tmp_path / "request.json", tmp_path / "solution.json"
    spec = {
        "total_weight": 60,
        "cost_basis_weight": 20,
        "ingredients": [
            {"name": "base", "unit_cost": 7, "min_weight": 10},
            {"name": "additive", "unit_cost": 12, "max_weight": 40},
        ],
        "nutrients": [
            {
                "name": "fraction",
                "min": 0.58,
                "coefficients": {"base": 0.1, "additive": 0.9},
            },
            {
                "name": "property",
                "max": 130,
                "coefficients": {"base": 80, "additive": 160},
            },
        ],
    }
    source.write_text(json.dumps(spec))
    cmd = [sys.executable, str(script), str(source), str(target)]
    subprocess.run(cmd, capture_output=True, check=True)
    result = json.loads(target.read_text())
    assert result["weights"] == pytest.approx({"base": 24, "additive": 36})
    assert result["total_cost"] == pytest.approx(30) and result[
        "cost_per_basis_weight"
    ] == pytest.approx(10)
    assert result["weight_residual"] == pytest.approx(0)
    assert result["nutrients"]["property"]["weighted_total"] == pytest.approx(7680)
    assert result["nutrients"]["property"]["upper_slack"] == pytest.approx(2)
    assert result["nutrients"]["fraction"]["lower_slack"] == pytest.approx(0, abs=1e-12)
    assert result["weight_slacks"]["additive"]["upper_slack"] == pytest.approx(4)
    spec["ingredients"][1]["max_weight"] = 15
    source.write_text(json.dumps(spec))
    failed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert failed.returncode == 2 and "infeasible" in failed.stderr.lower()
    assert json.loads(target.read_text()) == result
