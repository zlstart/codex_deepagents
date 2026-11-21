"""Decode real Langfuse SDK exports at a loopback OTLP HTTP receiver."""

import asyncio
import base64
import gzip
import json
import shutil
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from deepagents.backends import FilesystemBackend
from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from test_harness import ScriptedChat

from famsun import harness, telemetry
from famsun.config import LangfuseConfig, Settings


class Receiver(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        if self.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        packet = ExportTraceServiceRequest.FromString(raw)
        self.server.received.append(
            (self.path, {k.lower(): v for k, v in self.headers.items()}, packet)
        )
        if self.server.gate is not None:
            self.server.arrived.set()
            self.server.gate.wait(2)
        self.send_response(self.server.response_status)
        self.end_headers()


@pytest.fixture
def receiver():
    service = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    service.response_status = 200
    service.gate = None
    service.arrived = threading.Event()
    service.received = []
    worker = threading.Thread(target=service.serve_forever, daemon=True)
    worker.start()
    try:
        yield service
    finally:
        service.shutdown()
        service.server_close()
        worker.join()


def settings(receiver, directory, capture=False):
    return SimpleNamespace(
        langfuse=LangfuseConfig(
            f"http://127.0.0.1:{receiver.server_port}",
            "pk-lf-" + uuid4().hex,
            "lf-secret-unit",
            capture,
        ),
        llm_model="provider/test",
        llm_api_key="provider-unit-secret",
        checkpoint_path=directory / "state.sqlite",
        mcp_enabled=False,
    )


class MeteredModel(ScriptedChat):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        result = super()._generate(
            messages, stop=stop, run_manager=run_manager, **kwargs
        )
        msg = result.generations[0].message
        msg.usage_metadata = {
            "input_tokens": 10,
            "output_tokens": 4,
            "total_tokens": 14,
        }
        msg.additional_kwargs["reasoning_content"] = "private-provider-thought"
        if msg.content == "validated":
            msg.content = "<think>private-markup</think>validated provider-unit-secret"
        return result


async def discard(event):
    pass


@pytest.mark.parametrize("capture", [False, True])
async def test_deepagents_real_otlp_export(tmp_path, receiver, monkeypatch, capture):
    config = settings(receiver, tmp_path, capture)
    shutil.copytree(
        Path(harness.__file__).parent / "skills", tmp_path / "box/opt/famsun/skills"
    )
    monkeypatch.setattr(harness, "build_model", lambda _: MeteredModel())
    backend = FilesystemBackend(root_dir=tmp_path / "box")
    ids = telemetry.trace_ids(config.langfuse, "raw-user", "raw-session", "raw-run")
    try:
        answer = await harness.run_agent(
            user_id="raw-user",
            session_id="raw-session",
            run_id="raw-run",
            prompt="delegate both",
            settings=config,
            sandbox=backend,
            emit=discard,
        )
        assert answer == "validated [已隐藏]"
        await asyncio.wait_for(telemetry._service.flushing, 4)
        assert receiver.received
    finally:
        await telemetry.shutdown_telemetry()
    expected_auth = (
        "Basic "
        + base64.b64encode(
            (config.langfuse.public_key + ":lf-secret-unit").encode()
        ).decode()
    )
    spans = []
    for path, headers, packet in receiver.received:
        assert path == "/api/public/otel/v1/traces"
        assert headers["authorization"] == expected_auth
        assert headers["x-langfuse-ingestion-version"] == "4"
        raw = json.dumps(MessageToDict(packet))
        for hidden in (
            "lf-secret-unit",
            "provider-unit-secret",
            "private-provider-thought",
            "private-markup",
            "raw-user",
            "raw-session",
            "raw-run",
            "You are FAMSUN Agent",
        ):
            assert hidden not in raw
        spans.extend(
            s
            for res in packet.resource_spans
            for scope in res.scope_spans
            for s in scope.spans
        )
    assert {s.trace_id.hex() for s in spans} == {ids["trace_id"]}
    by_id = {s.span_id: s for s in spans}
    assert len(by_id) == len(spans)
    root = next(s for s in spans if s.name == "FAMSUN Agent")
    assert all(s is root or s.parent_span_id in by_id for s in spans)
    attrs = lambda s: {a.key: a.value.string_value for a in s.attributes}
    assert (
        attrs(root)["user.id"] == ids["user_id"]
        and attrs(root)["session.id"] == ids["session_id"]
    )
    delegates = [s for s in spans if s.name in ("task/reviewer", "task/engineering")]
    assert len(delegates) == 2

    def ancestors(span):
        parents = []
        while span.parent_span_id in by_id:
            span = by_id[span.parent_span_id]
            parents.append(span.name)
        return parents

    reads = [s for s in spans if s.name == "read_file"]
    assert len(reads) == 2
    assert all("FAMSUN Agent" in ancestors(s) for s in reads)
    failed = next(s for s in reads if s.status.code == 2)
    assert "task/reviewer" in ancestors(failed)
    assert "task/engineering" in ancestors(next(s for s in reads if s.status.code != 2))
    generations = [
        s for s in spans if attrs(s).get("langfuse.observation.type") == "generation"
    ]
    assert len(generations) == 6
    assert all(
        json.loads(attrs(s)["langfuse.observation.usage_details"])["total"] == 14
        for s in generations
    )
    if capture:
        assert "delegate both" in attrs(root)["langfuse.observation.input"]
        assert "validated" in attrs(root)["langfuse.observation.output"]
        assert (
            "/opt/famsun" in attrs(reads[0])["langfuse.observation.input"]
            or "file_path" in attrs(reads[0])["langfuse.observation.input"]
        )
    else:
        assert all(
            "langfuse.observation.input" not in attrs(s)
            and "langfuse.observation.output" not in attrs(s)
            for s in spans
        )
    evidence = {
        "capture_content": capture,
        "scope": "real Deep Agents graph, controlled model, Langfuse SDK, loopback HTTP OTLP/protobuf",
        "sdk": headers["x-langfuse-sdk-version"],
        "ingestion_version": headers["x-langfuse-ingestion-version"],
        "path": path,
        "span_count": len(spans),
        "observation_types": dict(
            Counter(attrs(s)["langfuse.observation.type"] for s in spans)
        ),
        "delegated_tools": sorted(s.name for s in delegates),
        "failed_read_branch": "reviewer",
        "verified_properties": [
            "single trace",
            "all child parents resolve",
            "HMAC user/session",
            "no raw identity, secrets, hidden reasoning or system message",
            "model token usage",
            "run-triggered flush",
        ],
    }
    (tmp_path / "wire.json").write_text(json.dumps(evidence, indent=2))


async def test_collector_failure_does_not_fail_run(receiver, tmp_path, monkeypatch):
    receiver.response_status = 401
    receiver.gate = threading.Event()
    cfg = settings(receiver, tmp_path)
    monkeypatch.setattr(harness, "build_model", lambda _: ScriptedChat())
    try:
        result = await harness.run_agent(
            user_id="u",
            session_id="s",
            run_id="run",
            prompt="ordinary",
            emit=discard,
            settings=cfg,
            sandbox=FilesystemBackend(root_dir=tmp_path / "box"),
        )
        assert result == "ordinary"
        assert await asyncio.to_thread(receiver.arrived.wait, 2)
        assert not telemetry._service.flushing.done()
    finally:
        receiver.gate.set()
        await telemetry.shutdown_telemetry()
    assert receiver.received


async def test_concurrent_failed_and_cancelled_runs(receiver, tmp_path, monkeypatch):
    started = asyncio.Event()

    class Interrupted(ScriptedChat):
        async def _agenerate(self, messages, **kwargs):
            if messages[-1].content == "cancel this":
                started.set()
                await asyncio.Future()
            raise RuntimeError("PRIVATE_ERROR_DETAILS")

    monkeypatch.setattr(harness, "build_model", lambda _: Interrupted())
    cfg = settings(receiver, tmp_path)

    async def run(user, prompt):
        return await harness.run_agent(
            user_id=user,
            session_id="same",
            run_id=user,
            prompt=prompt,
            settings=cfg,
            sandbox=FilesystemBackend(root_dir=tmp_path / user),
            emit=discard,
        )

    pending = asyncio.create_task(run("one-user", "cancel this"))
    try:
        await asyncio.wait_for(started.wait(), 3)
        with pytest.raises(RuntimeError, match="PRIVATE_ERROR_DETAILS"):
            await run("other-user", "fail")
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await telemetry.shutdown_telemetry()

    roots = {}
    for _, _, packet in receiver.received:
        assert "PRIVATE_ERROR_DETAILS" not in str(packet)
        for res in packet.resource_spans:
            for scope in res.scope_spans:
                for span in scope.spans:
                    if span.name == "FAMSUN Agent":
                        roots[span.trace_id.hex()] = {
                            a.key: a.value.string_value for a in span.attributes
                        }
    assert len(roots) == 2
    for user, status in [("one-user", "cancelled"), ("other-user", "failed")]:
        ids = telemetry.trace_ids(cfg.langfuse, user, "same", user)
        root = roots[ids["trace_id"]]
        assert root["langfuse.observation.status_message"] == status
        assert root["session.id"] == ids["session_id"]
        assert root["user.id"] == ids["user_id"]


def test_disabled_config_and_identity_domains(monkeypatch):
    monkeypatch.delenv("FAMSUN_LANGFUSE_ENABLED", raising=False)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "must-never-auto-enable")
    assert Settings().langfuse is None
    assert telemetry.start_telemetry(Settings(), "u", "s", "r", "private") is None
    monkeypatch.setenv("FAMSUN_LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "")
    with pytest.raises(ValueError, match="LANGFUSE_BASE_URL"):
        Settings()
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://langfuse:3000/")
    config = Settings().langfuse
    assert config.base_url == "http://langfuse:3000"
    assert config.secret_key not in repr(config)
    first = telemetry.trace_ids(config, "u", "a", "1")
    continued = telemetry.trace_ids(config, "u", "a", "2")
    other = telemetry.trace_ids(config, "v", "a", "1")
    assert (
        first["user_id"] == continued["user_id"]
        and first["session_id"] == continued["session_id"]
    )
    assert first["trace_id"] != continued["trace_id"]
    assert all(first[k] != other[k] for k in first)
