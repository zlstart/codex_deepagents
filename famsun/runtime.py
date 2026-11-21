"""Supervise the single API process's background jobs and durable run records."""

import asyncio
import logging
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

log = logging.getLogger("famsun.runtime")
run_deadline: ContextVar[float | None] = ContextVar("run_deadline", default=None)


class BudgetClosed(RuntimeError):
    public_message = "运行预算已触发收尾，任务未通过完整验收。"

    def __init__(self, answer):
        super().__init__(self.public_message)
        self.answer = answer


@dataclass
class Job:
    session_id: str
    supervisor: asyncio.Task | None = None
    execution: asyncio.Task | None = None
    cancel_requested: bool = False


class Jobs:
    def __init__(self, store, settings, runner, sandbox_factory, *, discard_previews):
        self.store = store
        self.settings = settings
        self.runner = runner
        self.sandbox_factory = sandbox_factory
        self.tasks = {}
        self.subscribers = {}
        self.deletions = {}
        self.delete_slots = asyncio.Semaphore(2)
        self.workspace_requests = {}
        self.discard_previews = discard_previews
        self.reaper = None

    @contextmanager
    def subscribe(self, rid):
        """A coalesced wake-up signal per connection, without buffering events."""
        signal = asyncio.Event()
        signals = self.subscribers.setdefault(rid, set())
        signals.add(signal)
        try:
            yield signal
        finally:
            signals.discard(signal)
            if not signals:
                self.subscribers.pop(rid, None)

    def notify(self, rid):
        for signal in self.subscribers.get(rid, ()):
            signal.set()

    def box(self, uid, sid):
        return self.sandbox_factory(user_id=uid, session_id=sid, settings=self.settings)

    async def _cleanup(self, uid, sid, sandbox=None):
        try:
            box = sandbox or self.box(uid, sid)
            await asyncio.to_thread(box.stop)
            await asyncio.to_thread(self.store.cleared, sid)
            return True
        except Exception:
            log.exception("Sandbox cleanup pending for session %s", sid)
            return False

    @asynccontextmanager
    async def workspace_access(self, uid, sid):
        # Register before checking the DB: when the tombstone commits, every
        # operation that passed ownership is already visible to the deleter.
        key = (uid, sid)
        requests = self.workspace_requests.setdefault(key, set())
        finished = asyncio.Event()
        requests.add(finished)
        try:
            await asyncio.to_thread(self.store.own_session, uid, sid)
            yield
        finally:
            finished.set()
            requests.discard(finished)
            if not requests:
                self.workspace_requests.pop(key, None)

    def _schedule_deletion(self, uid, sid):
        if task := self.deletions.get(sid):
            return task
        task = asyncio.create_task(
            self._delete_marked(uid, sid), name=f"famsun-delete-{sid}"
        )
        self.deletions[sid] = task
        task.add_done_callback(lambda _: self.deletions.pop(sid, None))
        return task

    async def delete_session(self, uid, sid):
        # Always check ownership in SQLite before joining an in-flight task.
        await asyncio.to_thread(self.store.mark_for_deletion, uid, sid)
        return await asyncio.shield(self._schedule_deletion(uid, sid))

    async def _delete_marked(self, uid, sid):
        from .session_checkpoints import remove_session_checkpoints

        try:
            async with self.delete_slots:
                # A completed HTTP retry can race the recovery scan's snapshot.
                if not await asyncio.to_thread(
                    self.store.deletion_is_pending, uid, sid
                ):
                    return True
                readers = tuple(self.workspace_requests.get((uid, sid), ()))
                if readers:
                    await asyncio.gather(*(reader.wait() for reader in readers))
                await self.discard_previews(uid, sid)
                await asyncio.to_thread(self.box(uid, sid).delete)
                await remove_session_checkpoints(
                    self.settings.checkpoint_path, uid, sid
                )
                await asyncio.to_thread(self.store.finalize_deletion, uid, sid)
            return True
        except Exception as exc:  # noqa: BLE001 - retain the durable tombstone for retry.
            log.error("Session deletion pending for %s (%s)", sid, type(exc).__name__)
            return False

    async def recover(self):
        await asyncio.to_thread(self.store.recovery)
        # Unclean shutdowns leave a durable cleanup marker. Block further runs
        # for that session until any surviving execution container is stopped.
        await self.reap_idle()
        self.reaper = asyncio.create_task(self._reap_periodically())

    async def reap_idle(self):
        for row in await asyncio.to_thread(self.store.pending_deletions):
            self._schedule_deletion(row["user_id"], row["id"])
        for row in await asyncio.to_thread(self.store.pending_cleanup):
            sid = row["id"]
            if any(job.session_id == sid for job in self.tasks.values()):
                continue
            await self._cleanup(row["user_id"], sid)

    async def _reap_periodically(self):
        while True:
            await asyncio.sleep(30)
            await self.reap_idle()

    def submit(self, uid, sid, run):
        job = Job(sid)
        job.supervisor = asyncio.create_task(
            self._execute(uid, sid, run, job), name=f"famsun-{run['id']}"
        )
        self.tasks[run["id"]] = job
        job.supervisor.add_done_callback(lambda _: self.tasks.pop(run["id"], None))

    async def cancel(self, rid):
        job = self.tasks.get(rid)
        if job:
            job.cancel_requested = True
            if (
                job.execution
                and not job.execution.done()
                and not job.execution.cancelling()
            ):
                job.execution.cancel()
            # Once execution returns, stop and database finalization must finish even
            # if another cancel arrives or the requesting browser disconnects.
            await asyncio.shield(job.supervisor)

    async def shutdown(self):
        if self.reaper:
            self.reaper.cancel()
            await asyncio.gather(self.reaper, return_exceptions=True)
        await asyncio.gather(*(self.cancel(rid) for rid in tuple(self.tasks)))
        await asyncio.gather(*tuple(self.deletions.values()))

    async def _execute(self, uid, sid, run, job):
        rid = run["id"]
        box = None
        start_task = None
        state, answer, error = "failed", None, None

        async def emit(event):
            await asyncio.to_thread(self.store.append_event, rid, event)
            self.notify(rid)

        async def execute():
            nonlocal box, start_task
            await asyncio.to_thread(self.store.start, rid)
            self.notify(rid)
            box = self.box(uid, sid)
            async with asyncio.timeout(self.settings.run_timeout) as timeout:
                # Scoped to this execution Task, including startup and its child graph.
                run_deadline.set(timeout.when())
                # The worker thread may still be creating the container after
                # cancellation. The supervisor waits for it before issuing stop.
                start_task = asyncio.create_task(asyncio.to_thread(box.start))
                await asyncio.shield(start_task)
                return await self.runner(
                    user_id=uid,
                    session_id=sid,
                    run_id=rid,
                    prompt=run["prompt"],
                    sandbox=box,
                    emit=emit,
                    settings=self.settings,
                )

        try:
            if job.cancel_requested:
                state = "cancelled"
            else:
                job.execution = asyncio.create_task(execute())
                answer = await job.execution
                state = "completed"
        except BudgetClosed as exc:
            answer, error = exc.answer, exc.public_message
        except TimeoutError:
            error = "任务超过执行时限，已停止"
        except asyncio.CancelledError:
            state = "cancelled"
        except Exception as exc:  # noqa: BLE001 - contain third-party errors at the run boundary.
            error = (
                getattr(exc, "public_message", None)
                or f"任务执行失败，可使用运行编号 {rid} 查询服务日志"
            )
            log.error("Run %s failed (%s)", rid, type(exc).__name__)
        finally:
            if start_task is not None:
                await asyncio.gather(start_task, return_exceptions=True)
            cleaned = await self._cleanup(uid, sid, box)
            if not cleaned and state == "completed":
                state, error = "failed", "任务已返回，但沙箱清理失败"
            await asyncio.to_thread(self.store.finish, rid, state, answer, error)
            self.notify(rid)
