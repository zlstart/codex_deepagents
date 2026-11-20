"""Check the acceptance oracles independently of the generative model."""

import csv
import json

import numpy as np
import pytest
import trimesh
from PIL import Image, ImageDraw

from evals.live import audit_trace
from evals.validate import (
    InvalidArtifact,
    downtime,
    feed_mix,
    pellet_mill,
    reference_orbit,
    three_body,
    vibration,
)


def write_csv(path, fields, data):
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(fields)
        writer.writerows(data)


def plot(path):
    img = Image.new("RGB", (640, 360), "white")
    ImageDraw.Draw(img).ellipse((40, 40, 500, 300), outline="black", width=12)
    img.save(path)


def test_simulation_checks_positions_and_velocities(tmp_path):
    path = tmp_path / "trajectory.csv"
    states = reference_orbit()
    data = [
        [i / 100, body + 1, *state[0, body], *state[1, body]]
        for i, state in enumerate(states)
        for body in range(3)
    ]
    labels = ["t", "body", "x", "y", "z", "vx", "vy", "vz"]
    write_csv(path, labels, data)
    plot(tmp_path / "orbits.png")
    assert three_body(tmp_path)
    data[40][-1] = 0
    write_csv(path, labels, data)
    with pytest.raises(InvalidArtifact, match="state error"):
        three_body(tmp_path)


def test_glb_checks_ring_and_relative_separation(tmp_path):
    parts = {
        name: trimesh.creation.box()
        for name in ("frame", "motor", "shaft", "roller_left", "roller_right")
    }
    parts["roller_left"] = trimesh.creation.cylinder(radius=0.15, height=0.3)
    parts["roller_left"].apply_translation([-0.2, 0, 0])
    parts["roller_right"] = trimesh.creation.cylinder(radius=0.15, height=0.3)
    parts["roller_right"].apply_translation([0.2, 0, 0])
    parts["ring_die"] = trimesh.creation.annulus(r_min=0.5, r_max=1.5, height=2)
    assembled = trimesh.Scene()
    exploded = trimesh.Scene()
    for i, (name, mesh) in enumerate(parts.items()):
        assembled.add_geometry(mesh, node_name=name)
        transform = np.eye(4)
        transform[:3, 3] = [i, (-1) ** i * i, 0]
        exploded.add_geometry(mesh, node_name=name, transform=transform)
    assembled.export(tmp_path / "pellet.glb")
    exploded.export(tmp_path / "exploded.glb")
    assert pellet_mill(tmp_path)
    assembled.export(tmp_path / "exploded.glb")
    with pytest.raises(InvalidArtifact, match="too few parts"):
        pellet_mill(tmp_path)


def test_spectrum_checks_every_bin(tmp_path):
    path = tmp_path / "spectrum.csv"
    t = np.arange(4096) / 1024
    x = (
        0.12
        + 0.8 * np.sin(2 * np.pi * 48.25 * t)
        + 0.35 * np.cos(2 * np.pi * 119.5 * t)
        + 0.06 * np.sin(2 * np.pi * 239 * t)
    )
    amplitudes = abs(np.fft.rfft(x - x.mean())) * 2 / len(x)
    amplitudes[[0, -1]] /= 2
    data = list(zip(np.fft.rfftfreq(4096, 1 / 1024), amplitudes, strict=True))
    fields = ["frequency_hz", "amplitude"]
    write_csv(path, fields, data)
    (tmp_path / "vibration.json").write_text(
        json.dumps(
            {
                "sample_count": 4096,
                "ac_rms": float(x.std()),
                "top_hz": [48.25, 119.5, 239],
            }
        )
    )
    plot(tmp_path / "spectrum.png")
    assert vibration(tmp_path)
    data[5] = (data[5][0], 0.4)
    write_csv(path, fields, data)
    with pytest.raises(InvalidArtifact, match="amplitude"):
        vibration(tmp_path)


