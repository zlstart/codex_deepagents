"""Opt-in, per-run Langfuse traces. The service owns one background exporter."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
from contextlib import contextmanager

from .config import LangfuseConfig
from .public_text import VisibleMarkup, redact_text
from .trace_io import tool_arguments

log = logging.getLogger(__name__)
_service = None


def trace_ids(
    config: LangfuseConfig, user_id: str, session_id: str, run_id: str
) -> dict:
    def digest(*parts):
        value = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()
        return hmac.new(config.secret_key.encode(), value, hashlib.sha256).hexdigest()[
            :32
        ]

    return {
        "user_id": digest("user", user_id),
        "session_id": digest("session", user_id, session_id),
        "trace_id": digest("run", user_id, session_id, run_id),
    }


@contextmanager
def best_effort(operation):
    try:
        yield
    except Exception as exc:  # noqa: BLE001 -- an observer must never fail its caller.
        log.warning("Langfuse %s failed (%s)", operation, type(exc).__name__)


class ExportService:
    def __init__(self, config):
        from langfuse import Langfuse
        from langfuse.span_filter import is_langfuse_span
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider

        # The SDK handles media before its span mask. Disable the separate upload
        # queue even when an administrator opts into text previews.
        os.environ["LANGFUSE_MEDIA_UPLOAD_ENABLED"] = "false"
        self.provider = TracerProvider(
            resource=Resource({"service.name": "famsun-agent"}), shutdown_on_exit=False
        )
        self.client = Langfuse(
            public_key=config.public_key,
            secret_key=config.secret_key,
            base_url=config.base_url,
            tracer_provider=self.provider,
            should_export_span=is_langfuse_span,
            mask_otel_spans=export_mask(config.capture_content),
            timeout=2,
            flush_interval=2,
        )
        self.dirty = False
        self.flushing = None

    def request_flush(self):
        self.dirty = True
        if self.flushing is None or self.flushing.done():
            self.flushing = asyncio.create_task(self._flush())

    async def _flush(self):
        while self.dirty:
            self.dirty = False
            with best_effort("flush"):
                await asyncio.to_thread(self.client.flush)

    async def close(self):
        if self.flushing:
            await self.flushing
        try:
            await asyncio.to_thread(self.client.shutdown)
        finally:
            await asyncio.to_thread(self.provider.shutdown)


def export_mask(capture):
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    # The list is closed: dependency upgrades cannot introduce unreviewed fields.
    allowed = {
        "user.id",
        "session.id",
        "langfuse.trace.name",
        "langfuse.internal.as_root",
        "langfuse.internal.is_app_root",
        "langfuse.observation.type",
        "langfuse.observation.level",
        "langfuse.observation.status_message",
        "langfuse.observation.model.name",
        "langfuse.observation.completion_start_time",
        "langfuse.observation.usage_details",
        "langfuse.observation.cost_details",
    }
    if capture:
        allowed |= {"langfuse.observation.input", "langfuse.observation.output"}

    def mask(*, params):
        return MaskOtelSpansResult(
            span_patches={
                key: OtelSpanPatch(
                    delete_attributes=tuple(set(span.attributes) - allowed)
                )
                for key, span in params.spans.items()
            }
        )

    return mask


class RunTelemetry:
    def __init__(self, service, settings, ids, prompt):
        from langfuse import propagate_attributes

        from .telemetry_callback import ProjectCallback

        self.service = service
        config = settings.langfuse
        self.capture = config.capture_content
        self.secrets = (settings.llm_api_key, config.secret_key, config.public_key)
        self.redact = lambda value: redact_text(
            VisibleMarkup().feed(value), self.secrets
        )
        with propagate_attributes(
            user_id=ids["user_id"],
            session_id=ids["session_id"],
            trace_name="FAMSUN Agent",
        ):
            self.root = service.client.start_observation(
                name="FAMSUN Agent",
                as_type="agent",
                trace_context={"trace_id": ids["trace_id"]},
                input=self.preview(prompt),
            )
        self.handler = ProjectCallback(
            config=config,
            root=self.root,
            ids=ids,
            redact=self.redact,
            model=settings.llm_model,
        )

    def preview(self, value):
        if self.capture and value:
            return tool_arguments("message", {"text": value}, self.redact)[
                "arguments"
            ].get("text")
        return None

    def finish(self, status, answer):
        with best_effort("trace finalization"):
            level = {"completed": "DEFAULT", "failed": "ERROR", "cancelled": "WARNING"}[
                status
            ]
            self.root.update(
                level=level, status_message=status, output=self.preview(answer)
            ).end()
            self.service.request_flush()


def start_telemetry(settings, user_id, session_id, run_id, prompt):
    global _service
    config = getattr(settings, "langfuse", None)
    if config is None:
        return None
    with best_effort("trace initialization"):
        if _service is None:
            _service = ExportService(config)
        return RunTelemetry(
            _service, settings, trace_ids(config, user_id, session_id, run_id), prompt
        )
    return None


async def shutdown_telemetry():
    global _service
    service, _service = _service, None
    if service is not None:
        with best_effort("shutdown"):
            await service.close()


def main():
    """Resolve a local run to its trace without printing identities or project keys."""
    import argparse
    import sqlite3

    from .config import Settings

    parser = argparse.ArgumentParser(
        description="Look up the Langfuse trace ID for a FAMSUN run"
    )
    parser.add_argument("run_id")
    args = parser.parse_args()
    settings = Settings()
    if settings.langfuse is None:
        parser.exit(2, "Langfuse is disabled.\n")
    db = settings.data_dir / "studio.sqlite3"
    if not db.exists():
        parser.exit(1, "FAMSUN database not found.\n")
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT s.user_id, s.id FROM runs r JOIN sessions s ON s.id=r.session_id WHERE r.id=?",
            (args.run_id,),
        ).fetchone()
    if row is None:
        parser.exit(1, "Run not found.\n")
    print(trace_ids(settings.langfuse, *row, args.run_id)["trace_id"])


if __name__ == "__main__":
    main()
