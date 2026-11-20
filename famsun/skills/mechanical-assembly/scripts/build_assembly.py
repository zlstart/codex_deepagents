"""Build and verify rigid assembled/exploded GLBs from an engineering manifest."""

from __future__ import annotations

import argparse
import fcntl
import io
import json
import math
import os
import tempfile
from contextlib import ExitStack
from pathlib import Path

import numpy as np
import trimesh

UNITS = {"m": 1.0, "cm": 0.01, "mm": 0.001}
DIMENSIONS = {
    "box": {"size"},
    "cylinder": {"radius", "height"},
    "annulus": {"inner_radius", "outer_radius", "height"},
}
COMMON = {"name", "shape", "position", "axis", "roll_deg", "color", "explode"}


def number(value, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("expected a finite number")
    if positive and value <= 0:
        raise ValueError("dimensions must be positive")
    return float(value)


def vector(values, *, positive=False):
    if not isinstance(values, list) or len(values) != 3:
        raise ValueError("expected a three-element vector")
    return np.array([number(v, positive=positive) for v in values])


def make_part(spec, scale):
    shape = spec.get("shape")
    if not isinstance(shape, str) or shape not in DIMENSIONS:
        raise ValueError("shape must be box, cylinder or annulus")
    unknown = set(spec) - (COMMON | DIMENSIONS[shape])
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")
    missing = DIMENSIONS[shape] - spec.keys()
    if missing:
        raise ValueError(f"missing dimensions: {sorted(missing)}")

    if shape == "box":
        mesh = trimesh.creation.box(extents=vector(spec["size"], positive=True) * scale)
    else:
        height = number(spec["height"], positive=True) * scale
        if shape == "cylinder":
            radius = number(spec["radius"], positive=True) * scale
            mesh = trimesh.creation.cylinder(radius, height, sections=64)
        else:
            inner = number(spec["inner_radius"], positive=True) * scale
            outer = number(spec["outer_radius"], positive=True) * scale
            if inner >= outer:
                raise ValueError("inner_radius must be less than outer_radius")
            mesh = trimesh.creation.annulus(inner, outer, height, sections=64)

    color = spec.get("color", [156, 167, 174, 255])
    if (
        not isinstance(color, list)
        or len(color) != 4
        or not all(type(value) is int and 0 <= value <= 255 for value in color)
    ):
        raise ValueError("color must be four 8-bit RGBA integers")
    mesh.visual = trimesh.visual.TextureVisuals(
        material=trimesh.visual.material.PBRMaterial(
            name=spec["name"],
            baseColorFactor=color,
            metallicFactor=0.35,
            roughnessFactor=0.62,
            alphaMode="BLEND" if color[3] < 255 else "OPAQUE",
        )
    )

    direction = vector(spec.get("axis", [0, 0, 1]))
    maximum = np.max(np.abs(direction))
    if maximum == 0:
        raise ValueError("axis must have a nonzero direction")
    direction /= maximum
    direction /= np.linalg.norm(direction)
    transform = trimesh.geometry.align_vectors([0, 0, 1], direction)
    roll = math.radians(number(spec.get("roll_deg", 0)) % 360)
    transform = transform @ trimesh.transformations.rotation_matrix(roll, [0, 0, 1])
    transform[:3, 3] = vector(spec.get("position", [0, 0, 0])) * scale
    exploded = transform.copy()
    exploded[:3, 3] += vector(spec.get("explode", [0, 0, 0])) * scale
    return mesh, transform, exploded


def build(manifest):
    if not isinstance(manifest, dict) or set(manifest) != {"units", "parts"}:
        raise ValueError("manifest must contain units and parts")
    units, parts = manifest["units"], manifest["parts"]
    if not isinstance(units, str) or units not in UNITS:
        raise ValueError("units must be m, cm or mm")
    if not isinstance(parts, list) or not 1 <= len(parts) <= 512:
        raise ValueError("specify 1 to 512 parts")

    assembled, exploded = trimesh.Scene(), trimesh.Scene()
    transforms = {}
    for part in parts:
        if not isinstance(part, dict):
            raise TypeError("each part must be an object")
        name = part.get("name")
        if (
            not isinstance(name, str)
            or not name.strip()
            or name == "world"
            or name in transforms
        ):
            raise ValueError(
                "part names must be unique nonempty strings; world is reserved"
            )
        try:
            mesh, pose, moved = make_part(part, UNITS[units])
        except (ValueError, OverflowError) as error:
            raise ValueError(f"part {name}: {error}") from error
        assembled.add_geometry(mesh, node_name=name, geom_name=name, transform=pose)
        exploded.add_geometry(mesh, node_name=name, geom_name=name, transform=moved)
        transforms[name] = (pose, moved)
    if all(np.array_equal(a, b) for a, b in transforms.values()):
        raise ValueError("at least one part requires a nonzero explosion displacement")

    # Validate emitted GLBs through a fresh parser, including transforms and topology.
    payloads = [scene.export(file_type="glb") for scene in (assembled, exploded)]
    loaded = [
        trimesh.load_scene(io.BytesIO(data), file_type="glb", process=False)
        for data in payloads
    ]
    for index, scene in enumerate(loaded):
        if set(scene.graph.nodes_geometry) != set(transforms):
            raise ValueError("export changed the part names")
        for name, poses in transforms.items():
            matrix, key = scene.graph[name]
            mesh = scene.geometry[key]
            expected = assembled.geometry[name]
            if not (
                np.allclose(matrix, poses[index], rtol=1e-6, atol=1e-10)
                and mesh.is_volume
                and np.isfinite(mesh.bounds).all()
                and np.array_equal(mesh.faces, expected.faces)
                and np.array_equal(mesh.vertices, expected.vertices.astype(np.float32))
                and np.array_equal(
                    mesh.visual.material.baseColorFactor,
                    expected.visual.material.baseColorFactor,
                )
            ):
                raise ValueError(f"export changed geometry, material or pose of {name}")
    return payloads


def publish(paths, data):
    """Prepare both files first; restore previous files if a replacement fails."""
    if paths[0].resolve() == paths[1].resolve() or any(
        p.suffix.lower() != ".glb" or (p.exists() and not p.is_file()) for p in paths
    ):
        raise ValueError("output must be two different .glb files")
    with ExitStack() as stack:
        # Concurrent subagents can export into the same directory. Lock in stable
        # order so a rollback cannot overwrite another builder's newer assembly.
        for parent in sorted({path.parent.resolve() for path in paths}):
            parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            stack.callback(os.close, descriptor)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        staged, backups = [], []
        for path, content in zip(paths, data, strict=True):
            directory = Path(
                stack.enter_context(tempfile.TemporaryDirectory(dir=path.parent))
            )
            staged.append(directory / "output")
            staged[-1].write_bytes(content)
            backup = directory / "original"
            if path.exists():
                os.link(path, backup, follow_symlinks=False)
            backups.append(backup)
        os.replace(staged[0], paths[0])
        try:
            os.replace(staged[1], paths[1])
        except OSError:
            if backups[0].exists():
                os.replace(backups[0], paths[0])
            else:
                paths[0].unlink()
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("assembled", type=Path)
    parser.add_argument("exploded", type=Path)
    args = parser.parse_args()
    try:
        if args.manifest.stat().st_size > 1024 * 1024:
            raise ValueError("manifest exceeds 1 MiB")
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        data = build(manifest)
        publish([args.assembled, args.exploded], data)
    except (OSError, ValueError, TypeError, OverflowError) as error:
        parser.exit(2, f"build_assembly: {error}\n")
    print(
        json.dumps(
            {
                "parts": len(manifest["parts"]),
                "units": "m",
                "verified": True,
                "assembled_bytes": len(data[0]),
                "exploded_bytes": len(data[1]),
            }
        )
    )


if __name__ == "__main__":
    main()
