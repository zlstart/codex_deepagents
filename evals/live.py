"""Exercise a running deployment through the same authenticated API as the UI."""

import argparse
import asyncio
import hashlib
import itertools
import json
import os
import re
import secrets
import time
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .scenarios import SCENARIOS
from .validate import VALIDATORS, require

TERMINAL = {"completed", "failed", "cancelled"}
MAX_FILE_BYTES = 32 * 1024 * 1024


def utc():
    return datetime.now(UTC).isoformat(timespec="seconds")


def save_json(path, data):
    # Each file may be read while multiple cases are still executing.
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temp.replace(path)


def audit_trace(events, rid, require_subagent):
    require(
        events and all(a["seq"] < b["seq"] for a, b in itertools.pairwise(events)),
        "event cursor did not advance monotonically",
    )
    latest = {}
    for node in events:
        key = node["id"]
        if key in latest:
            previous = latest[key]
            require(
                previous["status"] not in TERMINAL,
                f"event after terminal status: {key}",
            )
            require(
                all(
                    previous.get(f) == node.get(f)
                    for f in ("kind", "label", "parent_id")
                ),
                f"node identity changed during run: {key}",
            )
        else:
            require(
                node["status"] in ("running", "queued"), f"missing initial event: {key}"
            )
            parent = node.get("parent_id")
            require(
                key == rid
                or (parent in latest and latest[parent]["status"] not in TERMINAL),
                f"missing active parent at node start: {key}",
            )
        latest[key] = node
    require(
        rid in latest and latest[rid]["status"] == "completed",
        "missing completed run event",
    )
    require(
        any(
            e["kind"] == "model" and e["status"] == "completed" for e in latest.values()
        ),
        "no completed model turn",
    )
    require(
        any(
            e["kind"] == "tool"
            and e["label"] == "execute"
            and e["status"] == "completed"
            for e in latest.values()
        ),
        "no successful shell execution",
    )
    nested = False
    depth = 0
    for node_id, node in latest.items():
        require(node["status"] in TERMINAL, f"event without terminal status: {node_id}")
        current, ancestors = node_id, set()
        while current != rid:
            require(
                current in latest and current not in ancestors,
                "missing parent or cyclic trace",
            )
            ancestors.add(current)
            current = latest[current].get("parent_id")
            if (
                current in latest
                and latest[current]["kind"] == "subagent"
                and node["kind"] == "tool"
            ):
                nested = True
        depth = max(depth, len(ancestors) + 1)
    if require_subagent:
        require(nested, "expected a tool nested beneath a subagent")
    return f"{len(events)} 条生命周期事件，{len(latest)} 个节点，最大深度 {depth} 层"


def source_names(files):
    return sorted(
        name
        for name in files
        if re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,100}\.(py|scad)", name)
    )[:8]


def pack_sources(root, files):
    archive = root / "generated_sources.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in files.items():
            bundle.writestr(name, data)
    return {
        "name": archive.name,
        "bytes": archive.stat().st_size,
        "files": list(files),
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }


async def fetch_file(client, url, name, limit):
    content = bytearray()
    async with client.stream(
        "GET", url, params={"path": name, "download": "true"}
    ) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            require(
                len(content) + len(chunk) <= limit,
                f"artifact exceeds download limit: {name}",
            )
            content.extend(chunk)
    require(content, f"empty artifact: {name}")
    return bytes(content)


