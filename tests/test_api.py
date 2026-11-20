"""Exercise the HTTP control plane with deterministic runner and volume doubles."""

import asyncio
import os
import threading
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from famsun.api import create_app
from famsun.auth import password_hash
from famsun.config import Settings

PWD = "correct-horse-and-feed"


class Box:
    def __init__(self, volumes, stopped, *, user_id, session_id, settings):
        self.key = user_id, session_id
        self.files = volumes.setdefault(self.key, {})
        self.stopped = stopped

    def start(self):
        pass

    def stop(self):
        self.stopped.add(self.key)

    def list_artifacts(self):
        return [
            {"path": path, "name": path, "size": len(data)}
            for path, data in self.files.items()
        ]

    def read_artifact(self, path):
        if path not in self.files:
            raise FileNotFoundError
        return self.files[path]


@pytest.fixture
def rig(tmp_path):
    cfg = replace(
        Settings(),
        data_dir=tmp_path,
        static_dir=tmp_path / "unused",
        allow_signup=True,
        secure_cookies=False,
        max_parallel_runs=1,
    )
    volumes, stopped = {}, set()
    entered = threading.Event()

    def sandbox(**kwargs):
        return Box(volumes, stopped, **kwargs)

    async def run(*, run_id, sandbox, emit, prompt, **kwargs):
        sub = {
            "id": "delegation",
            "parent_id": run_id,
            "kind": "subagent",
            "label": "工程建模",
            "status": "running",
        }
        child = {
            "id": "tool",
            "parent_id": sub["id"],
            "kind": "tool",
            "label": "write_file",
            "status": "running",
        }
        await emit(sub)
        await emit(child)
        entered.set()
        if prompt == "wait":
            await asyncio.Event().wait()
        if prompt == "failure":
            raise RuntimeError("internal-only-marker")
        sandbox.files["result.txt"] = prompt.encode()
        sandbox.files["view.html"] = b"<p>report</p>"
        await emit({**child, "status": "completed"})
        await emit({**sub, "status": "completed"})
        return "完成"

    return cfg, run, sandbox, entered, stopped


def account(client, name):
    res = client.post("/api/auth/register", json={"username": name, "password": PWD})
    assert res.status_code == 201, res.text
    return res.json()


def session(client):
    res = client.post("/api/sessions", json={})
    assert res.status_code == 201, res.text
    return "/api/sessions/" + res.json()["id"]


def submit(client, sid, prompt):
    res = client.post(sid + "/runs", json={"prompt": prompt})
    assert res.status_code == 202, res.text
    return sid + "/runs/" + res.json()["id"]


