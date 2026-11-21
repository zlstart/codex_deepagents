"""Authorization, content-addressed previews, bounded conversions and input checks."""

import asyncio
import json
import subprocess
import threading
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from famsun import preview, sandbox
from famsun.api import create_app
from famsun.config import Settings


class Files:
    def __init__(self):
        self.contents = {}
        self.reads = 0

    def read_artifact(self, path):
        self.reads += 1
        if path not in self.contents:
            raise FileNotFoundError(path)
        return self.contents[path]

    def delete(self):
        self.contents.clear()


def test_preview_route_checks_scope_content_and_source_updates(tmp_path, monkeypatch):
    files = Files()
    original = b"first office snapshot"
    files.contents.update(
        {
            "report.docx": original,
            "plan.xlsx": b"sheet",
            "native.pdf": b"%PDF-1.7\nnative",
            "broken.pdf": b"other",
            "notes.log": b"a",
        }
    )
    app = create_app(
        Settings(data_dir=tmp_path, allow_signup=True, secure_cookies=False),
        sandbox_factory=lambda **kwargs: files,
    )
    calls = []

    def render(settings, data, ext, view, sheet, offset):
        calls.append((data, ext, view, sheet, offset))
        return (
            json.dumps({"sheet": sheet, "offset": offset}).encode()
            if view == "sheet"
            else b"%PDF-1.7\n" + data
        )

    monkeypatch.setattr(preview, "convert", render)
    with TestClient(app) as client:
        assert (
            client.get(
                "/api/sessions/test/preview", params={"path": "report.docx"}
            ).status_code
            == 401
        )

        def register(name):
            assert (
                client.post(
                    "/api/auth/register",
                    json={"username": name, "password": "Test-document-1847"},
                ).status_code
                == 201
            )
            return (
                "/api/sessions/"
                + client.post("/api/sessions", json={}).json()["id"]
                + "/preview"
            )

        url = register("docowner")
        for _ in range(2):
            res = client.get(url, params={"path": "report.docx"})
            assert res.status_code == 200 and res.content.endswith(original)
            assert res.headers["content-type"] == "application/pdf"
            assert (
                "inline;" in res.headers["content-disposition"]
                and "no-store" in res.headers["cache-control"]
            )
            assert res.headers["x-content-type-options"] == "nosniff"
        assert files.contents["report.docx"] == original and len(calls) == 1
        files.contents["report.docx"] = b"revised document"
        assert client.get(url, params={"path": "report.docx"}).content.endswith(
            b"revised document"
        )
        assert len(calls) == 2
        grid = client.get(
            url, params={"path": "plan.xlsx", "view": "sheet", "sheet": 1, "offset": 80}
        )
        assert (
            grid.status_code == 200
            and grid.headers["content-type"] == "application/json"
        )
        assert grid.json() == {"sheet": 1, "offset": 80}
        assert (
            client.get(url, params={"path": "native.pdf"}).content
            == files.contents["native.pdf"]
        )
        for params, status in [
            ({"path": "broken.pdf"}, 422),
            ({"path": "missing.pptx"}, 404),
            ({"path": "notes.log"}, 415),
            ({"path": "../secret.pdf"}, 400),
            ({"path": "report.docx", "view": "sheet"}, 415),
            ({"path": "report.docx", "offset": 80}, 422),
            ({"path": "plan.xlsx", "sheet": -1}, 422),
        ]:
            assert client.get(url, params=params).status_code == status
        client.post("/api/auth/logout")
        other = register("otheruser")
        count = files.reads
        assert client.get(url, params={"path": "report.docx"}).status_code == 404
        assert files.reads == count
        assert client.get(other, params={"path": "report.docx"}).status_code == 200
        assert len(calls) == 4


async def test_cancelled_preview_stays_bounded_and_session_purge_drains_it(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def render(settings, data, *args):
        calls.append(data)
        if data != b"preexisting":
            entered.set()
            assert release.wait(5)
        return b"%PDF-1.7\n" + data

    monkeypatch.setattr(preview, "convert", render)
    previews = preview.Previews(None)

    async def get(user, session, data):
        return await previews.get(user, session, data, "docx", "layout", 0, 0)

    retained = await get("other", "sid", b"preexisting")
    await get("one", "sid", b"preexisting")
    caller = asyncio.create_task(get("one", "sid", b"A"))
    assert await asyncio.to_thread(entered.wait, 3)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    duplicate = asyncio.create_task(get("one", "sid", b"A"))
    second = asyncio.create_task(get("two", "sid", b"B"))
    while len(previews.pending) < 2:
        await asyncio.sleep(0.01)
    with pytest.raises(preview.PreviewError) as err:
        await get("three", "sid", b"C")
    assert err.value.status == 429
    purge = asyncio.create_task(previews.discard_session("one", "sid"))
    await asyncio.sleep(0)
    assert not purge.done()
    release.set()
    await asyncio.gather(duplicate, second, purge)
    assert calls.count(b"A") == 1
    assert await get("other", "sid", b"preexisting") == retained
    assert not any(k[:2] == ("one", "sid") for k in previews.cache)
    assert previews.size == sum(map(len, previews.cache.values()))


@pytest.mark.parametrize(
    ("code", "status"),
    [(40, 422), (41, 413), (42, 422), (44, 503), (45, 504), (125, 503), (137, 413)],
)
def test_converter_sandbox_policy_and_errors(monkeypatch, code, status):
    def capture(args, *, limit, timeout, data, stderr):
        assert data == b"source" and 0 < timeout <= 120
        assert args[args.index("--network") + 1] == "none" and "--mount" not in args
        assert "--read-only" in args and "--memory" in args and "--pids-limit" in args
        assert args[args.index("--cap-drop") + 1] == "ALL"
        assert stderr == subprocess.DEVNULL and limit <= 32 * 1024 * 1024
        return code, b"", False

    monkeypatch.setattr(sandbox, "_capture", capture)
    with pytest.raises(preview.PreviewError) as err:
        preview.convert(
            Settings(allow_unenforced_cgroups=True), b"source", "docx", "layout", 0, 0
        )
    assert err.value.status == status


def test_conversion_timeout_removes_only_its_own_container(monkeypatch):
    invoked, removed = [], []

    def timeout(args, **kwargs):
        invoked.append(args[args.index("--name") + 1])
        raise subprocess.TimeoutExpired(["docker", "run"], 100)

    monkeypatch.setattr(sandbox, "_capture", timeout)
    monkeypatch.setattr(
        sandbox, "_remove", lambda kind, name: removed.append((kind, name))
    )
    with pytest.raises(preview.PreviewError) as err:
        preview.convert(
            Settings(allow_unenforced_cgroups=True), b"data", "pptx", "layout", 0, 0
        )
    assert err.value.status == 504 and removed == [("container", invoked[0])]


def test_ooxml_preflight_rejects_expansion_and_mismatched_content(tmp_path):
    import runpy

    guard = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "deploy/preview/convert.py")
    )

    def doc(extra, content=b"test"):
        p = tmp_path / "sample.docx"
        with zipfile.ZipFile(p, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", b"test")
            z.writestr("[Content_Types].xml", b"test")
            z.writestr(extra, content)
        return p

    with pytest.raises(OverflowError):
        guard["check_source"](doc("word/data.bin", bytes(3 * 1024 * 1024)), "docx")
    for name in ("../unrelated", "/absolute", "word/vbaProject.bin"):
        with pytest.raises(guard["InvalidDocument"]):
            guard["check_source"](doc(name), "docx")
    with pytest.raises(guard["InvalidDocument"]):
        guard["check_source"](doc("another-file"), "pptx")
