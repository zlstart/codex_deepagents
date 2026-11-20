"""Deployment preflight; all output is safe to store with the release evidence."""

import argparse
import asyncio
import json
import sqlite3
import subprocess
import sys

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool

from .config import Settings
from .harness import build_model, documentation_tools


@tool
def read_probe_value() -> str:
    """Read the workbench connection probe's current reference value."""
    return "1847"


def docker(*args: str) -> str:
    proc = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=20, check=True
    )
    return proc.stdout.strip()


async def inspect(*, live_model: bool, strict: bool) -> dict:
    settings = Settings()
    results = {}

    async def check(name, check_function):
        try:
            results[name] = {"status": "pass", "detail": await check_function()}
        except Exception as exc:  # noqa: BLE001 - one failed probe must not hide other results.
            # Provider exception payloads may include request headers or inputs.
            results[name] = {"status": "fail", "detail": type(exc).__name__}

    def storage():
        settings.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with sqlite3.connect(settings.data_dir / "studio.sqlite3", timeout=5) as con:
            if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("SQLite integrity check failed")
            con.execute("BEGIN IMMEDIATE")
            con.rollback()
        return "SQLite quick_check and write lock"

    def engine():
        version = docker("version", "--format", "{{.Server.Version}}")
        image = docker(
            "image", "inspect", "--format", "{{.Id}}", settings.sandbox_image
        )
        helper = docker(
            "image", "inspect", "--format", "{{.Id}}", settings.helper_image
        )
        return {"engine": version, "sandbox_image_id": image, "helper_image_id": helper}

    async def model():
        if not settings.llm_model or not settings.llm_api_key:
            raise ValueError("Model configuration is incomplete")
        chat = build_model(settings).bind_tools([read_probe_value])
        messages = [
            HumanMessage(content="Call read_probe_value once and return its value.")
        ]
        reply = await chat.ainvoke(messages)
        if (
            len(reply.tool_calls) != 1
            or reply.tool_calls[0]["name"] != read_probe_value.name
        ):
            raise ValueError("No tool call")
        call = reply.tool_calls[0]
        message = ToolMessage(
            content=read_probe_value.invoke(call["args"]), tool_call_id=call["id"]
        )
        final = await chat.ainvoke([*messages, reply, message])
        if "1847" not in final.text or final.tool_calls:
            raise ValueError("Tool round trip failed")
        return {"model": settings.llm_model, "tool_round_trip": True}

    async def mcp():
        return [t.name for t in await documentation_tools()]

    await check("sqlite", lambda: asyncio.to_thread(storage))
    await check("docker", lambda: asyncio.to_thread(engine))
    if "pass" == results["docker"]["status"]:
        caps = await asyncio.to_thread(
            docker,
            "info",
            "--format",
            "{{.CgroupDriver}} {{.MemoryLimit}} {{.CPUCfsQuota}} {{.PidsLimit}}",
        )
        driver, memory, cpu, pids = caps.split()
        enforced = driver != "none" and (memory, cpu, pids) == ("true", "true", "true")
        results["resource_isolation"] = {
            "status": "pass" if enforced else ("fail" if strict else "warning"),
            "detail": {"driver": driver, "memory": memory, "cpu": cpu, "pids": pids},
        }
    if live_model:
        await check("model", model)
    if settings.mcp_enabled:
        await check("langchain_mcp", mcp)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-model", action="store_true", help="Make a billed tool-calling probe."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Require working Docker CPU, memory and PID controllers.",
    )
    args = parser.parse_args()
    results = asyncio.run(inspect(live_model=args.live_model, strict=args.strict))
    print(json.dumps(results, ensure_ascii=False, indent=2))
    sys.exit(int(any(x["status"] == "fail" for x in results.values())))


if __name__ == "__main__":
    main()
