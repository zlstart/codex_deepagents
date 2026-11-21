"""Run five short general-workspace journeys through a real deployment and SSE."""

import argparse
import asyncio
import csv
import hashlib
import io
import json
import re
import secrets
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import httpx

from .live import TERMINAL, fetch_file, save_json, utc


@dataclass(frozen=True)
class Case:
    title: str
    prompt: str
    file: str | None = None


CASES = {
    "direct": Case(
        "直接问答",
        "7 个箱子，每箱 8 本书，共多少本？直接回答数字和单位，无需工具或文件。",
    ),
    "note": Case(
        "文本备忘",
        "用 write_file 将单句“周五前确认交货日期。”原样写入 /workspace/memo.txt，"
        "用 read_file 读回核对后，回复文件名即可。",
        "memo.txt",
    ),
    "csv": Case(
        "CSV 汇总",
        "用一次 execute 按渠道合计工单：email 4、web 7、email 9、web 3。"
        "写入 /workspace/summary.csv，表头 channel,count，每渠道一行。回复文件名。",
        "summary.csv",
    ),
    "actions": Case(
        "会议行动清单",
        "把会议决定写入 /workspace/actions.md，各一行 Markdown 未勾选任务，含负责人和截止日期。"
        "采购组于 2026-12-02 确认报价；物流组于 2026-12-04 核对运单。只需交付清单。",
        "actions.md",
    ),
    "review": Case(
        "独立复核",
        "用 write_file 将单行 18 + 24 = 42 写入 /workspace/check.txt。"
        "必须委派 reviewer 子智能体，用 read_file 独立读取文件核算，再简短回复复核结论。",
        "check.txt",
    ),
}


def validate_file(key, content):
    text = content.decode("utf-8-sig").strip()
    if key == "note":
        return text == "周五前确认交货日期。"
    if key == "csv":
        source = csv.DictReader(io.StringIO(text))
        rows = list(source)
        return (
            source.fieldnames == ["channel", "count"]
            and len(rows) == 2
            and {r["channel"]: int(r["count"]) for r in rows}
            == {"email": 13, "web": 10}
        )
    if key == "actions":
        tasks = [
            line for line in text.splitlines() if re.match(r"^\s*[-*]\s+\[ \]\s+", line)
        ]
        return len(tasks) == 2 and all(
            any(all(term in line for term in terms) for line in tasks)
            for terms in [
                ("采购", "报价", "2026-12-02"),
                ("物流", "运单", "2026-12-04"),
            ]
        )
    return key == "review" and re.sub(r"\s+", "", text) == "18+24=42"


