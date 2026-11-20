"""FAMSUN Studio's same-origin API and production static web entry point."""

from __future__ import annotations

import asyncio
import fcntl
import mimetypes
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from . import auth
from .config import Settings
from .runtime import Jobs
from .store import Busy, Conflict, Missing, Store


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Payload):
    username: str = Field(
        pattern="^" + auth.USERNAME.pattern + "$", min_length=3, max_length=64
    )
    password: SecretStr = Field(min_length=1, max_length=128)


class SessionInput(Payload):
    title: str = Field(default="新会话", max_length=120)


class RunInput(Payload):
    prompt: str = Field(min_length=1, max_length=32000)


class RequestGuard:
    """Reject cross-origin writes and bound request bodies before JSON parsing."""

    def __init__(self, app, public_origin=None):
        self.app = app
        self.public_origin = public_origin

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            return await self.app(scope, receive, send)
        request = Request(scope)
        origin = request.headers.get("origin")
        if origin:
            try:
                source = urlsplit(origin)
                expected = urlsplit(self.public_origin or str(request.base_url))
                origin_matches = (source.scheme, source.netloc.lower()) == (
                    expected.scheme,
                    expected.netloc.lower(),
                )
            except ValueError:
                origin_matches = False
        else:
            origin_matches = True
        if not origin_matches or request.headers.get("sec-fetch-site") == "cross-site":
            return await JSONResponse({"detail": "请求来源不匹配"}, 403)(
                scope, receive, send
            )
        payload = bytearray()
        while True:
            chunk = await receive()
            if chunk["type"] == "http.disconnect":
                return
            if len(payload) + len(chunk["body"]) > 192 * 1024:
                return await JSONResponse({"detail": "请求内容过大"}, 413)(
                    scope, receive, send
                )
            payload.extend(chunk["body"])
            if not chunk.get("more_body"):
                break
        if (payload or request.headers.get("content-type")) and request.headers.get(
            "content-type", ""
        ).split(";")[0].lower() != "application/json":
            return await JSONResponse({"detail": "请使用 application/json"}, 415)(
                scope, receive, send
            )
        body = bytes(payload)

        async def receive_body():
            nonlocal body
            if body is not None:
                message, body = body, None
                return {"type": "http.request", "body": message, "more_body": False}
            return await receive()

        return await self.app(scope, receive_body, send)


