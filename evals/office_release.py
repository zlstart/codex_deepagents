"""Run the Office demo against a real service and retain a private browser login."""

import argparse
import asyncio
import hashlib
import io
import json
import os
import re
import secrets
import subprocess
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx

from .live import fetch_file, save_json, utc
from .workspace_smoke import json_request, trace_stream

PROMPT = """请在 /workspace 为星桥服务中心制作一组简洁正式的中文《客户服务周报》。本周数据为邮件受理12办结10、网页受理18办结16、电话受理10办结9，合计受理40、办结35。
请用同一份数据一次生成四份可打开的成品，文件名固定：
briefing.pptx 三页宽屏，封面、本周渠道表现、下周行动；brief.docx 一份中文摘要，含统计表和行动建议；channels.xlsx 两张表，依次命名「渠道明细」「汇总」，汇总表写入数值40和35；report.html 一个自包含静态中文报告，有标题与表格，断网可看。版面使用墨绿、白色与清晰的中文字体。可以用一个 Python 脚本生成全部，若欠缺库可用 uv pip install python-docx python-pptx openpyxl。最后重新打开检查四个文件、幻灯片及工作表数量；确认后简短回复并结束。"""
FILES = ("briefing.pptx", "brief.docx", "channels.xlsx", "report.html")


def private_ticket(data):
    folder = Path(tempfile.mkdtemp(prefix="famsun-office-qa-"))
    path = folder / "login.json"
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_EXCL | os.O_CREAT, 0o600), "w"
    ) as fp:
        json.dump(data, fp)
    return path


async def start(base):
    credentials = {
        "username": "office_" + secrets.token_hex(8),
        "password": secrets.token_urlsafe(30),
    }
    async with httpx.AsyncClient(base_url=base, timeout=45) as client:
        user = await json_request(
            client, "POST", "/api/auth/register", json=credentials
        )
        session = await json_request(
            client, "POST", "/api/sessions", json={"title": "客户服务周报 · 文档预览"}
        )
        url = "/api/sessions/" + session["id"]
        run = await json_request(
            client,
            "POST",
            url + "/runs",
            json={"prompt": PROMPT},
            headers={"Idempotency-Key": secrets.token_hex(16)},
        )
    ticket = private_ticket(
        dict(
            credentials,
            base_url=base,
            user_id=user["id"],
            session_id=session["id"],
            run_id=run["id"],
        )
    )
    print(f"Private browser login and retained QA session: {ticket}", flush=True)


class ReportHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = set()
        self.text = []
        self.external = False

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        for key, value in attrs:
            if key in {"href", "src"} and (value or "").startswith(
                ("http:", "https:", "//")
            ):
                self.external = True

    def handle_data(self, data):
        self.text.append(data)


def inspect_file(name, content):
    labels = ["邮件", "网页", "电话"]
    if name.endswith(".html"):
        parser = ReportHTML()
        parser.feed(content.decode("utf-8-sig"))
        text = "".join(parser.text)
        return {
            "heading_and_table": bool({"h1", "h2"} & parser.tags)
            and "table" in parser.tags,
            "channels_and_totals": all(word in text for word in (*labels, "40", "35")),
            "standalone_static": not parser.external and "script" not in parser.tags,
        }
    with zipfile.ZipFile(io.BytesIO(content)) as doc:
        assert sum(entry.file_size for entry in doc.infolist()) < 64 * 1024 * 1024
        if name.endswith(".pptx"):
            slides = [
                entry
                for entry in doc.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", entry)
            ]
            text = "".join(
                "".join(ET.fromstring(doc.read(slide)).itertext()) for slide in slides
            )
            return {
                "three_slides": len(slides) == 3,
                "chinese_weekly_report": "周报" in text,
                "all_channels": all(word in text for word in labels),
            }
        if name.endswith(".docx"):
            body = ET.fromstring(doc.read("word/document.xml"))
            text = "".join(body.itertext())
            return {
                "has_table": body.find(".//{*}tbl") is not None,
                "chinese_report_and_channels": all(
                    word in text for word in ("周报", *labels)
                ),
                "data_totals": "40" in text and "35" in text,
            }
        workbook = ET.fromstring(doc.read("xl/workbook.xml"))
        return {
            "sheet_names": [s.attrib["name"] for s in workbook.findall(".//{*}sheet")]
            == ["渠道明细", "汇总"]
        }


async def verify_preview(client, session_path, name):
    url = session_path + "/preview"
    if name.endswith(".html"):
        resp = await client.get(session_path + "/file", params={"path": name})
        resp.raise_for_status()
        return {
            "downloadable_html": resp.headers.get("content-type", "").startswith(
                "text/html"
            )
        }
    if name.endswith(".xlsx"):
        sheets = []
        for sheet in (0, 1):
            data = await json_request(
                client,
                "GET",
                url,
                params={"path": name, "sheet": sheet, "view": "sheet"},
            )
            sheets.append({str(v) for row in data["rows"] for v in row})
        return {
            "detail_values": {"邮件", "网页", "电话", "12", "10", "18", "16", "9"}
            <= sheets[0],
            "totals_on_second_sheet": {"40", "35"} <= sheets[1],
        }
    pdf = await client.get(url, params={"path": name})
    pdf.raise_for_status()
    result = await asyncio.to_thread(
        subprocess.run,
        ["pdfinfo", "-"],
        input=pdf.content,
        capture_output=True,
        timeout=20,
        check=True,
    )
    pages = int(re.search(rb"(?m)^Pages:\s+(\d+)", result.stdout).group(1))
    return {
        "pdf_response": pdf.headers.get("content-type", "").startswith(
            "application/pdf"
        ),
        "renders_expected_pages": pages == 3 if name.endswith(".pptx") else pages >= 1,
        "page_count": pages,
    }


