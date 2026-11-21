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


# Browser-controlled barriers make it possible to assert the visible UI before
# a second chunk, a tool result, or the final answer exists in SQLite yet.
GATES = {}


async def streamed_report(run_id, emit, sandbox):
    gates = GATES.setdefault(
        run_id, {k: asyncio.Event() for k in ("delta", "tool", "finish")}
    )

    def node(key, parent, kind, label, status, payload=None):
        return {
            "id": key,
            "parent_id": parent,
            "kind": kind,
            "label": label,
            "status": status,
            "payload": payload,
        }

    async def text(key, delta, status="running"):
        await emit(
            node(
                key,
                "main",
                "model",
                "模型响应",
                status,
                {"type": "model_text", "delta": delta},
            )
        )

    try:
        await emit(node("main", run_id, "agent", "FAMSUN Agent", "running"))
        await emit(node("introduction", "main", "model", "模型响应", "running"))
        await gates["delta"].wait()
        await text("introduction", "先核对订单明细，")
        await gates["tool"].wait()
        await text("introduction", "再汇总每个渠道的销售额。")
        await emit(node("introduction", "main", "model", "模型响应", "completed"))
        await emit(node("review", "main", "subagent", "数据复核", "running"))
        arguments = {"command": "python verify_orders.py orders.csv"}
        await emit(
            node(
                "verify",
                "review",
                "tool",
                "execute",
                "running",
                {"type": "tool", "arguments": arguments},
            )
        )
        await emit(
            node(
                "verify",
                "review",
                "tool",
                "execute",
                "completed",
                {
                    "type": "tool",
                    "arguments": arguments,
                    "result": "3 orders, total=150",
                    "exit_code": 0,
                },
            )
        )
        await emit(node("review", "main", "subagent", "数据复核", "completed"))
        await gates["finish"].wait()
        args = {
            "file_path": "/workspace/sales-summary.md",
            "content": "# 销售汇总\n合计 150 元。",
        }
        await emit(
            node(
                "report",
                "main",
                "tool",
                "write_file",
                "running",
                {"type": "tool", "arguments": args},
            )
        )
        sandbox.files["sales-summary.md"] = (args["content"].encode(), "text/markdown")
        await emit(
            node(
                "report",
                "main",
                "tool",
                "write_file",
                "completed",
                {"type": "tool", "arguments": args, "result": "Wrote sales-summary.md"},
            )
        )
        answer = "三个渠道销售合计 150 元。"
        await emit(node("final", "main", "model", "模型响应", "running"))
        await text("final", answer)
        await emit(node("final", "main", "model", "模型响应", "completed"))
        await emit(node("main", run_id, "agent", "FAMSUN Agent", "completed"))
        return answer
    finally:
        GATES.pop(run_id, None)


async def runner(*, prompt, sandbox, emit, run_id, **kwargs):
    if prompt == "检查失败的工具调用":
        for index, name, code, output in [
            (1, "missing.csv", 2, "missing.csv: not found"),
            (2, "orders.csv", 0, "rows=4"),
        ]:
            item = {
                "id": f"exec-{index}",
                "parent_id": run_id,
                "kind": "tool",
                "label": "execute",
            }
            payload = {
                "type": "tool",
                "arguments": {"command": f"python check_csv.py {name}"},
            }
            await emit({**item, "status": "running", "payload": payload})
            await emit(
                {
                    **item,
                    "status": "failed" if code else "completed",
                    "payload": {**payload, "result": output, "exit_code": code},
                }
            )
        return "改用 orders.csv 后，确认有四行订单。"
    if prompt == "分步汇总渠道订单":
        return await streamed_report(run_id, emit, sandbox)
    if prompt == "预览已归档的制粒机验收模型" and os.environ.get("FAMSUN_E2E_MODEL"):
        path = Path(os.environ["FAMSUN_E2E_MODEL"])
        sandbox.files["pellet.glb"] = (path.read_bytes(), "model/gltf-binary")
        trace_path = path.with_name("trace.json")
        if trace_path.is_file():
            events = json.loads(trace_path.read_text(encoding="utf-8"))
            original_run = next(
                event["id"] for event in events if event["kind"] == "run"
            )
            for event in events:
                if event["kind"] == "run":
                    continue
                record = {
                    key: event.get(key)
                    for key in ("id", "parent_id", "kind", "label", "detail", "status")
                }
                if record["parent_id"] == original_run:
                    record["parent_id"] = run_id
                await emit(record)
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
    sandbox.files["summary.md"] = (
        (
            "# 周度摘要\n\n| 渠道 | 金额 |\n| --- | --- |\n| 门店 | 150 |\n\n"
            "![外部配图](https://external.invalid/markdown.png)\n\n"
            '<img src="https://external.invalid/raw.png">\n'
            "<script>window.untrusted = true</script>\n"
        ).encode(),
        "text/markdown",
    )
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

        @app.post("/fixture/{run_id}/{phase}")
        async def advance(run_id: str, phase: str):
            gates = GATES.setdefault(
                run_id, {k: asyncio.Event() for k in ("delta", "tool", "finish")}
            )
            gates[phase].set()
            return {"phase": phase}

        # The production app ends with a catch-all static mount. The fixture-only
        # barriers must precede that mount to receive POST requests.
        app.router.routes.insert(0, app.router.routes.pop())

        uvicorn.run(
            app, host="127.0.0.1", port=int(os.getenv("FAMSUN_FIXTURE_PORT", "8176"))
        )
