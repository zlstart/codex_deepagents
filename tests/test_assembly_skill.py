"""Check the mechanical assembly skill's public CLI and serialized geometry."""

import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
trimesh = pytest.importorskip("trimesh")

BUILDER = (
    Path(__file__).resolve().parents[1]
    / "famsun"
    / "skills"
    / "mechanical-assembly"
    / "scripts"
    / "build_assembly.py"
)


def manifest(units="mm"):
    return {
        "units": units,
        "parts": [
            {
                "name": "mount",
                "shape": "box",
                "size": [4, 8, 12],
                "position": [5, 7, 11],
                "roll_deg": 90,
                "color": [24, 68, 110, 255],
            },
            {
                "name": "spindle",
                "shape": "cylinder",
                "radius": 2,
                "height": 18,
                "axis": [2, 0, 0],
                "position": [-3, 2, 1],
                "explode": [-10, 1, 0],
                "color": [190, 120, 80, 128],
            },
            {
                "name": "sleeve",
                "shape": "annulus",
                "outer_radius": 3,
                "inner_radius": 2.1,
                "height": 4,
                "axis": [0, -3, 4],
                "position": [0, 2, 1],
                "explode": [0, -9, 12],
            },
        ],
    }


def cli_args(directory, spec):
    spec_file, assembled, exploded = (
        directory / n for n in ["parts.json", "assembled.glb", "exploded.glb"]
    )
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    return [str(BUILDER), str(spec_file), str(assembled), str(exploded)]


@pytest.mark.parametrize(("units", "scale"), [("m", 1.0), ("cm", 1e-2), ("mm", 1e-3)])
def test_assembly_cli_emits_rigid_colored_solids(tmp_path, units, scale):
    spec = manifest(units)
    args = cli_args(tmp_path, spec)
    completed = subprocess.run(
        [sys.executable, *args], capture_output=True, text=True, check=True
    )
    summary = json.loads(completed.stdout)
    assert summary["verified"] and summary["parts"] == 3 and summary["units"] == "m"
    for key, path in zip(("assembled_bytes", "exploded_bytes"), args[2:], strict=True):
        assert summary[key] == Path(path).stat().st_size
    assembled, exploded = [trimesh.load_scene(path, process=False) for path in args[2:]]
    expected = {part["name"] for part in spec["parts"]}
    assert (
        set(assembled.graph.nodes_geometry)
        == set(exploded.graph.nodes_geometry)
        == expected
    )
    for part in spec["parts"]:
        name = part["name"]
        before, geometry = assembled.graph[name]
        after, moved_geometry = exploded.graph[name]
        mesh = assembled.geometry[geometry]
        moved = exploded.geometry[moved_geometry]
        assert mesh.is_volume and moved.is_volume
        np.testing.assert_array_equal(mesh.vertices, moved.vertices)
        np.testing.assert_array_equal(mesh.faces, moved.faces)
        for material in (mesh.visual.material, moved.visual.material):
            assert material.name == name
            assert list(material.baseColorFactor) == part.get(
                "color", [156, 167, 174, 255]
            )
            assert material.alphaMode == ("BLEND" if name == "spindle" else "OPAQUE")
        rotation = before[:3, :3]
        np.testing.assert_allclose(rotation.T @ rotation, np.eye(3), atol=1e-7)
        assert np.linalg.det(rotation) == pytest.approx(1)
        axis = np.array(part.get("axis", [0, 0, 1]))
        np.testing.assert_allclose(
            rotation[:, 2], axis / np.linalg.norm(axis), atol=1e-7
        )
        np.testing.assert_allclose(before[:3, 3], np.array(part["position"]) * scale)
        np.testing.assert_array_equal(rotation, after[:3, :3])
        np.testing.assert_allclose(
            after[:3, 3] - before[:3, 3],
            np.array(part.get("explode", [0, 0, 0])) * scale,
        )
        if name == "mount":
            np.testing.assert_allclose(rotation[:, 0], [0, 1, 0], atol=1e-7)
            np.testing.assert_allclose(mesh.extents, np.array([4, 8, 12]) * scale)
        elif name == "spindle":
            assert np.ptp(mesh.vertices[:, 2]) == pytest.approx(18 * scale)
            assert mesh.volume == pytest.approx(np.pi * 2**2 * 18 * scale**3, rel=0.002)
        else:
            assert mesh.euler_number == 0
            _, distances, _ = trimesh.proximity.closest_point_naive(mesh, [[0, 0, 0]])
            assert distances[0] == pytest.approx(2.1 * scale, rel=0.002)
            assert mesh.volume == pytest.approx(
                np.pi * (3**2 - 2.1**2) * 4 * scale**3, rel=0.002
            )


@pytest.mark.parametrize(
    "patch",
    [
        {"axis": [0, 0, 0]},
        {"inner_radius": 3},
        {"outer_radius": 0},
        {"color": [0, 0, 256, 255]},
        {"position": [1, float("nan"), 0]},
        {"name": "mount"},
        {"size": [1, 2, 3]},
    ],
)
def test_invalid_part_preserves_both_exports(tmp_path, monkeypatch, patch):
    spec = manifest()
    spec["parts"][-1].update(patch)
    args = cli_args(tmp_path, spec)
    before = [b"previous assembly", b"previous exploded view"]
    for path, content in zip(args[2:], before, strict=True):
        Path(path).write_bytes(content)
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(BUILDER), run_name="__main__")
    assert exc.value.code == 2
    assert [Path(p).read_bytes() for p in args[2:]] == before


def test_second_publish_error_restores_both_previous_files(tmp_path, monkeypatch):
    args = cli_args(tmp_path, manifest())
    assembled, exploded = [Path(p) for p in args[2:]]
    assembled.write_bytes(b"old assembly")
    exploded.write_bytes(b"old exploded view")
    replace = os.replace

    def fail_second_commit(source, destination):
        if destination == exploded:
            raise OSError("unavailable destination")
        replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_second_commit)
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(BUILDER), run_name="__main__")
    assert exc.value.code == 2
    assert assembled.read_bytes() == b"old assembly"
    assert exploded.read_bytes() == b"old exploded view"
