"""Look up one FAMSUN run using the configured project's private credentials."""

import argparse
import json
import os
import time
from collections import Counter

import httpx

from famsun.config import LangfuseConfig
from famsun.telemetry import trace_ids


def verify(base_url: str, user: str, session: str, run: str, timeout: int) -> dict:
    config = LangfuseConfig(
        base_url, os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"]
    )
    ids = trace_ids(config, user, session, run)
    query = {"traceId": ids["trace_id"], "fields": "core,basic,io", "limit": 500}
    deadline = time.monotonic() + timeout
    with httpx.Client(
        base_url=base_url, auth=(config.public_key, config.secret_key), timeout=10
    ) as client:
        while True:
            rows = []
            cursor = None
            while True:
                params = {**query, **({"cursor": cursor} if cursor else {})}
                response = client.get("/api/public/v2/observations", params=params)
                response.raise_for_status()
                page = response.json()
                rows.extend(page["data"])
                cursor = page["meta"].get("cursor")
                if not cursor:
                    break
            root = next(
                (
                    row
                    for row in rows
                    if row.get("isRootObservation") and row.get("endTime")
                ),
                None,
            )
            if root:
                if any(
                    row["traceId"] != ids["trace_id"]
                    or row.get("userId") != ids["user_id"]
                    or row.get("sessionId") != ids["session_id"]
                    for row in rows
                ):
                    raise ValueError("Trace、用户或会话标识与此 FAMSUN 回合不一致。")
                return {
                    "trace_id": ids["trace_id"],
                    "name": root["name"],
                    "run_id": run,
                    "status": root.get("statusMessage"),
                    "observation_types": dict(Counter(row["type"] for row in rows)),
                    "observation_count": len(rows),
                    "trace_has_content": bool(root.get("input") or root.get("output")),
                    "observations_have_content": any(
                        row.get("input") or row.get("output") for row in rows
                    ),
                }
            if time.monotonic() >= deadline:
                raise TimeoutError("时限内没有查到此回合已结束的根观测记录。")
            time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--langfuse-url", default="http://127.0.0.1:3000")
    parser.add_argument("--user", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    result = verify(args.langfuse_url, args.user, args.session, args.run, args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
