"""Regression checks for tenant boundaries and concurrent admission to a SQLite worker."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from famsun.config import Settings
from famsun.store import Busy, Conflict, Missing, Store


@pytest.fixture
def users(tmp_path):
    db = Store(tmp_path / "test.sqlite3")
    db.initialize()
    a = db.create_user("alice", "unused-test-value")["id"]
    b = db.create_user("bruno", "unused-test-value")["id"]
    return db, a, b


def test_run_ownership_checks_both_session_and_user(users):
    db, alice, bruno = users
    first = db.create_session(alice, "first")["id"]
    second = db.create_session(alice, "second")["id"]
    other = db.create_session(bruno, "other")["id"]
    run = db.new_run(alice, first, "private question", 2)["id"]
    for account, session in [(bruno, first), (bruno, other), (alice, second)]:
        with pytest.raises(Missing):
            db.run(account, session, run)
        with pytest.raises(Missing):
            db.events(account, session, run, 0)
    assert [r["id"] for r in db.sessions(bruno)] == [other]
    assert [r["id"] for r in db.runs(alice, first)] == [run]


def test_database_rejects_overlapping_run_creation(users):
    db, alice, _ = users
    sid = db.create_session(alice, "model")["id"]
    together = Barrier(8)

    def start():
        together.wait(5)
        try:
            return db.new_run(alice, sid, "concurrent", 8)["id"]
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        winners = [rid for rid in pool.map(lambda _: start(), range(8)) if rid]
    assert len(winners) == 1
    assert [run["id"] for run in db.runs(alice, sid)] == winners


def test_capacity_and_interrupted_session_do_not_block_other_users(users):
    db, alice, bruno = users
    a = db.create_session(alice, "first")["id"]
    b = db.create_session(bruno, "second")["id"]
    rid = db.new_run(alice, a, "initial", 1)["id"]
    with pytest.raises(Busy):
        db.new_run(bruno, b, "queued", 1)
    db.start(rid)
    db.append_event(
        rid,
        {
            "id": "sub",
            "kind": "subagent",
            "parent_id": rid,
            "label": "verification",
            "status": "running",
        },
    )
    db.recovery()
    assert db.run(alice, a, rid)["status"] == "failed"
    last = {row["id"]: row["status"] for row in db.events(alice, a, rid, 0)["events"]}
    assert last == {rid: "failed", "sub": "failed"}
    with pytest.raises(Busy):
        db.new_run(alice, a, "waiting for cleanup", 1)
    assert db.new_run(bruno, b, "independent", 1)["session_id"] == b


def test_provider_secret_not_in_settings_repr(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "synthetic-secret-for-repr-test")
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.example/v1/responses")
    configuration = Settings()
    assert configuration.llm_base_url == "https://provider.example/v1"
    assert configuration.llm_api_key not in repr(configuration)


def test_tls_proxy_accepts_external_origin_only(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from famsun.api import create_app

    monkeypatch.setenv("FAMSUN_PUBLIC_ORIGIN", "https://studio.example")
    settings = Settings(data_dir=tmp_path, allow_signup=False)
    with TestClient(
        create_app(settings), base_url="http://app.internal:8000"
    ) as client:
        for origin in [
            "https://other.example",
            "null",
            "http://studio.example",
            "https://studio.example:8888",
        ]:
            response = client.post("/api/auth/logout", headers={"Origin": origin})
            assert response.status_code == 403
        approved = client.post(
            "/api/auth/logout", headers={"Origin": "https://studio.example"}
        )
        assert approved.status_code == 204
        assert "secure" in approved.headers["set-cookie"].lower()


def test_login_validation_does_not_echo_password(tmp_path):
    from fastapi.testclient import TestClient

    from famsun.api import create_app

    settings = Settings(data_dir=tmp_path, allow_signup=False)
    with TestClient(create_app(settings)) as client:
        # An overlong password is rejected by the schema before verification.
        marker = "synthetic-sensitive-password-for-validation"
        response = client.post(
            "/api/auth/login", json={"username": "alice", "password": marker * 4}
        )
        assert response.status_code == 422
        assert marker not in response.text
        assert "no-store" in response.headers["Cache-Control"]
