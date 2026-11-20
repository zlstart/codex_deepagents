"""Verify native glTF structure, binary geometry, material and rigid-offset identity."""

import hashlib
import json
import math
import struct
from pathlib import Path

OUT = Path(__file__).resolve().parent


def read_glb(path):
    raw = path.read_bytes()
    magic, version, length = struct.unpack_from("<4sII", raw)
    assert (magic, version, length) == (b"glTF", 2, len(raw))
    length, kind = struct.unpack_from("<I4s", raw, 12)
    assert kind == b"JSON"
    gltf = json.loads(raw[20 : 20 + length])
    offset = 20 + length
    size, kind = struct.unpack_from("<I4s", raw, offset)
    data = raw[offset + 8 :]
    assert kind == b"BIN\x00" and len(data) == size
    assert all("uri" not in b for b in gltf["buffers"] + gltf.get("images", []))
    return gltf, data


def bounds(gltf):
    low, high = [math.inf] * 3, [-math.inf] * 3
    for node in gltf["nodes"]:
        shift = node.get("translation", (0, 0, 0))
        for prim in gltf["meshes"][node["mesh"]]["primitives"]:
            accessor = gltf["accessors"][prim["attributes"]["POSITION"]]
            for i in range(3):
                low[i] = min(low[i], accessor["min"][i] + shift[i])
                high[i] = max(high[i], accessor["max"][i] + shift[i])
    return [round(h - l, 4) for l, h in zip(low, high)]


def main():
    (assembled, a), (exploded, b) = (
        read_glb(OUT / f"{name}.glb") for name in ("assembled", "exploded")
    )
    assert a == b
    for key in ("accessors", "bufferViews", "meshes", "materials"):
        assert assembled[key] == exploded[key]
    first = {node["name"]: node for node in assembled["nodes"]}
    second = {node["name"]: node for node in exploded["nodes"]}
    assert len(first) == len(second) == 16 and first.keys() == second.keys()
    required = {
        "frame",
        "motor",
        "shaft",
        "ring_die",
        "roller_left",
        "roller_right",
        "feed_hopper",
        "service_cover",
    }
    assert required <= first.keys()
    for name, node in first.items():
        other = second[name]
        mesh = assembled["meshes"][node["mesh"]]
        assert mesh["name"] == name and node["mesh"] == other["mesh"]
        assert all(
            key not in node and key not in other
            for key in ("rotation", "scale", "matrix", "children")
        )
        assert "translation" not in node
        assert (
            math.dist(other["translation"], node["extras"]["explosion_translation_m"])
            < 1e-6
        )
        assert math.dist(other["translation"], [0, 0, 0]) > 0.05
    count = sum(
        assembled["accessors"][p["indices"]]["count"] // 3
        for m in assembled["meshes"]
        for p in m["primitives"]
    )
    print(
        json.dumps(
            {
                "named_parts": len(first),
                "materials": len(assembled["materials"]),
                "triangles": count,
                "geometry_sha256": hashlib.sha256(a).hexdigest(),
                "assembled_xyz_m": bounds(assembled),
                "exploded_xyz_m": bounds(exploded),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