def create_app(settings=None, *, runner=None, sandbox_factory=None):
    cfg = settings or Settings()
    store = Store(Path(cfg.data_dir) / "studio.sqlite3")
    auth_attempts = auth.RateLimit(30, 60)
    registrations = auth.RateLimit(8, 60)
    reads = asyncio.Semaphore(4)
    passwords = asyncio.Semaphore(2)

    def default_sandbox(**kwargs):
        from .sandbox import create_sandbox

        return create_sandbox(**kwargs)

    async def default_runner(**kwargs):
        from .harness import run_agent

        return await run_agent(**kwargs)

    jobs = Jobs(
        store, cfg, runner or default_runner, sandbox_factory or default_sandbox
    )

    @asynccontextmanager
    async def lifespan(app):
        Path(cfg.data_dir).mkdir(mode=0o700, parents=True, exist_ok=True)
        with (Path(cfg.data_dir) / "service.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError(
                    "The SQLite deployment requires exactly one application worker per data directory."
                ) from None
            await asyncio.to_thread(store.initialize)
            await jobs.recover()
            try:
                yield
            finally:
                await jobs.shutdown()

    app = FastAPI(
        title="FAMSUN Agent",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store, app.state.jobs = store, jobs
    public_origin = getattr(cfg, "public_origin", None)
    app.add_middleware(RequestGuard, public_origin=public_origin)

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "private, no-store"
        if cfg.secure_cookies and (public_origin or str(request.base_url)).startswith(
            "https://"
        ):
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.exception_handler(Missing)
    async def missing(request, exc):
        return JSONResponse({"detail": "未找到资源"}, 404)

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, 409)

    @app.exception_handler(Busy)
    async def busy(request, exc):
        return JSONResponse({"detail": str(exc)}, 429, headers={"Retry-After": "5"})

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        # FastAPI's default handler includes raw form values, including passwords.
        fields = [str(error["loc"][-1]) for error in exc.errors() if error["loc"]]
        return JSONResponse({"detail": "请求参数无效：" + "、".join(fields)}, 422)

    def current_user(request: Request):
        token = request.cookies.get(auth.COOKIE, "")
        user = (
            store.user_by_token(auth.token_digest(token)) if len(token) == 43 else None
        )
        if not user:
            raise HTTPException(401, "请先登录")
        return user

    authorized = Depends(current_user)

    def issue(user, response):
        token = secrets.token_urlsafe(32)
        store.save_token(
            user["id"], auth.token_digest(token), int(time.time()) + auth.TTL
        )
        response.set_cookie(
            auth.COOKIE,
            token,
            max_age=auth.TTL,
            secure=cfg.secure_cookies,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return user

    def client_addr(request):
        return request.client.host if request.client else "unknown"

    @app.get("/api/health")
    def health():
        with store.db() as con:
            con.execute("SELECT COUNT(*) FROM sqlite_schema").fetchone()
        return {"status": "ok"}

    @app.get("/api/auth/config")
    def auth_config():
        return {"allow_signup": cfg.allow_signup}

    @app.post("/api/auth/register", status_code=201)
    async def register(request: Request, payload: Login, response: Response):
        if not cfg.allow_signup:
            raise HTTPException(403, "此部署已关闭公开注册，请联系管理员开通帐户")
        if not registrations.accept(client_addr(request)):
            raise HTTPException(
                429, "注册请求过多，请稍后重试", headers={"Retry-After": "60"}
            )
        password = payload.password.get_secret_value()
        if not auth.USERNAME.fullmatch(payload.username) or len(password) < 12:
            raise HTTPException(422, "用户名无效或密码少于 12 位")
        async with passwords:
            hashed = await asyncio.to_thread(auth.password_hash, password)
        user = await asyncio.to_thread(store.create_user, payload.username, hashed)
        return await asyncio.to_thread(issue, user, response)

    @app.post("/api/auth/login")
    async def login(request: Request, payload: Login, response: Response):
        if not auth_attempts.accept(client_addr(request)):
            raise HTTPException(
                429, "尝试过于频繁，请稍后重试", headers={"Retry-After": "60"}
            )
        credentials = await asyncio.to_thread(store.credentials, payload.username)
        async with passwords:
            ok = await asyncio.to_thread(
                auth.check_password,
                payload.password.get_secret_value(),
                credentials["password_hash"] if credentials else None,
            )
        if not ok:
            raise HTTPException(401, "用户名或密码错误")
        return await asyncio.to_thread(
            issue, {k: credentials[k] for k in ("id", "username")}, response
        )

    @app.post("/api/auth/logout", status_code=204)
    def logout(request: Request):
        store.revoke(auth.token_digest(request.cookies.get(auth.COOKIE, "")))
        response = Response(status_code=204)
        response.delete_cookie(
            auth.COOKIE,
            path="/",
            secure=cfg.secure_cookies,
            httponly=True,
            samesite="strict",
        )
        return response

    @app.get("/api/auth/me")
    def me(user=authorized):
        return user

    @app.get("/api/sessions")
    def sessions(user=authorized):
        return store.sessions(user["id"])

    @app.post("/api/sessions", status_code=201)
    def create_session(payload: SessionInput | None = None, user=authorized):
        return store.create_session(
            user["id"], (payload.title.strip() if payload else "") or "新会话"
        )

    @app.get("/api/sessions/{sid}/runs")
    def runs(sid: str, user=authorized):
        return store.runs(user["id"], sid)

    @app.post("/api/sessions/{sid}/runs", status_code=202)
    async def submit(sid: str, payload: RunInput, request: Request, user=authorized):
        prompt = payload.prompt.strip()
        if not prompt:
            raise HTTPException(422, "请输入任务内容")
        key = request.headers.get("idempotency-key")
        if key is not None and not (
            1 <= len(key) <= 128 and key.isascii() and key.isprintable()
        ):
            raise HTTPException(422, "无效请求编号")

        async def admit():
            rid = uuid4().hex
            run = await asyncio.to_thread(
                store.new_run,
                user["id"],
                sid,
                prompt,
                cfg.max_parallel_runs,
                run_id=rid,
                request_key=key,
            )
            if run["id"] == rid:
                jobs.submit(user["id"], sid, run)
            return run

        return await asyncio.shield(admit())

    @app.get("/api/sessions/{sid}/runs/{rid}")
    def get_run(sid: str, rid: str, user=authorized):
        return store.run(user["id"], sid, rid)

    @app.get("/api/sessions/{sid}/runs/{rid}/events")
    def events(
        sid: str, rid: str, after: int = Query(0, ge=0, le=2**63 - 1), user=authorized
    ):
        return store.events(user["id"], sid, rid, after)

    @app.post("/api/sessions/{sid}/runs/{rid}/cancel")
    async def cancel(sid: str, rid: str, user=authorized):
        uid = user["id"]
        await asyncio.to_thread(store.run, uid, sid, rid)
        await jobs.cancel(rid)
        return await asyncio.to_thread(store.run, uid, sid, rid)

    async def read_workspace(uid, sid, path=None):
        await asyncio.to_thread(store.own_session, uid, sid)
        from .sandbox import ArtifactTooLarge

        try:
            box = jobs.box(uid, sid)
            async with reads:
                if path is None:
                    return await asyncio.to_thread(box.list_artifacts)
                return await asyncio.to_thread(box.read_artifact, path)
        except (FileNotFoundError, IsADirectoryError):
            raise HTTPException(404, "未找到文件") from None
        except ArtifactTooLarge:
            raise HTTPException(413, "文件超过读取上限") from None
        except ValueError:
            raise HTTPException(400, "无效文件路径") from None
        except (RuntimeError, OSError):
            raise HTTPException(
                503, "工作区暂不可用，请检查 Docker 服务和沙箱镜像"
            ) from None

    @app.get("/api/sessions/{sid}/files")
    async def files(sid: str, user=authorized):
        return await read_workspace(user["id"], sid)

    @app.get("/api/sessions/{sid}/file")
    async def file(
        sid: str,
        path: str = Query(min_length=1, max_length=1024),
        download: bool = False,
        user=authorized,
    ):
        parts = path.split("/")
        if (
            "\\" in path
            or any(ord(char) < 32 for char in path)
            or any(not part or part.startswith(".") for part in parts)
        ):
            raise HTTPException(400, "无效文件路径")
        content = await read_workspace(user["id"], sid, path)
        name = PurePosixPath(path).name
        suffix = PurePosixPath(name).suffix.lower()
        mime = {".glb": "model/gltf-binary", ".gltf": "model/gltf+json"}.get(suffix)
        mime = mime or mimetypes.guess_type(name)[0] or "application/octet-stream"
        inline = mime in {
            "image/png",
            "image/jpeg",
            "image/webp",
            "image/gif",
            "application/pdf",
            "text/plain",
            "application/json",
            "text/csv",
            "model/gltf-binary",
            "model/gltf+json",
        }
        disposition = "inline" if inline and not download else "attachment"
        return Response(
            content=content,
            media_type=mime,
            headers={
                "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(name, safe='')}",
                "Content-Security-Policy": "sandbox; default-src 'none'; frame-ancestors 'self'",
            },
        )

    # /api misses should retain JSON status codes even when an SPA is installed.
    @app.api_route(
        "/api/{unknown:path}", methods=["GET", "POST", "DELETE", "PATCH", "PUT"]
    )
    def unknown_api(unknown: str):
        raise Missing

    if cfg.static_dir and Path(cfg.static_dir).is_dir():
        app.mount("/", StaticFiles(directory=cfg.static_dir, html=True), name="studio")
    return app


app = create_app()