def finish(client, run, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = client.get(run).json()
        if record["status"] not in ("queued", "running"):
            return record
        time.sleep(0.015)
    pytest.fail("run did not settle")


def test_auth_session_run_events_files_isolation(rig):
    cfg, runner, factory, _, _ = rig
    app = create_app(cfg, runner=runner, sandbox_factory=factory)
    with TestClient(app) as client:
        assert client.get("/api/auth/me").status_code == 401
        account(client, "alice")
        cookie = client.cookies["famsun_session"]
        a = session(client)
        b = session(client)
        run = submit(client, a, "工程检查")
        assert finish(client, run)["answer"] == "完成"
        trace = client.get(run + "/events").json()
        state = {e["id"]: e for e in trace["events"]}
        assert state["tool"]["parent_id"] == "delegation"
        assert all(e["status"] == "completed" for e in state.values())
        assert (
            client.get(run + "/events", params={"after": trace["cursor"]}).json()[
                "events"
            ]
            == []
        )
        assert [f["path"] for f in client.get(a + "/files").json()] == [
            "result.txt",
            "view.html",
        ]
        file = client.get(a + "/file", params={"path": "result.txt"})
        assert file.text == "工程检查"
        assert file.headers["x-content-type-options"] == "nosniff"
        assert (
            client.get(a + "/file", params={"path": "view.html"})
            .headers["content-disposition"]
            .startswith("attachment")
        )
        assert client.get(a + "/file", params={"path": "../data"}).status_code == 400
        assert client.get(b + "/file", params={"path": "result.txt"}).status_code == 404
        assert client.get(b + "/files").json() == []
        assert client.get("/api/sessions").json()[0]["title"] == "工程检查"
        assert client.post("/api/auth/logout").status_code == 204
        client.cookies.set("famsun_session", cookie)
        assert client.get("/api/auth/me").status_code == 401
        client.cookies.clear()
        account(client, "bruno")
        for target in (
            a + "/files",
            a + "/file?path=result.txt",
            run,
            run + "/events",
            a + "/runs",
        ):
            assert client.get(target).status_code == 404
        assert client.post(run + "/cancel").status_code == 404
        assert client.post(a + "/runs", json={"prompt": "x"}).status_code == 404
        assert client.get("/api/sessions").json() == []
        assert (
            client.post(
                "/api/auth/login", json={"username": "alice", "password": "wrong"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/auth/login", json={"username": "Alice", "password": PWD}
            ).status_code
            == 200
        )
        assert len(client.get(a + "/runs").json()) == 1


def test_cancel_admission_capacity_and_failure(rig):
    cfg, runner, factory, entered, stopped = rig
    with TestClient(create_app(cfg, runner=runner, sandbox_factory=factory)) as client:
        account(client, "engineer")
        sid, other = session(client), session(client)
        run = submit(client, sid, "wait")
        assert entered.wait(2)
        assert client.post(sid + "/runs", json={"prompt": "overlap"}).status_code == 409
        assert (
            client.post(other + "/runs", json={"prompt": "overlap"}).status_code == 429
        )
        assert client.post(run + "/cancel").json()["status"] == "cancelled"
        states = {
            e["id"]: e["status"] for e in client.get(run + "/events").json()["events"]
        }
        assert set(states.values()) == {"cancelled"}
        assert stopped
        assert client.post(run + "/cancel").json()["status"] == "cancelled"
        failed = submit(client, sid, "failure")
        record = finish(client, failed)
        assert (
            record["status"] == "failed"
            and "internal-only-marker" not in record["error"]
        )
        assert finish(client, submit(client, sid, "recovered"))["status"] == "completed"


def test_origin_cookie_body_and_registration_controls(rig):
    cfg, run, factory, _, _ = rig
    with TestClient(
        create_app(
            replace(cfg, secure_cookies=True), runner=run, sandbox_factory=factory
        ),
        base_url="https://studio.example",
    ) as client:
        payload = {"username": "alice", "password": PWD}
        assert (
            client.post(
                "/api/auth/register",
                json=payload,
                headers={"Origin": "https://other.example"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/auth/register", json=payload, headers={"Origin": "null"}
            ).status_code
            == 403
        )
        assert client.post("/api/auth/register", data=payload).status_code == 415
        assert (
            client.post("/api/auth/register", content=b"x" * 200000).status_code == 413
        )
        res = client.post(
            "/api/auth/register",
            json=payload,
            headers={"Origin": "https://studio.example"},
        )
        assert res.status_code == 201
        policy = res.headers["set-cookie"].lower()
        assert (
            "secure" in policy and "httponly" in policy and "samesite=strict" in policy
        )
        invalid = client.post(
            "/api/auth/login", json={"username": " !alice", "password": PWD}
        )
        assert invalid.status_code == 422 and PWD not in invalid.text
        with (
            pytest.raises(RuntimeError, match="one application worker"),
            TestClient(create_app(cfg, runner=run, sandbox_factory=factory)),
        ):
            pass
    with TestClient(
        create_app(
            replace(cfg, allow_signup=False), runner=run, sandbox_factory=factory
        )
    ) as client:
        assert client.get("/api/auth/config").json() == {"allow_signup": False}
        assert (
            client.post(
                "/api/auth/register", json={"username": "user", "password": PWD}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/auth/login", json={"username": "alice", "password": PWD}
            ).status_code
            == 200
        )


def test_restarts_close_dangling_children_and_resume_session(rig):
    cfg, run, factory, _, stopped = rig
    # Simulate an abrupt worker exit with a persisted running record.
    app = create_app(cfg, runner=run, sandbox_factory=factory)
    db = app.state.store
    db.initialize()
    credential = password_hash(PWD)
    uid = db.create_user("engineer", credential)["id"]
    sid = db.create_session(uid, "design")["id"]
    rid = db.new_run(uid, sid, "first", 1)["id"]
    db.start(rid)
    db.append_event(
        rid,
        {
            "id": "pending",
            "parent_id": rid,
            "kind": "tool",
            "label": "execute",
            "status": "running",
        },
    )
    with TestClient(app) as client:
        assert (uid, sid) in stopped
        assert (
            client.post(
                "/api/auth/login", json={"username": "engineer", "password": PWD}
            ).status_code
            == 200
        )
        run_url = f"/api/sessions/{sid}/runs/{rid}"
        assert client.get(run_url).json()["status"] == "failed"
        assert (
            client.get(run_url + "/events").json()["events"][-2]["status"] == "failed"
        )
        assert (
            finish(client, submit(client, "/api/sessions/" + sid, "resume"))["status"]
            == "completed"
        )


def test_timeout_releases_capacity_and_closes_child_nodes(rig):
    cfg, runner, factory, _, stopped = rig
    with TestClient(
        create_app(replace(cfg, run_timeout=1), runner=runner, sandbox_factory=factory)
    ) as client:
        account(client, "engineer")
        sid = session(client)
        run = submit(client, sid, "wait")
        result = finish(client, run)
        assert result["status"] == "failed" and "时限" in result["error"]
        states = {
            event["id"]: event["status"]
            for event in client.get(run + "/events").json()["events"]
        }
        assert set(states.values()) == {"failed"}
        assert stopped
        assert finish(client, submit(client, sid, "retry"))["status"] == "completed"


@pytest.mark.asyncio
async def test_cancel_racing_stop_waits_for_finalization(rig):
    cfg, runner, factory, _, _ = rig
    cleaning, release = threading.Event(), threading.Event()

    def slow_factory(**kwargs):
        box = factory(**kwargs)

        def stop():
            cleaning.set()
            assert release.wait(3)

        box.stop = stop
        return box

    app = create_app(cfg, runner=runner, sandbox_factory=slow_factory)
    db, jobs = app.state.store, app.state.jobs
    db.initialize()
    uid = db.create_user("engineer", "unused")["id"]
    sid = db.create_session(uid, "model")["id"]
    run = db.new_run(uid, sid, "complete", 1)
    jobs.submit(uid, sid, run)
    assert await asyncio.to_thread(cleaning.wait, 2)
    cancels = [asyncio.create_task(jobs.cancel(run["id"])) for _ in range(2)]
    await asyncio.sleep(0.02)
    assert not any(task.done() for task in cancels)
    release.set()
    await asyncio.gather(*cancels)
    assert db.run(uid, sid, run["id"])["status"] == "completed"
    assert db.pending_cleanup() == []


def test_request_key_preserves_single_run_across_http_retries(rig):
    cfg, runner, factory, _, _ = rig
    with TestClient(create_app(cfg, runner=runner, sandbox_factory=factory)) as client:
        account(client, "engineer")
        sid = session(client)
        kwargs = {
            "json": {"prompt": "calculate"},
            "headers": {"Idempotency-Key": "same-request"},
        }
        first = client.post(sid + "/runs", **kwargs)
        assert first.status_code == 202
        rid = first.json()["id"]
        assert finish(client, sid + "/runs/" + rid)["status"] == "completed"
        repeated = client.post(sid + "/runs", **kwargs)
        assert repeated.status_code == 202 and repeated.json()["id"] == rid
        assert len(client.get(sid + "/runs").json()) == 1
        kwargs["json"]["prompt"] = "changed"
        assert client.post(sid + "/runs", **kwargs).status_code == 409


@pytest.mark.skipif(
    os.getenv("FAMSUN_REQUIRE_DOCKER_TESTS") != "true",
    reason="requires a built sandbox image and Docker",
)
def test_http_session_artifacts_on_real_docker_volumes(rig):
    from famsun.sandbox import create_sandbox

    cfg, _, _, _, _ = rig
    # This functional test also runs on CI's rootless daemon without delegated
    # cgroups. Strict deployment preflight and limits are verified separately.
    cfg = replace(cfg, allow_unenforced_cgroups=True)
    boxes = []

    async def execute(**kwargs):
        box = kwargs["sandbox"]
        boxes.append(box)
        result = await asyncio.to_thread(
            box.execute, "printf '42' > /workspace/verified.txt"
        )
        assert result.exit_code == 0
        return "42"

    with TestClient(create_app(cfg, runner=execute)) as client:
        try:
            info = account(client, "alice")
            sid, sister = session(client), session(client)
            run = submit(client, sid, "verify")
            assert finish(client, run, timeout=60)["status"] == "completed"
            assert (
                client.get(sid + "/file", params={"path": "verified.txt"}).content
                == b"42"
            )
            assert [r["path"] for r in client.get(sid + "/files").json()] == [
                "verified.txt"
            ]
            assert client.get(sister + "/files").json() == []
            client.post("/api/auth/logout")
            account(client, "bruno")
            assert (
                client.get(sid + "/file", params={"path": "verified.txt"}).status_code
                == 404
            )
        finally:
            if boxes:
                cleanup = create_sandbox(
                    user_id=info["id"], session_id=sid.rsplit("/", 1)[-1], settings=cfg
                )
                cleanup.delete()