def check_trace(events, rid, key):
    latest = {}
    inputs, outputs, deltas, mcp = Counter(), Counter(), [], 0
    for event in events:
        label, kind = event["label"], event["kind"]
        latest[event["id"]] = event
        payload = event.get("payload") or {}
        if (
            payload.get("type") == "model_text"
            and isinstance(payload.get("delta"), str)
            and payload["delta"]
        ):
            deltas.append(payload["delta"])
        if kind in ("tool", "subagent"):
            if event["status"] == "running":
                mcp += int(
                    any(
                        s in label.lower()
                        for s in ("mcp", "langchain", "query_docs", "search_docs")
                    )
                )
                if (
                    payload.get("type") == "tool"
                    and isinstance(payload.get("arguments"), dict)
                    and payload["arguments"]
                ):
                    inputs[label] += 1
            if (
                event["status"] in TERMINAL
                and payload.get("type") == "tool"
                and "result" in payload
            ):
                outputs[label] += 1
    valid_tree = (
        bool(events)
        and rid in latest
        and all(node["status"] in TERMINAL for node in latest.values())
    )
    depth, nested_read = 0, False
    for node_id, node in latest.items():
        parent, lineage = node_id, []
        while parent:
            if parent in lineage or parent not in latest:
                valid_tree = False
                break
            lineage.append(parent)
            parent = latest[parent].get("parent_id")
        if lineage[-1:] != [rid]:
            valid_tree = False
        payload = node.get("payload") or {}
        readback = json.dumps(payload.get("result"), ensure_ascii=False)
        if (
            node["label"] == "read_file"
            and node["status"] == "completed"
            and "check.txt" in json.dumps(payload.get("arguments"))
            and "18+24=42" in re.sub(r"\s+", "", readback)
        ):
            nested_read |= any(
                latest[p]["kind"] == "subagent" and latest[p]["label"] == "reviewer"
                for p in lineage[1:]
            )
        depth = max(depth, len(lineage))
    tool_labels = Counter(
        node["label"]
        for node in latest.values()
        if node["kind"] in ("tool", "subagent")
    )
    failed_tools = Counter(
        node["label"]
        for node in latest.values()
        if node["kind"] in ("tool", "subagent") and node["status"] != "completed"
    )
    paired = inputs & outputs
    expected_tools = {
        "note": {"write_file", "read_file"},
        "csv": {"execute"},
        "review": {"write_file", "read_file", "reviewer"},
    }
    checks = {
        "tree_and_terminal_states": valid_tree,
        "has_public_model_delta": bool(deltas),
        "no_docs_mcp": mcp == 0,
        "all_tools_completed": not failed_tools,
    }
    if key == "direct":
        checks["direct_without_tools"] = not tool_labels
    else:
        checks["structured_arguments_and_results"] = (
            bool(paired) and expected_tools.get(key, set()) <= paired.keys()
        )
    if key == "review":
        checks["reviewer_read_in_child_branch"] = nested_read
    return checks, {
        "events": len(events),
        "nodes": len(latest),
        "maximum_depth": depth,
        "model_chunks": len(deltas),
        "model_text_characters": sum(map(len, deltas)),
        "docs_mcp_invocations": mcp,
        "tool_counts": dict(tool_labels),
        "structured_argument_counts": dict(inputs),
        "structured_result_counts": dict(outputs),
        "unsuccessful_tools": dict(failed_tools),
    }


async def trace_stream(client, path):
    recorded, last, terminal = [], 0, None
    async with client.stream(
        "GET", path + "/stream", timeout=httpx.Timeout(30, read=35)
    ) as response:
        response.raise_for_status()
        if not response.headers.get("content-type", "").startswith("text/event-stream"):
            raise ValueError("expected event stream")
        event, seq, data = None, None, None
        async for line in response.aiter_lines():
            if not line:
                if event == "activity":
                    if data is None or seq is None or seq <= last or data["seq"] != seq:
                        raise ValueError("invalid activity cursor")
                    recorded.append(data)
                    last = seq
                if event == "end":
                    terminal = data
                event, seq, data = None, None, None
            elif line.startswith("id:"):
                seq = int(line[3:].strip())
            elif line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:])
    if not terminal or terminal["cursor"] != last:
        raise ValueError("stream ended without a durable final cursor")
    return recorded, terminal


async def json_request(client, method, path, **kwargs):
    response = await client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


