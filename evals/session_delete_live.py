"""Release acceptance for deleting one freshly seeded synthetic session."""

import asyncio
import inspect
import json
import re
import secrets
import subprocess
import time

import httpx

from .live import fetch_file
from .workspace_smoke import json_request, trace_stream


def _scoped_counts(uid, sid, rid):
    """Runs inside the named service container, with read-only database and Docker calls."""
    import sqlite3
    import subprocess
    from contextlib import closing

    from famsun.config import Settings
    from famsun.sandbox import LABEL, DockerSandbox
    from famsun.session_checkpoints import session_thread_id

    def read(path):
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        db.execute("PRAGMA query_only=ON")
        return closing(db)

    def count(db, table, predicate, *values):
        return db.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {predicate}", values
        ).fetchone()[0]

    def docker(*args):
        return subprocess.check_output(["docker", *args], text=True, timeout=10).strip()

    cfg = Settings()
    with read(cfg.data_dir / "studio.sqlite3") as db:
        db.execute("BEGIN")
        counts = {
            "sessions": count(db, "sessions", "user_id=? AND id=?", uid, sid),
            "runs": count(db, "runs", "session_id=?", sid),
            "events": count(db, "events", "run_id=?", rid),
            "request_keys": count(db, "run_requests", "session_id=?", sid),
            "cleanup": count(db, "cleanup", "session_id=?", sid),
        }
    with read(cfg.checkpoint_path) as db:
        db.execute("BEGIN")
        for table in ("checkpoints", "writes"):
            counts[table] = count(db, table, "thread_id=?", session_thread_id(uid, sid))

    base = DockerSandbox(uid, sid).id
    names = docker("volume", "ls", "--filter", f"name={base}", "--format", "{{.Name}}")
    volumes = set(names.splitlines()) & {base + "-files", base + "-home"}
    label, expected = LABEL.split("=", 1)
    counts["volumes"] = len(volumes)
    counts["tagged_volumes"] = sum(
        docker("volume", "inspect", "--format", '{{index .Labels "' + label + '"}}', v)
        == expected
        for v in volumes
    )
    return counts


async def _inspect(container, uid, sid, rid):
    # Send just the count function to the deployed Python, including its imports.
    source = inspect.getsource(_scoped_counts) + (
        "\nimport json, sys\nprint(json.dumps(_scoped_counts(*sys.argv[1:])))\n"
    )
    result = await asyncio.to_thread(
        subprocess.run,
        ["docker", "exec", "-i", container, "python", "-", uid, sid, rid],
        input=source,
        text=True,
        capture_output=True,
        check=True,
        timeout=45,
    )
    return json.loads(result.stdout)


async def verify_session_delete(client, uid, *, docker_container, retained_session_id):
    """Delete only this call's new, marked session. Report counts without identifiers.

    The caller supplies its freshly registered account and one sibling to retain.
    Passing no container runs HTTP checks only and leaves physical verification pending.
    """
    started = time.monotonic()
    physical = {"verified": False, "before": None, "after": None}
    checks = {}
    report = {"passed": False, "checks": checks, "physical": physical}
    phase = "verify test account"

    def require(name, condition):
        checks[name] = bool(condition)
        if not condition:
            raise ValueError(name)

    async def owned():
        rows = await json_request(client, "GET", "/api/sessions")
        return {row["id"]: row["title"] for row in rows}

    try:
        if docker_container and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", docker_container
        ):
            raise ValueError("invalid service container name")
        current = await json_request(client, "GET", "/api/auth/me")
        existing = await owned()
        require("owner_verified", current["id"] == uid)
        require("retained_session_exists", retained_session_id in existing)

        phase = "seed isolated session"
        nonce = secrets.token_hex(16)
        title = "会话清理验收 · " + nonce
        marker = ("FAMSUN-QA-" + nonce).encode("ascii")
        session = await json_request(
            client, "POST", "/api/sessions", json={"title": title}
        )
        sid = session["id"]
        require(
            "target_created_here",
            bool(re.fullmatch("[a-f0-9]{32}", sid))
            and sid not in existing
            and session["title"] == title,
        )
        base = f"/api/sessions/{sid}"
        run = await json_request(
            client,
            "POST",
            base + "/runs",
            headers={"Idempotency-Key": secrets.token_hex(16)},
            json={
                "prompt": "请调用工具写入 /workspace/marker.txt。文件仅含以下一行，写完简短确认即可。\n"
                + marker.decode("ascii")
            },
        )
        rid = run["id"]
        require("valid_run_identity", bool(re.fullmatch("[a-f0-9]{32}", rid)))
        path = f"{base}/runs/{rid}"
        phase = "verify completed agent run and marker"
        async with asyncio.timeout(600):
            events, terminal = await trace_stream(client, path)
        run = await json_request(client, "GET", path)
        require(
            "durable_run_completed",
            terminal["status"] == run["status"] == "completed"
            and run["id"] == rid
            and bool(events),
        )
        content = await fetch_file(client, base + "/file", "marker.txt", 4096)
        require("marker_matches", content in (marker, marker + b"\n", marker + b"\r\n"))

        phase = "verify persisted data before deletion"
        if docker_container:
            before = physical["before"] = await _inspect(
                docker_container, uid, sid, rid
            )
            require(
                "database_rows_seeded",
                before["sessions"] == before["runs"] == before["request_keys"] == 1
                and before["events"] == len(events)
                and before["cleanup"] == 0,
            )
            require(
                "graph_rows_seeded", before["checkpoints"] > 0 and before["writes"] > 0
            )
            require(
                "both_volumes_tagged",
                before["volumes"] == before["tagged_volumes"] == 2,
            )

        phase = "confirm identity and delete synthetic session"
        current = await json_request(client, "GET", "/api/auth/me")
        visible = await owned()
        require(
            "target_and_sibling_reconfirmed",
            current["id"] == uid
            and sid != retained_session_id
            and visible.get(sid) == title
            and retained_session_id in visible,
        )
        response = await client.delete(base)
        report["delete_http_status"] = response.status_code
        require("delete_acknowledged", response.status_code == 204)

        phase = "verify deleted routes and retained sibling"
        visible = await owned()
        require(
            "only_target_disappeared",
            sid not in visible and existing.keys() <= visible.keys(),
        )
        for name, url, params in (
            ("runs", base + "/runs", {}),
            ("run_detail", path, {}),
            ("events", path + "/events", {}),
            ("files", base + "/files", {}),
            ("marker", base + "/file", {"path": "marker.txt"}),
        ):
            require(
                name + "_unavailable",
                (await client.get(url, params=params)).status_code == 404,
            )

        phase = "verify physical deletion"
        if docker_container:
            after = physical["after"] = await _inspect(docker_container, uid, sid, rid)
            require(
                "all_target_rows_and_volumes_removed",
                all(value == 0 for value in after.values()),
            )
            physical["verified"] = True
        else:
            physical["reason"] = "service container was not supplied"
        report["passed"] = physical["verified"] and all(checks.values())
    except (
        httpx.HTTPError,
        OSError,
        subprocess.SubprocessError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
    ) as exc:
        # HTTP exceptions, subprocess logs and model output may include identities or inputs.
        report["error"] = {"phase": phase, "kind": type(exc).__name__}
        if isinstance(exc, httpx.HTTPStatusError):
            report["error"]["http_status"] = exc.response.status_code
    report["duration_seconds"] = round(time.monotonic() - started, 2)
    return report