def test_feed_oracle_rejects_feasible_suboptimal_formula(tmp_path):
    from scipy.optimize import linprog

    def write(fractions):
        kg = dict(
            zip(
                ["corn", "soy", "bran", "lime"], np.array(fractions) * 1000, strict=True
            )
        )
        analysis = (
            np.array(
                [
                    [240, 430, 200, 100],
                    [9, 46, 16, 0],
                    [2, 4, 12, 0],
                    [0.03, 0.32, 0.13, 38],
                ]
            )
            @ fractions
        )
        result = dict(
            zip(
                ["cost_per_tonne", "protein_pct", "fiber_pct", "calcium_pct"],
                analysis,
                strict=True,
            )
        )
        (tmp_path / "formula.json").write_text(json.dumps({"kg": kg, **result}))

    optimum = linprog(
        [240, 430, 200, 100],
        A_ub=[
            [-9, -46, -16, 0],
            [2, 4, 12, 0],
            [-0.03, -0.32, -0.13, -38],
            [0.03, 0.32, 0.13, 38],
        ],
        b_ub=[-18, 5, -0.75, 1],
        A_eq=[[1] * 4],
        b_eq=[1],
        bounds=[(0.35, 1), (0, 0.4), (0, 0.25), (0, 0.04)],
    )
    write(optimum.x)
    assert feed_mix(tmp_path)
    write([0.5, 0.28, 0.2, 0.02])
    with pytest.raises(InvalidArtifact, match="LP optimum"):
        feed_mix(tmp_path)


def test_downtime_oracle_checks_each_group(tmp_path):
    fields = ["date", "machine", "reason", "minutes"]
    before, after = "2025-12-31", "2026-01-01"
    data = [
        (before, "A", "jam", 15),
        (before, "A", "electrical", 5),
        (after, "A", "jam", 25),
        (after, "A", "electrical", 5),
        (before, "B", "maintenance", 10),
        (after, "B", "maintenance", 10),
        (after, "B", "jam", 10),
    ]
    write_csv(tmp_path / "downtime.csv", fields, data)
    (tmp_path / "downtime.json").write_text(
        json.dumps(
            {
                "by_reason": {"jam": 50, "electrical": 10, "maintenance": 20},
                "total_machine_minutes": 80,
            }
        )
    )
    assert downtime(tmp_path)
    write_csv(tmp_path / "downtime.csv", fields, [*data, data[0]])
    with pytest.raises(InvalidArtifact, match="attribution"):
        downtime(tmp_path)


def test_trace_requires_actual_tool_within_subagent():
    nodes = [
        ("r", None, "run", "run"),
        ("m", "r", "model", "model"),
        ("s", "r", "subagent", "engineering"),
        ("t", "s", "tool", "execute"),
    ]
    events = []
    for iteration, status in [(nodes, "running"), (reversed(nodes), "completed")]:
        for key, parent, kind, label in iteration:
            events.append(
                {
                    "id": key,
                    "parent_id": parent,
                    "kind": kind,
                    "label": label,
                    "seq": len(events) + 1,
                    "status": status,
                }
            )
    events.insert(
        2,
        {
            "id": "budget",
            "parent_id": "r",
            "kind": "notice",
            "label": "quota",
            "seq": 0,
            "status": "completed",
        },
    )
    for i, node in enumerate(events, 1):
        node["seq"] = i
    assert audit_trace(events, "r", True)
    for node in events:
        if node["id"] == "t":
            node["parent_id"] = "r"
    with pytest.raises(InvalidArtifact, match="nested"):
        audit_trace(events, "r", True)
    with pytest.raises(InvalidArtifact, match="missing initial"):
        audit_trace(
            [e for e in events if (e["id"], e["status"]) != ("t", "running")],
            "r",
            False,
        )


