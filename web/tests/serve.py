"""UI integration fixture. Real auth, SQLite, HTTP and static build, scripted runner."""

import asyncio
import json
import os
import struct
import tempfile
from pathlib import Path

import uvicorn

from famsun.api import create_app
from famsun.config import Settings


def two_parts(*, external_resource=False):
    binary = struct.pack("<9f", -1, 0, 0, 1, 0, 0, 0, 1, 0)
    doc = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0, 1]}],
        "nodes": [{"mesh": 0}, {"mesh": 0, "translation": [0, 0, 1]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteLength": len(binary)}],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 3,
                "type": "VEC3",
                "min": [-1, 0, 0],
                "max": [1, 1, 0],
            }
        ],
    }
    if external_resource:
        doc["buffers"][0]["uri"] = "https://external.invalid/mesh.bin"
    raw = json.dumps(doc).encode()
    raw += b" " * (-len(raw) % 4)
    return (
        struct.pack("<4sII", b"glTF", 2, 28 + len(raw) + len(binary))
        + struct.pack("<I4s", len(raw), b"JSON")
        + raw
        + struct.pack("<I4s", len(binary), b"BIN\0")
        + binary
    )


class Workspace:
    def __init__(self, files):
        self.files = files

    def start(self):
        pass

    def stop(self):
        pass

    def list_artifacts(self):
        return [
            {
                "path": k,
                "name": k,
                "size": len(b),
                "mime_type": m,
                "modified_at": "2026-01-01T12:00:00Z",
            }
            for k, (b, m) in self.files.items()
        ]

    def read_artifact(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path][0]


async def runner(*, prompt, sandbox, emit, **kwargs):
    if prompt == "预览已归档的制粒机验收模型" and os.environ.get("FAMSUN_E2E_MODEL"):
        path = Path(os.environ["FAMSUN_E2E_MODEL"])
        sandbox.files["pellet.glb"] = (path.read_bytes(), "model/gltf-binary")
        return "[pellet.glb](pellet.glb)"
    nodes = [
        ("root", None, "agent", "工程智能体"),
        ("sub", "root", "subagent", "建模子智能体"),
        ("tool", "sub", "tool", "执行建模工具"),
    ]
    for key, parent, kind, label in nodes:
        await emit(
            {
                "id": key,
                "parent_id": parent,
                "kind": kind,
                "label": label,
                "status": "running",
                "detail": "构建两个独立几何网格" if kind == "tool" else "",
            }
        )
    if prompt == "等待取消":
        await asyncio.sleep(60)
    if prompt == "测试失败":
        raise ValueError("test failure")
    sandbox.files["assembly.glb"] = (two_parts(), "model/gltf-binary")
    sandbox.files["external.glb"] = (
        two_parts(external_resource=True),
        "model/gltf-binary",
    )
    sandbox.files["results.csv"] = (b'name,value\r\n"torque, Nm",10.2\r\n', "text/csv")
    sandbox.files["notes.html"] = (
        b"<script>window.untrusted = true</script>",
        "text/html",
    )
    for key, parent, kind, label in reversed(nodes):
        await emit(
            {
                "id": key,
                "parent_id": parent,
                "kind": kind,
                "label": label,
                "status": "completed",
                "detail": "已生成几何网格" if kind == "tool" else "",
            }
        )
    return "生成完成：[assembly.glb](assembly.glb)"


if __name__ == "__main__":
    sessions = {}
    with tempfile.TemporaryDirectory(prefix="famsun-browser-") as temp:
        settings = Settings(
            data_dir=Path(temp),
            static_dir=Path("web/dist").resolve(),
            allow_signup=True,
            secure_cookies=False,
        )
        app = create_app(
            settings,
            runner=runner,
            sandbox_factory=lambda user_id, session_id, settings: Workspace(
                sessions.setdefault((user_id, session_id), {})
            ),
        )
        uvicorn.run(app, host="127.0.0.1", port=8176)
