"""Deletion of synthetic sessions: tenant scope, resumption, and live Docker volumes."""

import asyncio
import os
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver

from famsun.api import create_app
from famsun.config import Settings
from famsun.session_checkpoints import remove_session_checkpoints, session_thread_id
from famsun.store import Conflict, Missing, Store

PASSWORD = "temporary-test-password"


class Box:
    def __init__(self, calls, failed, *, user_id, session_id, settings):
        self.key = (user_id, session_id)
        self.failures = failed
        self.calls = calls

    def stop(self):
        pass

    def delete(self):
        self.calls.append(self.key)
        if self.key in self.failures:
            raise OSError("synthetic offline storage")


def config(path):
    return replace(
        Settings(),
        data_dir=path,
        static_dir=None,
        secure_cookies=False,
        allow_signup=True,
        public_origin=None,
    )


def register(client, name):
    response = client.post(
        "/api/auth/register", json={"username": name, "password": PASSWORD}
    )
    assert response.status_code == 201
    return response.json()["id"]


def create(client, title):
    response = client.post("/api/sessions", json={"title": title})
    assert response.status_code == 201
    return response.json()["id"]


def history(db, uid, sid):
    rid = db.new_run(uid, sid, "synthetic prompt", 4, request_key="test-key")["id"]
    db.start(rid)
    db.append_event(
        rid,
        {
            "id": "tool",
            "parent_id": rid,
            "kind": "tool",
            "label": "execute",
            "status": "completed",
        },
    )
    db.finish(rid, "completed", "synthetic reply")
    return rid


def checkpoint_rows(path, threads):
    with SqliteSaver.from_conn_string(str(path)) as saver:
        saver.setup()
    with sqlite3.connect(path) as con:
        for thread_id in threads:
            for ns in ("", "child:engineering"):
                con.execute(
                    "INSERT INTO checkpoints(thread_id,checkpoint_ns,checkpoint_id) VALUES (?,?,?)",
                    (thread_id, ns, "checkpoint"),
                )
                con.execute(
                    "INSERT INTO writes(thread_id,checkpoint_ns,checkpoint_id,task_id,idx,channel) VALUES(?,?,?,?,?,?)",
                    (thread_id, ns, "checkpoint", "task", 0, "test"),
                )


def test_delete_is_scoped_to_owner_and_removes_all_history_and_checkpoint_namespaces(
    tmp_path,
):
    calls, failed = [], set()
    cfg = config(tmp_path)
    app = create_app(cfg, sandbox_factory=lambda **kw: Box(calls, failed, **kw))
    db = app.state.store
    with TestClient(app) as client:
        alice = register(client, "alice")
        target, sister = create(client, "target"), create(client, "sister")
        rid, sibling_run = history(db, alice, target), history(db, alice, sister)
        client.post("/api/auth/logout")
        path = "/api/sessions/" + target
        assert client.delete(path).status_code == 401
        bruno = register(client, "bruno")
        other = create(client, "other")
        other_run = history(db, bruno, other)
        assert client.delete(path).status_code == 404 and calls == []
        prefix = session_thread_id(alice, target)
        neighbors = {
            session_thread_id(alice, sister),
            session_thread_id(bruno, other),
            session_thread_id(bruno, target),
            prefix + "-suffix",
        }
        checkpoint_rows(cfg.checkpoint_path, neighbors | {prefix})
        client.post(
            "/api/auth/login", json={"username": "alice", "password": PASSWORD}
        ).raise_for_status()
        assert (
            client.delete(
                path, headers={"Origin": "https://elsewhere.example"}
            ).status_code
            == 403
        )
        assert calls == []
        response = client.delete(path)
        assert response.status_code == 204 and response.content == b""
        assert calls == [(alice, target)]
        for route in (
            "/runs",
            "/runs/" + rid,
            "/runs/" + rid + "/events",
            "/runs/" + rid + "/stream",
            "/files",
        ):
            assert client.get(path + route).status_code == 404
        assert client.post(path + "/runs", json={"prompt": "no"}).status_code == 404
        assert client.delete(path).status_code == 404
        assert [s["id"] for s in client.get("/api/sessions").json()] == [sister]
        assert db.run(alice, sister, sibling_run)["answer"] == "synthetic reply"
        assert db.run(bruno, other, other_run)["answer"] == "synthetic reply"
        with db.db() as con:
            for statement, key in [
                ("SELECT count(*) FROM events WHERE run_id=?", rid),
                ("SELECT count(*) FROM runs WHERE session_id=?", target),
                ("SELECT count(*) FROM run_requests WHERE session_id=?", target),
                ("SELECT count(*) FROM cleanup WHERE session_id=?", target),
                ("SELECT count(*) FROM sessions WHERE id=?", target),
            ]:
                assert con.execute(statement, (key,)).fetchone()[0] == 0
            assert not list(con.execute("PRAGMA foreign_key_check"))
        with sqlite3.connect(cfg.checkpoint_path) as con:
            for table in ("writes", "checkpoints"):
                assert dict(
                    con.execute(
                        f"SELECT thread_id,count(*) FROM {table} GROUP BY thread_id"
                    )
                ) == dict.fromkeys(neighbors, 2)