def failed_tool(event):
    payload = event.get("payload") or {}
    text = str(payload.get("result", "")).lower()
    if "modulenotfounderror" in text:
        diagnosis = "package unavailable during import check"
    elif "read-only file system" in text and "failed to install" in text:
        diagnosis = "installation targeted read-only system Python"
    elif "ooxml" in text and ("[pass]" in text or "[fail]" in text):
        diagnosis = "agent's document validation command exited nonzero"
    else:
        diagnosis = "tool returned an error"
    return {
        "tool": event["label"],
        "exit_code": payload.get("exit_code"),
        "diagnosis": diagnosis,
    }


async def verify_office(client, ticket):
    session_path = "/api/sessions/" + ticket["session_id"]
    run_path = session_path + "/runs/" + ticket["run_id"]
    async with asyncio.timeout(1250):
        events, terminal = await trace_stream(client, run_path)
    listed = {
        f["path"] for f in await json_request(client, "GET", session_path + "/files")
    }
    tools = [e for e in events if e["kind"] == "tool"]
    result = {
        "status": terminal["status"],
        "trace_events": len(events),
        "successful_tools": sorted(
            {e["label"] for e in tools if e["status"] == "completed"}
        ),
        "tool_failures": [failed_tool(e) for e in tools if e["status"] == "failed"],
        "artifacts": {},
    }
    for name in FILES:
        info = result["artifacts"][name] = {"listed": name in listed, "passed": False}
        if name not in listed:
            continue
        try:
            content = await fetch_file(
                client, session_path + "/file", name, 8 * 1024 * 1024
            )
            info.update(
                bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                structure=inspect_file(name, content),
            )
            info["preview"] = await verify_preview(client, session_path, name)
            info["passed"] = all(info["structure"].values()) and all(
                info["preview"].values()
            )
        except (
            httpx.HTTPError,
            ValueError,
            zipfile.BadZipFile,
            KeyError,
            AssertionError,
            ET.ParseError,
            OSError,
            subprocess.SubprocessError,
        ) as exc:
            info["error"] = type(exc).__name__
            if isinstance(exc, httpx.HTTPStatusError):
                info["http_status"] = exc.response.status_code
    result["passed"] = (
        result["status"] == "completed"
        and bool(result["successful_tools"])
        and all(info["passed"] for info in result["artifacts"].values())
    )
    return result


async def finish(args):
    ticket = json.loads(args.ticket.read_text())
    key = hashlib.sha256(ticket["run_id"].encode()).hexdigest()
    report = json.loads(args.output.read_text()) if args.output.exists() else {}
    if report.get("qa_run_sha256") not in (None, key):
        raise ValueError("Choose a separate output for this new QA run")
    if args.delete and (report.get("qa_run_sha256") != key or "office" not in report):
        raise ValueError("Verify the retained Office session into this output first")
    report["qa_run_sha256"] = key
    async with httpx.AsyncClient(base_url=ticket["base_url"], timeout=120) as client:
        user = await json_request(
            client,
            "POST",
            "/api/auth/login",
            json={k: ticket[k] for k in ("username", "password")},
        )
        if ticket["user_id"] != user["id"]:
            raise ValueError("Unexpected QA user")
        if args.delete:
            from .session_delete_live import verify_session_delete

            report["session_deletion"] = await verify_session_delete(
                client,
                user["id"],
                docker_container=args.docker_container,
                retained_session_id=ticket["session_id"],
            )
            deletion = report["session_deletion"]
            if deletion["passed"]:
                artifact = "report.html"
                try:
                    content = await fetch_file(
                        client,
                        "/api/sessions/" + ticket["session_id"] + "/file",
                        artifact,
                        8 * 1024 * 1024,
                    )
                    retained = (
                        hashlib.sha256(content).hexdigest()
                        == report["office"]["artifacts"][artifact]["sha256"]
                    )
                except (KeyError, ValueError, httpx.HTTPError):
                    retained = False
                deletion["checks"]["retained_report_hash_unchanged"] = retained
                deletion["passed"] = retained
        else:
            report["office"] = await verify_office(client, ticket)
        report["checked_at"] = utc()
        phases = ("office", "session_deletion")
        report["complete"] = all(phase in report for phase in phases)
        report["passed"] = report["complete"] and all(
            report[phase]["passed"] for phase in phases
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        save_json(args.output, report)
        phase = "session_deletion" if args.delete else "office"
        print(
            f"{args.output} — {phase} {'passed' if report[phase]['passed'] else 'failed'}",
            flush=True,
        )
        return report[phase]["passed"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument(
        "--ticket",
        type=Path,
        help="Verify a previously started run using its private login file.",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("docs/testing/office-release.json")
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Run and delete a separate new session, adding physical checks to the existing report.",
    )
    parser.add_argument("--docker-container", default="famsun-app-1")
    args = parser.parse_args()
    if args.ticket:
        raise SystemExit(0 if asyncio.run(finish(args)) else 1)
    if args.delete:
        parser.error("--delete requires the retained preview session's --ticket")
    asyncio.run(start(args.base_url))


if __name__ == "__main__":
    main()
