"""Verify an actual MCP search, SQLite conversation recall and workspace persistence."""

import argparse
import asyncio
import json
import os
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .live import TERMINAL, request


async def turn(client, session, prompt):
    run = await request(client, "POST", f"{session}/runs", json={"prompt": prompt})
    run_url = f"{session}/runs/{run['id']}"
    events = []
    cursor = 0
    start = time.monotonic()
    try:
        async with asyncio.timeout(300):
            while True:
                page = await request(
                    client, "GET", f"{run_url}/events", params={"after": cursor}
                )
                events.extend(page["events"])
                cursor = page["cursor"]
                if page["status"] in TERMINAL and not page["events"]:
                    break
                await asyncio.sleep(1)
    except TimeoutError:
        await request(client, "POST", f"{run_url}/cancel")
        raise
    final = next(
        r
        for r in await request(client, "GET", f"{session}/runs")
        if r["id"] == run["id"]
    )
    latest = {e["id"]: e for e in events}
    return {
        "run_id": run["id"],
        "status": final["status"],
        "answer": final["answer"],
        "error": final.get("error"),
        "elapsed_seconds": round(time.monotonic() - start, 2),
        "nodes": [
            {k: e.get(k) for k in ("id", "parent_id", "kind", "label", "status")}
            for e in latest.values()
        ],
    }


async def main(url, output):
    name = os.getenv("FAMSUN_EVAL_USERNAME")
    password = os.getenv("FAMSUN_EVAL_PASSWORD")
    nonce = secrets.token_hex(6)
    marker = f"FAMSUN-{nonce}"
    report = {
        "started_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "turns": [],
        "checks": {},
    }
    async with httpx.AsyncClient(base_url=url, timeout=45, trust_env=False) as client:
        if name and password:
            await request(
                client,
                "POST",
                "/api/auth/login",
                json={"username": name, "password": password},
            )
        elif name or password:
            raise ValueError("set both FAMSUN_EVAL_USERNAME and FAMSUN_EVAL_PASSWORD")
        else:
            await request(
                client,
                "POST",
                "/api/auth/register",
                json={
                    "username": "mcp_eval_" + nonce,
                    "password": secrets.token_urlsafe(32),
                },
            )
        session = await request(
            client,
            "POST",
            "/api/sessions",
            json={"title": "验收 · 官方文档与上下文恢复"},
        )
        path = f"/api/sessions/{session['id']}"
        report["session_id"] = session["id"]
        first = await turn(
            client,
            path,
            f"记住这个仅在对话中使用的校验码 {marker}，不要把它写入文件。"
            "调用 LangChain 官方文档 MCP 的 search_docs_by_lang_chain 查找 Deep Agents Python 的 skills 配置，"
            "从结果选一个官方文档 URL，调用文件工具把 URL 写入 /workspace/docs.txt。简短回复 URL。",
        )
        report["turns"].append(first)
        second = await turn(
            client,
            path,
            "请从本会话上一轮记忆中取回校验码，并用 read_file 读取 /workspace/docs.txt。"
            "只回复校验码和文件里的 URL。",
        )
        report["turns"].append(second)
        file = await client.get(f"{path}/file", params={"path": "docs.txt"})
        docs_text = file.text.strip()

        def used(result, label):
            return any(
                n["label"] == label and n["status"] == "completed"
                for n in result["nodes"]
            )

        report["checks"] = {
            "both_runs_completed": all(
                t["status"] == "completed" for t in report["turns"]
            ),
            "official_mcp_discovered": used(first, "LangChain 文档 MCP"),
            "official_search_tool_called": used(first, "search_docs_by_lang_chain"),
            "private_marker_recalled": marker in (second["answer"] or ""),
            "old_file_read_in_new_run": used(second, "read_file"),
            "download_contains_official_url_only": file.is_success
            and docs_text.startswith("https://docs.langchain.com/")
            and marker not in docs_text
            and docs_text in (second["answer"] or ""),
            "all_nodes_succeeded": all(
                n["status"] == "completed" for t in report["turns"] for n in t["nodes"]
            ),
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["checks"], ensure_ascii=False))
    return 0 if all(report["checks"].values()) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.base_url, args.output)))