async def run_case(client, key, timeout):
    case = CASES[key]
    evidence = {"started": utc(), "title": case.title, "passed": False, "checks": {}}
    started = time.monotonic()
    path = None
    finished = False
    try:
        async with asyncio.timeout(timeout):
            session = await json_request(
                client,
                "POST",
                "/api/sessions",
                json={"title": "工作台验收 · " + case.title},
            )
            base = "/api/sessions/" + session["id"]
            run = await json_request(
                client,
                "POST",
                base + "/runs",
                json={"prompt": case.prompt},
                headers={"Idempotency-Key": secrets.token_hex(16)},
            )
            rid = run["id"]
            path = f"{base}/runs/{rid}"
            events, terminal = await trace_stream(client, path)
            finished = True
            record = await json_request(client, "GET", path)
            checks, summary = check_trace(events, rid, key)
            cursor, offset, identical = 0, 0, True
            while True:
                page = await json_request(
                    client, "GET", path + "/events", params={"after": cursor}
                )
                rows = page["events"]
                identical &= rows == events[offset : offset + len(rows)]
                offset += len(rows)
                cursor = page["cursor"]
                if not page["has_more"]:
                    break
            checks["sse_matches_sqlite_replay"] = (
                identical and offset == len(events) and cursor == terminal["cursor"]
            )
            checks["run_completed"] = (
                record["status"] == terminal["status"] == "completed"
            )
            answer = record.get("answer") or ""
            checks["answer_present"] = bool(answer.strip())
            evidence.update(status=record["status"], trace=summary, checks=checks)
            if case.file:
                listed = await json_request(client, "GET", base + "/files")
                checks["file_listed"] = any(
                    item["path"] == case.file for item in listed
                )
                if checks["file_listed"]:
                    data = await fetch_file(
                        client, base + "/file", case.file, 64 * 1024
                    )
                    checks["independent_file_check"] = validate_file(key, data)
                    evidence["artifact"] = {
                        "file": case.file,
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                else:
                    checks["independent_file_check"] = False
            else:
                checks["answer_value_and_unit"] = bool(re.search(r"56\s*本", answer))
            evidence["passed"] = all(checks.values())
    except httpx.HTTPStatusError as exc:
        evidence["error"] = f"HTTP {exc.response.status_code}"
    except (
        OSError,
        TimeoutError,
        ValueError,
        KeyError,
        RuntimeError,
        httpx.HTTPError,
        AssertionError,
    ) as exc:
        # Third-party HTTP details or trace payloads never enter the evidence file.
        evidence["error"] = type(exc).__name__
    finally:
        if path and not finished:
            try:
                await client.post(path + "/cancel", timeout=60)
            except httpx.HTTPError:
                evidence["cancel"] = "unconfirmed"
        evidence["finished"] = utc()
        evidence["duration_seconds"] = round(time.monotonic() - started, 2)
    return evidence


async def run(args):
    result_path = args.output
    result_path.parent.mkdir(parents=True, exist_ok=True)
    report = (
        json.loads(result_path.read_text())
        if result_path.exists()
        else {
            "suite": "general-workspace",
            "transport": "authenticated HTTP + SSE",
            "cases": {},
        }
    )
    if report["suite"] != "general-workspace":
        raise ValueError("unexpected existing evidence file")
    selected = args.case or list(CASES)
    parallel = asyncio.Semaphore(args.parallel)
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"), timeout=30, trust_env=False
    ) as client:
        # Credentials live only in this process. The report contains synthetic
        # task metadata, counts, validations and hashes, never accounts or cookies.
        await json_request(
            client,
            "POST",
            "/api/auth/register",
            json={
                "username": "workspace_" + secrets.token_hex(6),
                "password": secrets.token_urlsafe(32),
            },
        )

        async def check(key):
            async with parallel:
                print(f"Starting {key}", flush=True)
                result = await run_case(client, key, args.timeout)
                report["cases"].setdefault(key, []).append(result)
                report["passed_cases"] = sum(
                    bool(records[-1]["passed"]) for records in report["cases"].values()
                )
                report["total_cases"] = len(CASES)
                save_json(result_path, report)
                print(
                    f"{key}: {'PASS' if result['passed'] else 'FAIL'} ({result['duration_seconds']}s)",
                    flush=True,
                )
                return result["passed"]

        try:
            return all(await asyncio.gather(*(check(key) for key in selected)))
        finally:
            await client.post("/api/auth/logout")


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--base-url", required=True)
    cli.add_argument(
        "--output", type=Path, default=Path("docs/testing/general-workspace.json")
    )
    cli.add_argument("--case", action="append", choices=sorted(CASES))
    cli.add_argument("--parallel", type=int, choices=(1, 2), default=2)
    cli.add_argument(
        "--timeout",
        type=float,
        default=360,
        help="Maximum seconds per task, including independent artifact checks",
    )
    args = cli.parse_args()
    raise SystemExit(0 if asyncio.run(run(args)) else 1)


if __name__ == "__main__":
    main()
