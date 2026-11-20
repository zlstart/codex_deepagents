"""Supervise the single API process's background jobs and durable run records."""

import asyncio
import logging
from dataclasses import dataclass

log = logging.getLogger("famsun.runtime")


@dataclass
class Job:
    session_id: str
    supervisor: asyncio.Task | None = None
    execution: asyncio.Task | None = None
    cancel_requested: bool = False


class Jobs:
    def __init__(self, store, settings, runner, sandbox_factory):
        self.store = store
        self.settings = settings
        self.runner = runner
        self.sandbox_factory = sandbox_factory
        self.tasks = {}
        self.reaper = None

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

    async def recover(self):
        await asyncio.to_thread(self.store.recovery)
        # Unclean shutdowns leave a durable cleanup marker. Block further runs
        # for that session until any surviving execution container is stopped.
        await self.reap_idle()
        self.reaper = asyncio.create_task(self._reap_periodically())

    async def reap_idle(self):
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

    async def _execute(self, uid, sid, run, job):
        rid = run["id"]
        box = None
        start_task = None
        state, answer, error = "failed", None, None

        async def emit(event):
            await asyncio.to_thread(self.store.append_event, rid, event)

        async def execute():
            nonlocal box, start_task
            await asyncio.to_thread(self.store.start, rid)
            box = self.box(uid, sid)
            async with asyncio.timeout(self.settings.run_timeout):
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