def test_failed_storage_cleanup_persists_hidden_tombstone_and_retries_on_restart(
    tmp_path,
):
    calls, failed = [], set()
    cfg = config(tmp_path)

    def build():
        return create_app(cfg, sandbox_factory=lambda **kw: Box(calls, failed, **kw))

    first = build()
    with TestClient(first) as client:
        uid = register(client, "writer")
        sid = create(client, "synthetic")
        path = "/api/sessions/" + sid
        db = first.state.store
        rid = history(db, uid, sid)
        failed.add((uid, sid))
        response = client.delete(path)
        assert response.status_code == 503 and response.headers["retry-after"] == "30"
        assert client.get("/api/sessions").json() == []
        for route in (
            "/runs",
            "/runs/" + rid + "/events",
            "/runs/" + rid + "/stream",
            "/files",
        ):
            assert client.get(path + route).status_code == 404
        assert (
            client.post(path + "/runs", json={"prompt": "blocked"}).status_code == 404
        )
        assert db.pending_deletions() == [{"id": sid, "user_id": uid}]
        with db.db() as con:
            assert (
                con.execute("SELECT COUNT(*) FROM runs WHERE id=?", (rid,)).fetchone()[
                    0
                ]
                == 1
            )
    failed.clear()
    second = build()
    with TestClient(second):
        deadline = time.monotonic() + 3
        while second.state.store.pending_deletions() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert second.state.store.pending_deletions() == []
        assert calls == [(uid, sid), (uid, sid)]
        assert not cfg.checkpoint_path.exists()


def test_old_sessions_gain_deleting_state_and_queue_conflict_is_reversible(tmp_path):
    cfg = config(tmp_path)
    db = Store(tmp_path / "studio.sqlite3")
    db.initialize()
    uid = db.create_user("owner", "unused")["id"]
    sid = db.create_session(uid, "from prior version")["id"]
    with db.db(write=True) as con:
        con.execute("ALTER TABLE sessions DROP COLUMN deleting")
    db.initialize()
    assert db.own_session(uid, sid)["title"] == "from prior version"
    calls = []
    with TestClient(
        create_app(cfg, sandbox_factory=lambda **kw: Box(calls, set(), **kw))
    ) as client:
        uid = register(client, "newuser")
        sid = create(client, "another")
        rid = db.new_run(uid, sid, "busy", 4)["id"]
        path = "/api/sessions/" + sid
        for state in ("queued", "running"):
            if state == "running":
                db.start(rid)
            response = client.delete(path)
            assert response.status_code == 409 and "停止" in response.json()["detail"]
            assert db.pending_deletions() == [] and calls == []
        db.finish(rid, "cancelled")
        assert client.delete(path).status_code == 204


