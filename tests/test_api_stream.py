"""Exercise the durable trace protocol over a genuine streaming HTTP connection."""

import asyncio
import json
import socket
from contextlib import asynccontextmanager
from dataclasses import replace

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from famsun import api
from famsun.config import Settings
from famsun.store import EVENT_PAYLOAD_BYTES, Store

PASSWORD = "test-only-stream-password"


class Box:
    def start(self):
        pass

    def stop(self):
        pass


def application(tmp_path, runner):
    cfg = replace(
        Settings(),
        data_dir=tmp_path,
        static_dir=None,
        allow_signup=True,
        secure_cookies=False,
        public_origin=None,
    )
    return api.create_app(cfg, runner=runner, sandbox_factory=lambda **_: Box())


@asynccontextmanager
async def serve(app):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, log_level="error", timeout_graceful_shutdown=2)
        )
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(3):
                while not server.started:
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            await task


async def records(response):
    """Small EventSource-format parser; comment messages expose heartbeat timing."""
    fields = {}
    async for line in response.aiter_lines():
        if not line and fields:
            yield fields
            fields = {}
        elif line.startswith(":"):
            fields["comment"] = line[1:].strip()
        elif ":" in line:
            key, value = line.split(":", 1)
            fields[key] = value.lstrip()


async def register(client, name):
    resp = await client.post(
        "/api/auth/register", json={"username": name, "password": PASSWORD}
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_stream_delivers_before_completion_and_replays_after_disconnect(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(api, "SSE_HEARTBEAT_SECONDS", 0.1)
    offline, midway, complete = asyncio.Event(), asyncio.Event(), asyncio.Event()
    node = {
        "id": "model",
        "parent_id": "parent",
        "kind": "model",
        "label": "模型响应",
        "status": "running",
    }

    async def runner(*, emit, **_):
        await emit(
            {**node, "payload": {"type": "model_text", "delta": "第一段\n\nevent: end"}}
        )
        await offline.wait()
        await emit({**node, "payload": {"type": "model_text", "delta": "第二段"}})
        midway.set()
        await complete.wait()
        await emit({**node, "status": "completed"})
        return "finished"

    app = application(tmp_path, runner)
    async with (
        serve(app) as base,
        httpx.AsyncClient(base_url=base, trust_env=False) as alice,
        httpx.AsyncClient(base_url=base, trust_env=False) as other,
    ):
        await register(alice, "alice")
        sid = (await alice.post("/api/sessions", json={})).json()["id"]
        root = f"/api/sessions/{sid}/runs"
        rid = (await alice.post(root, json={"prompt": "stream"})).json()["id"]
        url = f"{root}/{rid}/stream"
        assert (await other.get(url)).status_code == 401
        await register(other, "bruno")
        assert (await other.get(url)).status_code == 404
        assert (
            await alice.get(url, headers={"Origin": "https://different.example"})
        ).status_code == 403
        assert (
            await alice.get(url, headers={"Last-Event-ID": "not-a-number"})
        ).status_code == 422
        seen = []
        async with asyncio.timeout(6):
            async with alice.stream("GET", url) as response:
                assert response.status_code == 200 and response.headers[
                    "content-type"
                ].startswith("text/event-stream")
                assert (
                    response.headers["x-accel-buffering"] == "no"
                    and "no-store" in response.headers["cache-control"]
                )
                async for msg in records(response):
                    if msg.get("event") == "activity":
                        row = json.loads(msg["data"])
                        assert int(msg["id"]) == row["seq"]
                        seen.append(row)
                        if row["payload"]:
                            assert row["payload"]["delta"] == "第一段\n\nevent: end"
                            break
            while app.state.jobs.subscribers:
                await asyncio.sleep(0.01)
            assert (await alice.get(f"{root}/{rid}")).json()["status"] == "running"
            offline.set()
            await midway.wait()
            first_seq = seen[-1]["seq"]
            heartbeat_seen = False
            terminal = None
            async with alice.stream(
                "GET",
                url,
                params={"after": first_seq - 1},
                headers={"Last-Event-ID": str(first_seq)},
            ) as response:
                async for msg in records(response):
                    if msg.get("event") == "activity":
                        row = json.loads(msg["data"])
                        assert row["seq"] > seen[-1]["seq"]
                        seen.append(row)
                    elif msg.get("comment") == "heartbeat":
                        heartbeat_seen = True
                        complete.set()
                    elif msg.get("event") == "end":
                        terminal = json.loads(msg["data"])
        assert heartbeat_seen and terminal == {
            "status": "completed",
            "cursor": seen[-1]["seq"],
        }
        poll = (await alice.get(f"{root}/{rid}/events")).json()
        assert seen == poll["events"]
        assert [row["payload"]["delta"] for row in seen if row["payload"]] == [
            "第一段\n\nevent: end",
            "第二段",
        ]
        assert not app.state.jobs.subscribers


def test_migrates_legacy_events_and_omits_repeated_delta_on_interruption(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    store.initialize()
    uid = store.create_user("alice", "unused")["id"]
    sid = store.create_session(uid, "old session")["id"]
    rid = store.new_run(uid, sid, "existing", 1)["id"]
    with store.db(write=True) as con:
        con.execute("ALTER TABLE events DROP COLUMN payload")
        previous = con.execute(
            "SELECT seq,created_at FROM events WHERE run_id=?", (rid,)
        ).fetchone()
    store.initialize()
    store.initialize()
    legacy = store.events(uid, sid, rid, 0)["events"][0]
    assert (
        legacy["seq"] == previous["seq"]
        and legacy["created_at"] == previous["created_at"]
        and legacy["payload"] is None
    )
    node = {
        "id": "model",
        "kind": "model",
        "label": "turn",
        "status": "running",
        "parent_id": rid,
    }
    store.append_event(
        rid, {**node, "payload": {"type": "model_text", "delta": "text"}}
    )
    store.finish(rid, "cancelled")
    rows = store.events(uid, sid, rid, legacy["seq"])["events"]
    assert rows[0]["payload"] == {"type": "model_text", "delta": "text"}
    assert (
        rows[1]["id"] == "model"
        and rows[1]["payload"] is None
        and rows[1]["status"] == "cancelled"
    )


def test_bounded_multi_page_terminal_stream_and_oversize_result(tmp_path, monkeypatch):
    app = application(tmp_path, None)
    with TestClient(app) as client:
        uid = client.post(
            "/api/auth/register", json={"username": "alice", "password": PASSWORD}
        ).json()["id"]
        sid = client.post("/api/sessions", json={}).json()["id"]
        db = app.state.store
        rid = db.new_run(uid, sid, "history", 1)["id"]
        node = {
            "id": "tool",
            "kind": "tool",
            "label": "execute",
            "status": "running",
            "parent_id": rid,
        }
        with db.db(write=True) as con:
            for num in range(514):
                db._event(
                    con,
                    rid,
                    {
                        **node,
                        "payload": {
                            "type": "tool",
                            "arguments": {"line": num},
                            "result": [1, False, None],
                        },
                    },
                )
        db.append_event(
            rid,
            {**node, "payload": {"type": "tool", "result": "文" * EVENT_PAYLOAD_BYTES}},
        )
        db.finish(rid, "failed", error="test")
        db.cleared(sid)
        path = f"/api/sessions/{sid}/runs/{rid}"
        first = client.get(path + "/events").json()
        assert len(first["events"]) == 500 and first["has_more"]
        # Force many byte-limited pages as well as checking the row limit.
        monkeypatch.setattr("famsun.store.EVENT_PAGE_BYTES", 4096)
        expected, cursor = [], 0
        while True:
            page = client.get(path + "/events", params={"after": cursor}).json()
            assert len(page["events"]) < 500
            expected.extend(page["events"])
            cursor = page["cursor"]
            if not page["has_more"]:
                break
        assert len(expected) == 518
        assert expected[-3]["payload"]["result_truncated"] is True
        response = client.get(path + "/stream")
        data = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert data[:-1] == expected and data[-1] == {
            "cursor": cursor,
            "status": "failed",
        }
        assert (
            client.get(path + "/stream", params={"after": cursor}).text.count(
                "event: activity"
            )
            == 0
        )


@pytest.mark.asyncio
async def test_logging_out_ends_a_live_stream_before_more_private_rows(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(api, "SSE_HEARTBEAT_SECONDS", 0.15)
    release = asyncio.Event()

    async def runner(*, emit, run_id, **_):
        await release.wait()
        await emit(
            {
                "id": "private",
                "parent_id": run_id,
                "kind": "tool",
                "label": "after logout",
                "status": "completed",
                "payload": {"type": "tool", "result": "after revocation"},
            }
        )
        return "finished"

    app = application(tmp_path, runner)
    async with (
        serve(app) as base,
        httpx.AsyncClient(base_url=base, trust_env=False) as client,
    ):
        await register(client, "alice")
        sid = (await client.post("/api/sessions", json={})).json()["id"]
        root = f"/api/sessions/{sid}/runs"
        rid = (await client.post(root, json={"prompt": "run"})).json()["id"]
        received = []
        async with (
            asyncio.timeout(5),
            client.stream("GET", f"{root}/{rid}/stream") as response,
        ):
            async for msg in records(response):
                if msg.get("event") == "activity":
                    row = json.loads(msg["data"])
                    received.append(row)
                    if row["status"] == "running":
                        assert (
                            await client.post("/api/auth/logout")
                        ).status_code == 204
                        release.set()
        assert release.is_set() and all(row["id"] != "private" for row in received)
        assert (await client.get(f"{root}/{rid}/events")).status_code == 401
        assert not app.state.jobs.subscribers
