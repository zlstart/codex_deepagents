"""Revalidate previously downloaded live evidence without a model or server."""

import argparse
import hashlib
import json
from pathlib import Path

from .live import audit_trace
from .scenarios import SCENARIOS
from .validate import VALIDATORS, require


def recheck(directory, scenario):
    root = directory / scenario.key
    result = json.loads((root / "result.json").read_text(encoding="utf-8"))
    trace = json.loads((root / "trace.json").read_text(encoding="utf-8"))
    require(
        result["scenario"] == scenario.key and result["agent_status"] == "completed",
        "evidence belongs to another scenario or the agent did not finish",
    )
    signatures = {item["name"]: item for item in result["artifacts"]}
    require(
        set(scenario.outputs) == set(signatures),
        "evidence does not include the expected artifacts",
    )
    for name in scenario.outputs:
        data = (root / name).read_bytes()
        require(
            len(data) == signatures[name]["bytes"]
            and hashlib.sha256(data).hexdigest() == signatures[name]["sha256"],
            f"artifact digest mismatch: {name}",
        )
    followup = result.get("mode") == "same_session_followup"
    checks = []
    if followup:
        require(
            result["original_artifacts"] == result["artifacts"],
            "follow-up rewrote the original files",
        )
        original_trace = json.loads((root / "original-trace.json").read_text())
        checks.append(
            audit_trace(
                original_trace,
                result["original_run_id"],
                scenario.require_subagent,
                terminal="failed",
            )
        )
    return [
        *checks,
        audit_trace(
            trace, result["run_id"], scenario.require_subagent and not followup
        ),
        *VALIDATORS[scenario.key](root),
    ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--case", choices=[s.key for s in SCENARIOS], action="append")
    args = parser.parse_args()
    for scenario in SCENARIOS:
        if not args.case or scenario.key in args.case:
            print(scenario.title, *recheck(args.directory, scenario), sep="\n  ")