def test_concurrent_run_and_delete_admissions_choose_one_winner(tmp_path):
    db = Store(tmp_path / "state.sqlite3")
    db.initialize()
    uid = db.create_user("owner", "unused")["id"]
    for _ in range(8):
        sid = db.create_session(uid, "race")["id"]
        barrier = threading.Barrier(2)

        def mark(sid=sid, barrier=barrier):
            barrier.wait(2)
            try:
                db.mark_for_deletion(uid, sid)
                return True
            except Conflict:
                return False

        def submit(sid=sid, barrier=barrier):
            barrier.wait(2)
            try:
                db.new_run(uid, sid, "task", 100)
                return True
            except Missing:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(mark), pool.submit(submit)]
            assert sorted(f.result(3) for f in futures) == [False, True]


@pytest.mark.asyncio
async def test_cancelled_http_waiter_and_simultaneous_delete_share_one_cleanup(
    tmp_path,
):
    cfg = config(tmp_path)
    entered, released = threading.Event(), threading.Event()
    calls = []

    class SlowBox:
        def delete(self):
            calls.append(True)
            entered.set()
            assert released.wait(5)

    app = create_app(cfg, sandbox_factory=lambda **_: SlowBox())
    db, jobs = app.state.store, app.state.jobs
    db.initialize()
    uid = db.create_user("owner", "unused")["id"]
    sid = db.create_session(uid, "target")["id"]
    first = asyncio.create_task(jobs.delete_session(uid, sid))
    assert await asyncio.to_thread(entered.wait, 2)
    second = asyncio.create_task(jobs.delete_session(uid, sid))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    await asyncio.sleep(0.04)
    assert db.pending_deletions() and not second.done()
    with pytest.raises(Missing):
        await jobs.delete_session("foreign-user", sid)
    released.set()
    assert await second and calls == [True] and db.pending_deletions() == []


@pytest.mark.asyncio
async def test_checkpoint_delete_preserves_retry_on_disk_error(tmp_path):
    missing = tmp_path / "no-db"
    await remove_session_checkpoints(missing, "u", "s")
    assert not missing.exists()
    with sqlite3.connect(missing) as con:
        con.execute("CREATE TABLE harmless(id INTEGER)")
    await remove_session_checkpoints(missing, "u", "s")
    with sqlite3.connect(missing) as con:
        assert {r[0] for r in con.execute("SELECT name FROM sqlite_schema")} == {
            "harmless"
        }
    invalid = tmp_path / "bad"
    invalid.write_bytes(b"not a sqlite database")
    with pytest.raises(sqlite3.DatabaseError):
        await remove_session_checkpoints(invalid, "u", "s")


@pytest.mark.skipif(
    os.getenv("FAMSUN_REQUIRE_DOCKER_TESTS") != "true",
    reason="requires built sandbox and helper images",
)
def test_http_deletes_two_real_docker_volumes_only_for_new_test_session(tmp_path):
    from famsun.sandbox import DockerSandbox

    cfg = replace(config(tmp_path), allow_unenforced_cgroups=True)

    async def runner(*, sandbox, **_):
        result = await asyncio.to_thread(
            sandbox.execute, "printf 'owned by test' > /workspace/marker.txt"
        )
        assert result.exit_code == 0
        return "written"

    def volume_exists(name):
        return (
            subprocess.run(
                ["docker", "volume", "inspect", name], capture_output=True, check=False
            ).returncode
            == 0
        )

    with TestClient(create_app(cfg, runner=runner)) as client:
        uid = register(client, "docker-user")
        sid = create(client, "delete-me")
        box = DockerSandbox(uid, sid)
        volumes = [box.id + "-files", box.id + "-home"]
        try:
            root = f"/api/sessions/{sid}"
            rid = client.post(root + "/runs", json={"prompt": "save"}).json()["id"]
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                record = client.get(root + "/runs/" + rid).json()
                if record["status"] not in ("running", "queued"):
                    break
                time.sleep(0.1)
            assert record["status"] == "completed" and all(map(volume_exists, volumes))
            assert (
                client.get(root + "/file", params={"path": "marker.txt"}).content
                == b"owned by test"
            )
            assert client.delete(root).status_code == 204
            assert not any(map(volume_exists, volumes))
            assert (
                client.get(root + "/file", params={"path": "marker.txt"}).status_code
                == 404
            )
        finally:
            box.delete()


