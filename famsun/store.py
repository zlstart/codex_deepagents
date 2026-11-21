"""SQLite application state. Each operation takes a short-lived connection."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class Missing(Exception):
    pass


class SessionRevoked(Exception):
    pass


class Conflict(Exception):
    pass


class Busy(Exception):
    pass


ACTIVE = ("queued", "running")
EVENT_PAGE_BYTES = 4 * 1024**2
EVENT_PAYLOAD_BYTES = 128 * 1024


def encode_payload(payload):
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise TypeError("event payload must be an object")
    data = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    if len(data.encode()) <= EVENT_PAYLOAD_BYTES:
        return data
    # A single unexpectedly large public callback must not cancel the job.
    # The harness normally applies its field-specific bounds and redaction first.
    if payload.get("type") == "model_text" and isinstance(payload.get("delta"), str):
        prefix = (
            payload["delta"]
            .encode()[: EVENT_PAYLOAD_BYTES // 8]
            .decode(errors="ignore")
        )
        omitted = {"type": "model_text", "delta": prefix, "truncated": True}
    else:
        omitted = {
            "type": "tool",
            "truncated": True,
            "arguments_truncated": True,
            "result_truncated": True,
            "truncation_note": "结构化记录超过单条存储上限",
        }
    return json.dumps(omitted, ensure_ascii=False, separators=(",", ":"))


_TITLE_END = re.compile(r"[。！？!?：\r\n]|\.(?=\s|$)|:(?=\s)")


def suggested_title(prompt: str) -> str:
    title = next(
        (part.strip() for part in _TITLE_END.split(prompt.strip()) if part.strip()),
        "新会话",
    )
    return title if len(title) <= 28 else title[:27] + "…"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class Store:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def db(self, *, write=False):
        con = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA foreign_keys=ON")
            if write:
                con.execute("BEGIN IMMEDIATE")
            yield con
            if write:
                con.commit()
        finally:
            con.close()

    def initialize(self):
        with self.db() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                    password_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tokens (
                    digest TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id),
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS token_owner ON tokens(user_id,expires_at);
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id),
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS session_owner ON sessions(user_id,updated_at);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL,
                    answer TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS run_session ON runs(session_id,created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS session_active ON runs(session_id)
                    WHERE status IN ('queued','running');
                CREATE TABLE IF NOT EXISTS run_requests (
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    request_key TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    PRIMARY KEY (session_id, request_key)
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    id TEXT NOT NULL,
                    parent_id TEXT,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT,
                    payload TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS event_run ON events(run_id,seq);
                CREATE TABLE IF NOT EXISTS cleanup (
                    session_id TEXT PRIMARY KEY REFERENCES sessions(id)
                );
            """)
        # BEGIN IMMEDIATE also serializes migration with the admin account CLI.
        # Existing append-only records acquire a null payload, retaining their seq.
        with self.db(write=True) as con:
            if "payload" not in {
                row["name"] for row in con.execute("PRAGMA table_info(events)")
            }:
                con.execute("ALTER TABLE events ADD COLUMN payload TEXT")

    def create_user(self, username: str, password_hash: str):
        uid = uuid4().hex
        try:
            with self.db(write=True) as con:
                con.execute(
                    "INSERT INTO users VALUES (?,?,?)", (uid, username, password_hash)
                )
        except sqlite3.IntegrityError:
            raise Conflict("用户名不可用") from None
        return {"id": uid, "username": username}

    def credentials(self, username: str):
        with self.db() as con:
            row = con.execute(
                "SELECT * FROM users WHERE username=?", (username,)
            ).fetchone()
            return dict(row) if row else None

    def save_token(self, user_id: str, digest: str, expires: int):
        with self.db(write=True) as con:
            con.execute("DELETE FROM tokens WHERE expires_at<=?", (int(time.time()),))
            con.execute("INSERT INTO tokens VALUES (?,?,?)", (digest, user_id, expires))
            # Retain the most recent logins in case a browser lost its cookies.
            con.execute(
                """DELETE FROM tokens WHERE digest IN (
                    SELECT digest FROM tokens WHERE user_id=?
                    ORDER BY expires_at DESC,rowid DESC LIMIT -1 OFFSET 20
                )""",
                (user_id,),
            )

    def user_by_token(self, digest: str):
        with self.db() as con:
            row = con.execute(
                """SELECT u.id,u.username FROM users u JOIN tokens t ON t.user_id=u.id
                                WHERE t.digest=? AND t.expires_at>?""",
                (digest, int(time.time())),
            ).fetchone()
            return dict(row) if row else None

    def revoke(self, digest: str):
        with self.db(write=True) as con:
            con.execute("DELETE FROM tokens WHERE digest=?", (digest,))

    @staticmethod
    def own(con, uid, sid):
        row = con.execute(
            "SELECT * FROM sessions WHERE id=? AND user_id=?", (sid, uid)
        ).fetchone()
        if not row:
            raise Missing
        return row

    @staticmethod
    def public_session(row):
        return {key: row[key] for key in ("id", "title", "created_at", "updated_at")}

    def own_session(self, uid, sid):
        with self.db() as con:
            return self.public_session(self.own(con, uid, sid))

    def sessions(self, uid):
        with self.db() as con:
            return [
                self.public_session(row)
                for row in con.execute(
                    "SELECT * FROM sessions WHERE user_id=? ORDER BY updated_at DESC",
                    (uid,),
                )
            ]

    def create_session(self, uid, title):
        sid = uuid4().hex
        stamp = now()
        with self.db(write=True) as con:
            con.execute(
                "INSERT INTO sessions VALUES (?,?,?,?,?)",
                (sid, uid, title, stamp, stamp),
            )
            return self.public_session(self.own(con, uid, sid))

    def runs(self, uid, sid):
        with self.db() as con:
            self.own(con, uid, sid)
            return [
                dict(row)
                for row in con.execute(
                    "SELECT * FROM runs WHERE session_id=? ORDER BY created_at,id",
                    (sid,),
                )
            ]

    @staticmethod
    def run_row(con, sid, rid):
        row = con.execute(
            "SELECT * FROM runs WHERE session_id=? AND id=?", (sid, rid)
        ).fetchone()
        if not row:
            raise Missing
        return dict(row)

    def run(self, uid, sid, rid):
        with self.db() as con:
            self.own(con, uid, sid)
            return self.run_row(con, sid, rid)

    def new_run(self, uid, sid, prompt, max_parallel, *, request_key=None, run_id=None):
        rid = run_id or uuid4().hex
        stamp = now()
        with self.db(write=True) as con:
            session = self.own(con, uid, sid)
            if request_key:
                previous = con.execute(
                    "SELECT run_id FROM run_requests WHERE session_id=? AND request_key=?",
                    (sid, request_key),
                ).fetchone()
                if previous:
                    record = self.run_row(con, sid, previous["run_id"])
                    if record["prompt"] != prompt:
                        raise Conflict("相同请求编号已经用于其他任务")
                    return record
            if con.execute(
                "SELECT 1 FROM runs WHERE session_id=? AND status IN ('queued','running')",
                (sid,),
            ).fetchone():
                raise Conflict("此会话的上一项任务仍在执行")
            if con.execute(
                "SELECT 1 FROM cleanup WHERE session_id=?", (sid,)
            ).fetchone():
                raise Busy("此会话沙箱等待清理")
            if (
                con.execute(
                    "SELECT count(*) FROM runs WHERE status IN ('queued','running')"
                ).fetchone()[0]
                >= max_parallel
            ):
                raise Busy("当前执行资源已占满，请稍后重试")
            title = (
                suggested_title(prompt)
                if session["title"] == "新会话"
                else session["title"]
            )
            con.execute(
                "UPDATE sessions SET title=?,updated_at=? WHERE id=?",
                (title, stamp, sid),
            )
            con.execute(
                "INSERT INTO runs VALUES (?,?,?,'queued',NULL,NULL,?,?)",
                (rid, sid, prompt, stamp, stamp),
            )
            if request_key:
                con.execute(
                    "INSERT INTO run_requests VALUES (?,?,?)", (sid, request_key, rid)
                )
            con.execute("INSERT INTO cleanup VALUES (?)", (sid,))
            self._event(
                con,
                rid,
                {"id": rid, "kind": "run", "label": "执行任务", "status": "queued"},
            )
            return self.run_row(con, sid, rid)

    @staticmethod
    def _event(con, rid, event):
        con.execute(
            """INSERT INTO events(run_id,id,parent_id,kind,label,status,detail,payload,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                rid,
                event["id"],
                event.get("parent_id"),
                event["kind"],
                event["label"],
                event["status"],
                event.get("detail"),
                encode_payload(event.get("payload")),
                now(),
            ),
        )

    def append_event(self, rid, event):
        with self.db(write=True) as con:
            active = con.execute(
                "SELECT 1 FROM runs WHERE id=? AND status IN ('queued','running')",
                (rid,),
            ).fetchone()
            if active:
                self._event(con, rid, event)

    def start(self, rid):
        with self.db(write=True) as con:
            updated = con.execute(
                "UPDATE runs SET status='running',updated_at=? WHERE id=? AND status='queued'",
                (now(), rid),
            ).rowcount
            if updated:
                self._event(
                    con,
                    rid,
                    {
                        "id": rid,
                        "kind": "run",
                        "label": "执行任务",
                        "status": "running",
                    },
                )

    def finish(self, rid, status, answer=None, error=None):
        stamp = now()
        with self.db(write=True) as con:
            row = con.execute(
                "SELECT session_id,status FROM runs WHERE id=?", (rid,)
            ).fetchone()
            if not row or row["status"] not in ACTIVE:
                return
            if status != "completed":
                # A cancellation or process restart can interrupt any open child span.
                open_rows = con.execute(
                    """SELECT e.* FROM events e JOIN
                    (SELECT id,MAX(seq) AS seq FROM events WHERE run_id=? GROUP BY id) latest
                    ON e.seq=latest.seq WHERE e.id!=? AND e.status IN ('running','queued')""",
                    (rid, rid),
                ).fetchall()
                for event in open_rows:
                    self._event(
                        con, rid, {**dict(event), "status": status, "payload": None}
                    )
            self._event(
                con,
                rid,
                {
                    "id": rid,
                    "kind": "run",
                    "label": "执行任务",
                    "status": status,
                    "detail": error,
                },
            )
            con.execute(
                "UPDATE runs SET status=?,answer=?,error=?,updated_at=? WHERE id=?",
                (status, answer, error, stamp, rid),
            )
            con.execute(
                "UPDATE sessions SET updated_at=? WHERE id=?",
                (stamp, row["session_id"]),
            )

    def events(self, uid, sid, rid, after, *, token_digest=None):
        with self.db() as con:
            # One snapshot covers ownership, token validity, terminal status and
            # final rows. Release it before yielding anything to an SSE client.
            con.execute("BEGIN")
            if (
                token_digest is not None
                and not con.execute(
                    "SELECT 1 FROM tokens WHERE digest=? AND user_id=? AND expires_at>?",
                    (token_digest, uid, int(time.time())),
                ).fetchone()
            ):
                raise SessionRevoked
            run = con.execute(
                """SELECT r.status FROM runs r JOIN sessions s ON r.session_id=s.id
                              WHERE r.id=? AND s.id=? AND s.user_id=?""",
                (rid, sid, uid),
            ).fetchone()
            if not run:
                raise Missing
            rows = con.execute(
                """SELECT seq,id,parent_id,kind,label,status,detail,payload,created_at
                               FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT 501""",
                (rid, after),
            )
            page = []
            has_more = False
            size = 0
            for row in rows:
                event = dict(row)
                if event["payload"] is not None:
                    event["payload"] = json.loads(event["payload"])
                event_size = (
                    len(
                        json.dumps(
                            event, ensure_ascii=False, separators=(",", ":")
                        ).encode()
                    )
                    + 1
                )
                if len(page) == 500 or (page and size + event_size > EVENT_PAGE_BYTES):
                    has_more = True
                    break
                page.append(event)
                size += event_size
            return {
                "events": page,
                "cursor": page[-1]["seq"] if page else after,
                "has_more": has_more,
                "status": run["status"],
            }

    def recovery(self):
        with self.db() as con:
            active = [
                row[0]
                for row in con.execute(
                    "SELECT id FROM runs WHERE status IN ('queued','running')"
                )
            ]
        for rid in active:
            self.finish(rid, "failed", error="服务已重新启动，任务执行中断")

    def pending_cleanup(self):
        with self.db() as con:
            return [
                dict(row)
                for row in con.execute("""SELECT s.id,s.user_id FROM sessions s
                    JOIN cleanup c ON c.session_id=s.id WHERE NOT EXISTS (SELECT 1 FROM runs r
                    WHERE r.session_id=s.id AND r.status IN ('queued','running'))""")
            ]

    def cleared(self, sid):
        with self.db(write=True) as con:
            con.execute("DELETE FROM cleanup WHERE session_id=?", (sid,))
