"""Identify exactly one tenant's graph thread, including its subgraph namespaces."""

from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


def session_thread_id(user_id: str, session_id: str) -> str:
    return f"{user_id}/{session_id}"


async def remove_session_checkpoints(path: Path, user_id: str, session_id: str) -> None:
    try:
        path.stat()
    except FileNotFoundError:
        return
    # Open an existing DB only. A permissions or I/O failure must keep deletion
    # pending; it is different from a session which never acquired a checkpoint.
    async with aiosqlite.connect(
        path.resolve().as_uri() + "?mode=rw", uri=True, timeout=5
    ) as conn:
        async with conn.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name IN ('checkpoints','writes')"
        ) as cursor:
            tables = {row[0] for row in await cursor.fetchall()}
        if not tables:
            return
        saver = AsyncSqliteSaver(conn)
        if tables != {"checkpoints", "writes"}:
            # Older/partially initialized files may only have one saver table.
            await saver.setup()
        await saver.adelete_thread(session_thread_id(user_id, session_id))
