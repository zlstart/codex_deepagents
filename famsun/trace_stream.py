"""Batch bounded, replayable public text deltas for a single model invocation."""

import asyncio

from .public_text import (
    MASK,
    VisibleMarkup,
    message_text,
    public_text,
    redact_text,
    stable_prefix,
)

MODEL_CHARS = 16_384
RUN_CHARS = 131_072
RAW_CHARS = 65_536
DELTA_CHARS = 2048
MAX_BATCHES = 192
BATCH_SECONDS = 0.18


class Transcript:
    def __init__(self, trace, event):
        self.trace = trace
        self.event = event.copy()
        self.markup = VisibleMarkup()
        self.parts = []
        self.length = 0
        self.clipped = False
        self.sent = ""
        self.truncated = False
        self.batches = 0
        self.task = None
        self.closing = None
        self.wake = asyncio.Event()

    def add(self, message):
        if self.closing is not None or self.truncated or self.clipped:
            return
        text = self.markup.feed(message_text(message))
        if not text:
            return
        room = RAW_CHARS - self.length
        self.parts.append(text[:room])
        self.length += min(room, len(text))
        self.clipped = len(text) > room
        if self.task is not None and self.task.done():
            self.task.result()
            self.task = None
        if self.task is None and self.batches < MAX_BATCHES:
            self.wake.clear()
            self.task = asyncio.create_task(self._batch())

    async def _batch(self):
        try:
            async with asyncio.timeout(BATCH_SECONDS):
                await self.wake.wait()
        except TimeoutError:
            pass
        await self.publish()

    async def drain(self):
        task = self.task
        if task is not None:
            self.wake.set()
            await asyncio.shield(task)
            if self.task is task:
                self.task = None

    async def close(self, message):
        if self.closing is None:

            async def finish():
                await self.drain()
                await self.publish(final=True, message=message)

            self.closing = asyncio.create_task(finish())
        await asyncio.shield(self.closing)

    async def publish(self, *, final=False, message=None):
        if self.truncated:
            return
        secrets = self.trace.secrets
        raw = "".join(self.parts)
        if not final or self.clipped:
            raw = stable_prefix(raw, secrets)
        elif message is None:
            # An interrupted response can end halfway through a configured key.
            for secret in secrets:
                for size in range(min(len(secret) - 1, len(raw)), 3, -1):
                    if raw.endswith(secret[:size]):
                        raw = raw[:-size] + MASK
                        break
        candidate = redact_text(raw, secrets)
        # A non-streaming provider still supplies an authoritative AIMessage at
        # on_llm_end. Chunks for a provider's final metadata can also be empty.
        if message is not None:
            complete = public_text(message, secrets)
            if complete:
                candidate = complete
        mismatch = not candidate.startswith(self.sent)
        if mismatch and not final:
            # Credentials split across multiple tokens may still be ambiguous.
            return
        remaining = min(MODEL_CHARS - len(self.sent), self.trace.remaining_text)
        tail = "" if mismatch else candidate[len(self.sent) :]
        delta = tail[:remaining]
        clipped = mismatch or self.clipped or len(tail) > remaining
        if not delta and not clipped:
            return
        self.trace.remaining_text -= len(delta)
        self.sent += delta
        self.batches += 1
        self.truncated = clipped
        for pos in range(0, max(1, len(delta)), DELTA_CHARS):
            payload = {"type": "model_text", "delta": delta[pos : pos + DELTA_CHARS]}
            if clipped and pos + DELTA_CHARS >= len(delta):
                payload["truncated"] = True
            await self.trace.emit({**self.event, "payload": payload})