async def download(client, url, name, directory):
    content = await fetch_file(client, url, name, MAX_FILE_BYTES)
    (directory / name).write_bytes(content)
    return {
        "name": name,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


async def request(client, method, path, **kwargs):
    response = await client.request(method, path, **kwargs)
    if response.is_error:
        raise RuntimeError(
            f"{method} {path} returned {response.status_code}: {response.text[:320]}"
        )
    return response.json()


async def run_case(client, scenario, output, timeout):
    root = output / scenario.key
    root.mkdir()
    report = {
        "title": scenario.title,
        "scenario": scenario.key,
        "started": utc(),
        "passed": False,
        "agent_status": "not_started",
        "artifacts": [],
        "checks": [],
        "errors": [],
    }
    events = []
    run_url = None
    started = time.monotonic()
    try:
        session = await request(
            client, "POST", "/api/sessions", json={"title": f"验收 · {scenario.title}"}
        )
        sid = session["id"]
        report["session_id"] = sid
        session_url = f"/api/sessions/{sid}"
        run = await request(
            client, "POST", f"{session_url}/runs", json={"prompt": scenario.prompt}
        )
        rid = run["id"]
        report["run_id"] = rid
        run_url = f"{session_url}/runs/{rid}"
        print(f"[{scenario.key}] run {rid}", flush=True)
        cursor = 0
        # Poll in pages. Terminal status can arrive before the last event is fetched.
        while time.monotonic() - started < timeout:
            page = await request(
                client, "GET", run_url + "/events", params={"after": cursor}
            )
            events.extend(page["events"])
            cursor = page["cursor"]
            report["agent_status"] = page["status"]
            if page["events"]:
                compact = " / ".join(
                    f"{e['label']}:{e['status']}" for e in page["events"][-3:]
                )
                print(f"[{scenario.key}] {compact}", flush=True)
            if page["status"] in TERMINAL and not page["events"]:
                break
            if len(page["events"]) < 500:
                await asyncio.sleep(1.5)
        else:
            raise TimeoutError(f"run exceeded {timeout} seconds")
        final = next(
            r
            for r in await request(client, "GET", session_url + "/runs")
            if r["id"] == rid
        )
        report["agent_error"] = final.get("error")
        if final["status"] != "completed" or not final.get("answer"):
            report["errors"].append(
                f"agent ended {final['status']}: {final.get('error')}"
            )
        else:
            try:
                report["checks"].append(
                    audit_trace(events, rid, scenario.require_subagent)
                )
            except (ValueError, KeyError) as exc:
                report["errors"].append(f"trace: {exc}")
        files = await request(client, "GET", session_url + "/files")
        names = {f["path"].removeprefix("/workspace/").lstrip("/") for f in files}
        report["listed_files"] = sorted(names)
        for name in scenario.outputs:
            try:
                require(name in names, f"{name} absent from workspace listing")
                report["artifacts"].append(
                    await download(client, session_url + "/file", name, root)
                )
            except (ValueError, httpx.HTTPError) as exc:
                report["errors"].append(f"download: {exc}")
        sources = {}
        for name in source_names(names):
            try:
                sources[name] = await fetch_file(
                    client, session_url + "/file", name, 512 * 1024
                )
            except (ValueError, httpx.HTTPError) as exc:
                report.setdefault("source_warnings", []).append(str(exc))
        if sources:
            report["sources"] = pack_sources(root, sources)
        if len(report["artifacts"]) == len(scenario.outputs):
            report["checks"].extend(
                await asyncio.to_thread(VALIDATORS[scenario.key], root)
            )
        report["passed"] = not report["errors"]
    except Exception as exc:  # noqa: BLE001 - record per-case failures without losing sibling results
        report["errors"].append(f"{type(exc).__name__}: {str(exc)[:1400]}")
    finally:
        if run_url and report["agent_status"] not in TERMINAL:
            try:
                await request(client, "POST", run_url + "/cancel")
            except Exception as exc:  # noqa: BLE001 - record per-case failures without losing sibling results
                report["errors"].append(f"cleanup: {type(exc).__name__}: {exc}")
        # Persist public trace topology and failed-tool diagnostics. Successful shell input and
        # output may be large and can contain workspace contents; artifacts carry their own hashes.
        trace = [
            {
                k: e.get(k)
                for k in (
                    "seq",
                    "id",
                    "parent_id",
                    "kind",
                    "label",
                    "status",
                    "created_at",
                )
            }
            | ({"detail": e.get("detail", "")} if e["status"] == "failed" else {})
            for e in events
        ]
        latest = {e["id"]: e for e in trace}
        report["node_counts"] = dict(Counter(e["kind"] for e in latest.values()))
        report["failed_steps"] = [e for e in trace if e["status"] == "failed"]
        report["duration_seconds"] = round(time.monotonic() - started, 2)
        report["finished"] = utc()
        save_json(root / "trace.json", trace)
        save_json(root / "result.json", report)
        print(
            f"[{scenario.key}] {'PASS' if report['passed'] else 'FAIL'} {report['errors']}",
            flush=True,
        )
    return report


def write_report(output, results):
    lines = [
        "# FAMSUN Agent 真实任务验收",
        "",
        (
            "通过部署服务的登录、会话、运行、事件和文件 HTTP 接口执行。"
            "数值与几何校验在独立的验收进程中读取下载文件完成。时间使用 UTC。"
        ),
        "",
        "| 任务 | 验收 | Agent | 耗时（秒） | 失败步骤 |",
        "|---|---|---|---:|---:|",
    ]
    for r in results:
        lines.append(
            f"| [{r['title']}]({r['scenario']}/result.json) | "
            f"{'通过' if r['passed'] else '失败'} | {r['agent_status']} | "
            f"{r['duration_seconds']} | {len(r['failed_steps'])} |"
        )
    for r in results:
        lines.extend(
            [
                "",
                f"## {r['title']}",
                "",
                (
                    f"运行 `{r.get('run_id', '未创建')}`，开始于 {r['started']}。"
                    f" [逐步轨迹]({r['scenario']}/trace.json)"
                ),
                "",
            ]
        )
        lines.extend(f"- {check}" for check in r["checks"])
        lines.extend(f"- 验收失败：{err}" for err in r["errors"])
        lines.extend(
            f"- 运行中失败步骤：{e['kind']} / {e['label']}（序号 {e['seq']}，详见轨迹）"
            for e in r["failed_steps"]
        )
        if sources := r.get("sources"):
            lines.append(
                f"- [生成代码]({r['scenario']}/{sources['name']}) · "
                + "、".join(sources["files"])
            )
        lines.extend(
            f"- [{a['name']}]({r['scenario']}/{a['name']}) · {a['bytes']} bytes"
            for a in r["artifacts"]
        )
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main(args):
    output = args.output
    # Keep each execution immutable, including partial failures and previous runs.
    output.mkdir(parents=True, exist_ok=False)
    selected = [
        scenario for scenario in SCENARIOS if not args.case or scenario.key in args.case
    ]
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=60, trust_env=False
    ) as client:
        name = os.environ.get("FAMSUN_EVAL_USERNAME")
        password = os.environ.get("FAMSUN_EVAL_PASSWORD")
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
                    "username": "acceptance_" + secrets.token_hex(6),
                    "password": secrets.token_urlsafe(32),
                },
            )
        sem = asyncio.Semaphore(args.parallel)

        async def limited(scenario):
            async with sem:
                return await run_case(client, scenario, output, args.timeout)

        results = await asyncio.gather(*(limited(case) for case in selected))
    write_report(output, results)
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--output", type=Path, required=True, help="new directory for evidence"
    )
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--parallel", type=int, choices=range(1, 9), default=2)
    parser.add_argument("--case", choices=[s.key for s in SCENARIOS], action="append")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