def test_checkpoint_failure_after_volume_cleanup_is_retriable(tmp_path):
    cfg = config(tmp_path)
    calls = []
    app = create_app(cfg, sandbox_factory=lambda **kw: Box(calls, set(), **kw))
    with TestClient(app) as client:
        uid = register(client, "checkpoint-test")
        sid = create(client, "fresh data")
        rid = history(app.state.store, uid, sid)
        cfg.checkpoint_path.write_bytes(b"intentionally damaged synthetic database")
        path = "/api/sessions/" + sid
        assert client.delete(path).status_code == 503
        assert calls == [(uid, sid)]
        assert app.state.store.deletion_is_pending(uid, sid)
        with app.state.store.db() as con:
            assert (
                con.execute(
                    "SELECT count(*) FROM events WHERE run_id=?", (rid,)
                ).fetchone()[0]
                > 0
            )
        # Replace this test's simulated failed disk with a recovered backup.
        cfg.checkpoint_path.unlink()
        owner_key = session_thread_id(uid, sid)
        checkpoint_rows(cfg.checkpoint_path, [owner_key, "different/thread"])
        assert client.delete(path).status_code == 204
        assert calls == [(uid, sid), (uid, sid)]
        assert not app.state.store.deletion_is_pending(uid, sid)
        with sqlite3.connect(cfg.checkpoint_path) as con:
            assert dict(
                con.execute("SELECT thread_id,count(*) FROM writes GROUP BY thread_id")
            ) == {"different/thread": 2}


def test_delete_waits_for_preview_before_hash_and_discards_only_that_session(
    tmp_path, monkeypatch
):
    from famsun import preview

    entered, released = threading.Event(), threading.Event()
    instances = []
    delay = set()

    class SlowPreview(preview.Previews):
        def __init__(self, settings):
            super().__init__(settings)
            instances.append(self)

        async def get(self, uid, sid, *args):
            if sid in delay:
                entered.set()
                assert await asyncio.to_thread(released.wait, 5)
            return await super().get(uid, sid, *args)

    class Documents(Box):
        def read_artifact(self, path):
            return b"synthetic Word snapshot"

    monkeypatch.setattr("famsun.api.Previews", SlowPreview)
    monkeypatch.setattr(preview, "convert", lambda *args: b"%PDF-test-document")
    app = create_app(
        config(tmp_path), sandbox_factory=lambda **kw: Documents([], set(), **kw)
    )
    with TestClient(app) as client, ThreadPoolExecutor(2) as pool:
        uid = register(client, "preview-race")
        sibling, target = create(client, "retained"), create(client, "purged")

        def url(sid):
            return f"/api/sessions/{sid}/preview?path=sample.docx"

        assert client.get(url(sibling)).status_code == 200
        delay.add(target)
        inflight = pool.submit(client.get, url(target))
        assert entered.wait(2)
        deletion = pool.submit(client.delete, f"/api/sessions/{target}")
        try:
            deadline = time.monotonic() + 2
            while not app.state.store.deletion_is_pending(uid, target):
                assert time.monotonic() < deadline
                time.sleep(0.01)
            assert client.get(url(target)).status_code == 404
            assert not deletion.done()
        finally:
            released.set()
        assert inflight.result(3).status_code == 200
        assert deletion.result(3).status_code == 204
        previews = instances[0]
        assert not previews.pending
        assert {k[:2] for k in previews.cache} == {(uid, sibling)}
        assert previews.size == len(b"%PDF-test-document")
