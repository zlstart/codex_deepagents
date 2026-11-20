"""Independent numerical and geometric oracles for the five live tasks."""

import csv
import io
import json
import math
import struct
from collections import defaultdict

import numpy as np
from PIL import Image


class InvalidArtifact(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidArtifact(message)


def near(value, expected, atol, message):
    require(
        math.isfinite(float(value)) and abs(float(value) - expected) <= atol,
        f"{message}: got {value}, expected {expected} ± {atol}",
    )


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def document(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def check_png(path):
    with Image.open(path) as img:
        require(img.format == "PNG", f"{path.name} must be PNG")
        width, height = img.size
        require(
            640 <= width <= 8192 and 360 <= height <= 8192,
            f"unexpected image size {img.size}",
        )
        img.thumbnail((320, 240))
        pixels = np.array(img.convert("RGB"), dtype=float)
        require(
            pixels.std(axis=(0, 1)).max() > 5, "plot contains too little visible detail"
        )
        return f"{width}×{height} PNG"


def derivative(state):
    positions, velocities = state
    delta = positions[None, :, :] - positions[:, None, :]
    radii = np.linalg.norm(delta, axis=2)
    np.fill_diagonal(radii, np.inf)
    return np.array([velocities, (delta / radii[:, :, None] ** 3).sum(axis=1)])


def reference_orbit():
    positions = [[-0.97000436, 0.24308753, 0], [0.97000436, -0.24308753, 0], [0, 0, 0]]
    velocities = [[0.466203685, 0.43236573, 0]] * 2 + [[-0.93240737, -0.86473146, 0]]
    angle = math.pi / 6
    rotation = np.array(
        [
            [1, 0, 0],
            [0, math.cos(angle), -math.sin(angle)],
            [0, math.sin(angle), math.cos(angle)],
        ]
    )
    state = np.array([positions, velocities]) @ rotation.T
    states = [state.copy()]
    step = 0.001
    for n in range(1000):
        a = derivative(state)
        b = derivative(state + step * a / 2)
        c = derivative(state + step * b / 2)
        d = derivative(state + step * c)
        state = state + step * (a + 2 * b + 2 * c + d) / 6
        if (n + 1) % 10 == 0:
            states.append(state.copy())
    return np.array(states)


def three_body(root):
    samples = rows(root / "trajectory.csv")
    require(len(samples) == 303, f"expected 303 samples, found {len(samples)}")
    trajectory = np.full((101, 2, 3, 3), np.nan)
    for row in samples:
        t, body = float(row["t"]), int(row["body"]) - 1
        tick = round(t * 100)
        require(0 <= tick <= 100 and 0 <= body <= 2, "time/body out of range")
        near(t, tick / 100, 1e-7, "time grid")
        trajectory[tick, :, body] = [
            [float(row[key]) for key in keys]
            for keys in (("x", "y", "z"), ("vx", "vy", "vz"))
        ]
    require(np.isfinite(trajectory).all(), "missing, duplicate or non-finite samples")
    error = abs(trajectory - reference_orbit()).max()
    require(error < 1e-3, f"maximum state error against RK4 reference {error:.3g}")
    positions, velocities = trajectory[:, 0], trajectory[:, 1]
    energies = (velocities**2).sum(axis=(1, 2)) / 2
    for a, b in ((0, 1), (0, 2), (1, 2)):
        energies -= 1 / np.linalg.norm(positions[:, a] - positions[:, b], axis=1)
    drift = abs(energies / energies[0] - 1).max()
    require(drift < 2e-4, f"energy drift {drift:.3g}")
    center_drift = np.linalg.norm(
        positions.mean(axis=1) - positions[0].mean(axis=0), axis=1
    ).max()
    momentum = np.linalg.norm(velocities.sum(axis=1), axis=1).max()
    require(
        center_drift < 1e-5 and momentum < 1e-5,
        "center of mass or total momentum not conserved",
    )
    return [
        f"最大质心位移 {center_drift:.3g}，总动量模长峰值 {momentum:.3g}",
        f"303 个三维状态，最大状态误差 {error:.3g}，最大相对能量漂移 {drift:.3g}",
        check_png(root / "orbits.png"),
    ]


def _load_glb(path):
    import trimesh

    data = path.read_bytes()
    require(len(data) >= 32, "truncated GLB")
    magic, version, length, js_length, js_type = struct.unpack_from("<4sIIII", data)
    require(
        (magic, version, length, js_type) == (b"glTF", 2, len(data), 0x4E4F534A),
        "invalid GLB 2.0 header",
    )
    header = json.loads(data[20 : 20 + js_length])
    for item in [*header.get("buffers", []), *header.get("images", [])]:
        require("uri" not in item, "model must be self-contained")
    scene = trimesh.load(io.BytesIO(data), file_type="glb", force="scene", process=True)
    parts = {}
    for name in ("frame", "motor", "shaft", "ring_die", "roller_left", "roller_right"):
        require(name in scene.graph.nodes_geometry, f"missing named mesh node {name}")
        transform, mesh_name = scene.graph.get(name)
        mesh = scene.geometry[mesh_name].copy()
        mesh.apply_transform(transform)
        require(
            len(mesh.faces) >= 4
            and len(mesh.vertices) >= 4
            and np.isfinite(mesh.vertices).all()
            and min(mesh.extents) > 1e-7,
            f"degenerate or non-finite mesh {name}",
        )
        parts[name] = mesh
    ring = parts["ring_die"].copy()
    ring.merge_vertices(merge_tex=True, merge_norm=True)
    # A sealed annular solid has genus >= 1 (Euler characteristic <= 0).
    components = ring.split(only_watertight=False)
    require(
        any(
            c.is_watertight
            and c.euler_number <= 0
            and max(c.extents) >= max(ring.extents) * 0.6
            for c in components
        ),
        "ring die must be a closed mesh with a through-hole",
    )
    return parts


def pellet_mill(root):
    assembled = _load_glb(root / "pellet.glb")
    exploded = _load_glb(root / "exploded.glb")
    scale = max(assembled["frame"].extents)
    shifts = {}
    for name, part in assembled.items():
        other = exploded[name]
        left = np.asarray(part.vertices)
        right = np.asarray(other.vertices)
        require(
            left.shape == right.shape and len(part.faces) == len(other.faces),
            f"mesh topology changed for {name}",
        )
        # Compare shape in world space, tolerating reordered vertices and GLB float rounding.
        left = left - left.mean(axis=0)
        right = right - right.mean(axis=0)
        from scipy.spatial import cKDTree

        error = max(
            cKDTree(left).query(right)[0].max(), cKDTree(right).query(left)[0].max()
        )
        require(error <= scale * 2e-5, f"{name} shape / orientation changed: {error}")
        shifts[name] = other.bounds.mean(axis=0) - part.bounds.mean(axis=0)
    ring = assembled["ring_die"]
    for name in ("roller_left", "roller_right"):
        center = assembled[name].bounds.mean(axis=0)
        require(
            (
                (center >= ring.bounds[0] - scale * 0.02)
                & (center <= ring.bounds[1] + scale * 0.02)
            ).all(),
            f"{name} center is outside the ring assembly",
        )
    roller_separation = np.linalg.norm(
        assembled["roller_left"].bounds.mean(axis=0)
        - assembled["roller_right"].bounds.mean(axis=0)
    )
    require(
        roller_separation > 0.02 * max(ring.extents),
        "rollers coincide in assembled view",
    )
    # Count movement relative to the frame so global scene translation cannot pass.
    offsets = [vec - shifts["frame"] for name, vec in shifts.items() if name != "frame"]
    significant = [vec for vec in offsets if np.linalg.norm(vec) > scale * 0.03]
    require(len(significant) >= 4, "too few parts separate from frame")
    directions = [v / np.linalg.norm(v) for v in significant]
    require(
        any(a @ b < 0.8 for a in directions for b in directions),
        "parts need distinct explosion directions",
    )
    return [
        f"每份 GLB 均含六个命名实体网格，环模有通孔，{len(significant)} 个零件相对于机架移开",
        "两压辊中心位于环模包围盒内，各部件形状和朝向保持一致",
    ]


def vibration(root):
    spectrum = rows(root / "spectrum.csv")
    require(len(spectrum) == 2049, f"expected 2049 bins, found {len(spectrum)}")
    peaks = {48.25: 0.8, 119.5: 0.35, 239: 0.06}
    for idx, row in enumerate(sorted(spectrum, key=lambda r: float(r["frequency_hz"]))):
        freq = idx / 4
        near(row["frequency_hz"], freq, 1e-6, "frequency grid")
        near(row["amplitude"], peaks.get(freq, 0), 2e-5, f"amplitude at {freq} Hz")
    result = document(root / "vibration.json")
    near(result["sample_count"], 4096, 0, "sample_count")
    near(
        result["ac_rms"],
        math.sqrt(sum(a * a for a in peaks.values()) / 2),
        5e-4,
        "AC RMS",
    )
    require(result["top_hz"] == list(peaks), "incorrect peak ranking")
    return [
        "2,049 个单边 FFT 频点及振幅、前三个峰值频率和交流均方根通过独立校验",
        check_png(root / "spectrum.png"),
    ]


def feed_mix(root):
    result = document(root / "formula.json")
    names = ["corn", "soy", "bran", "lime"]
    kg = np.array([float(result["kg"][name]) for name in names])
    require(np.isfinite(kg).all() and min(kg) >= -1e-4, "invalid ingredient quantity")
    near(kg.sum(), 1000, 0.002, "mass balance")
    require(
        kg[0] >= 349.999 and all(kg[1:] <= np.array([400, 250, 40]) + 0.001),
        "ingredient constraints",
    )
    fractions = kg / 1000
    calculated = (
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
    cost, protein, fiber, calcium = calculated
    require(
        protein >= 17.9999 and fiber <= 5.0001 and 0.7499 <= calcium <= 1.0001,
        "nutrition constraints",
    )
    for key, value in zip(
        ("cost_per_tonne", "protein_pct", "fiber_pct", "calcium_pct"), calculated
    ):
        near(result[key], value, 0.001, key)
    # Independent LP solution: protein floor, bran ceiling and calcium ceiling are active.
    optimal = np.linalg.solve(
        [[1, 1, 1, 1], [9, 46, 16, 0], [0, 0, 1, 0], [0.03, 0.32, 0.13, 38]],
        [1, 18, 0.25, 1],
    ) @ [240, 430, 200, 100]
    near(cost, optimal, 0.001, "objective vs LP optimum")
    return [f"总质量 1 吨，各原料和营养成分满足约束，最优成本为每吨 {cost:.5f} 元"]


def downtime(root):
    expected = {
        ("2025-12-31", "A", "jam"): 15,
        ("2025-12-31", "A", "electrical"): 5,
        ("2026-01-01", "A", "jam"): 25,
        ("2026-01-01", "A", "electrical"): 5,
        ("2025-12-31", "B", "maintenance"): 10,
        ("2026-01-01", "B", "maintenance"): 10,
        ("2026-01-01", "B", "jam"): 10,
    }
    observed = rows(root / "downtime.csv")
    parsed = {
        (r["date"], r["machine"], r["reason"]): float(r["minutes"]) for r in observed
    }
    require(
        len(observed) == len(expected) and parsed == expected,
        f"daily attribution mismatch: {parsed}",
    )
    totals = defaultdict(int)
    for (_, _, reason), minutes in expected.items():
        totals[reason] += minutes
    result = document(root / "downtime.json")
    near(result["total_machine_minutes"], sum(expected.values()), 0, "machine minutes")
    require(result["by_reason"] == totals, "per-cause totals mismatch")
    return ["跨日切分后得到七组归因记录，重复区间已消除，设备累计停机 80 分钟"]


VALIDATORS = {
    fn.__name__: fn for fn in (three_body, pellet_mill, vibration, feed_mix, downtime)
}