@pytest.mark.asyncio
async def test_reinspection_reads_terminal_run_without_submitting_again(tmp_path):
    import httpx

    from evals.live import run_case
    from evals.scenarios import SCENARIOS

    scenario = next(item for item in SCENARIOS if item.key == "downtime")
    previous = {
        "session_id": "session",
        "run_id": "run",
        "started": "2026-11-20",
        "duration_seconds": 120,
    }
    old = {
        "id": "run",
        "status": "completed",
        "prompt": scenario.prompt,
        "answer": "检查完成。",
    }
    sources = {
        "downtime.csv": (
            "date,machine,reason,minutes\n2025-12-31,A,jam,15\n"
            "2025-12-31,A,electrical,5\n2026-01-01,A,jam,25\n2026-01-01,A,electrical,5\n"
            "2025-12-31,B,maintenance,10\n2026-01-01,B,maintenance,10\n2026-01-01,B,jam,10\n"
        ),
        "downtime.json": json.dumps(
            {
                "total_machine_minutes": 80,
                "by_reason": {"jam": 50, "electrical": 10, "maintenance": 20},
            }
        ),
    }
    ids = [
        ("run", "run", None, "run"),
        ("m", "model", "run", "model"),
        ("t", "tool", "run", "execute"),
    ]
    events = []
    for nodes, status in [(ids, "running"), (reversed(ids), "completed")]:
        events += [
            {
                "seq": len(events) + i + 1,
                "id": id_,
                "parent_id": parent,
                "kind": kind,
                "label": label,
                "status": status,
            }
            for i, (id_, kind, parent, label) in enumerate(nodes)
        ]

    def endpoint(request):
        assert request.method == "GET"
        route = request.url.path.rsplit("/", 1)[-1]
        if route == "run":
            return httpx.Response(200, json=old)
        if route == "runs":
            return httpx.Response(200, json=[old])
        if route == "events":
            fresh = request.url.params["after"] == "0"
            return httpx.Response(
                200,
                json={
                    "events": events if fresh else [],
                    "cursor": len(events),
                    "status": "completed",
                },
            )
        if route == "files":
            return httpx.Response(200, json=[{"path": name} for name in sources])
        return httpx.Response(200, text=sources[request.url.params["path"]])

    async with httpx.AsyncClient(
        base_url="https://qa.test", transport=httpx.MockTransport(endpoint)
    ) as client:
        result = await run_case(client, scenario, tmp_path, 15, previous)
    assert result["passed"], result["errors"]
    assert result["mode"] == "read_only_reinspection"
    assert result["original_duration_seconds"] == 120
    assert (tmp_path / scenario.key / "answer.txt").read_text() == old["answer"]


@pytest.mark.asyncio
async def test_followup_requires_validated_original_files_before_post(tmp_path):
    import httpx

    from evals.live import start_followup
    from evals.scenarios import SCENARIOS

    scenario = next(s for s in SCENARIOS if s.key == "feed_mix")
    events = []
    for key, parent, kind, label, status in [
        ("r", None, "run", "original", "running"),
        ("m", "r", "model", "model", "running"),
        ("m", "r", "model", "model", "completed"),
        ("e", "r", "tool", "execute", "running"),
        ("e", "r", "tool", "execute", "completed"),
        ("r", None, "run", "original", "failed"),
    ]:
        events.append(
            {
                "id": key,
                "parent_id": parent,
                "kind": kind,
                "label": label,
                "status": status,
                "seq": len(events) + 1,
            }
        )

    def endpoint(req):
        assert req.method == "GET"
        if req.url.path.endswith("/events"):
            return httpx.Response(
                200, json={"events": events, "cursor": 6, "has_more": False}
            )
        return httpx.Response(
            200, json={"kg": {"corn": 0, "soy": 0, "lime": 0, "bran": 0}}
        )

    async with httpx.AsyncClient(
        base_url="https://qa.test", transport=httpx.MockTransport(endpoint)
    ) as client:
        with pytest.raises(InvalidArtifact, match="mass balance"):
            await start_followup(
                client,
                scenario,
                "/api/sessions/s",
                {"id": "r", "status": "failed"},
                tmp_path,
                {},
            )
