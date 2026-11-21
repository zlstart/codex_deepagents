"""Render an immutable, authorized document snapshot in a disposable container."""

import asyncio
import hashlib
import json
import subprocess
from collections import OrderedDict
from uuid import uuid4

OFFICE = {"doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "rtf"}
SHEETS = {"xls", "xlsx"}
CACHE_BYTES = 64 * 1024 * 1024
MAX_PDF = 32 * 1024 * 1024
MAX_SHEET = 1024 * 1024


class PreviewError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def convert(settings, data, ext, view, sheet, offset):
    from .sandbox import _POLICY, LABEL, _capture, _remove, require_resource_limits

    name = "famsun-preview-" + uuid4().hex
    clear_proxy = [
        arg
        for key in ("http_proxy", "https_proxy", "all_proxy", "ftp_proxy", "no_proxy")
        for variant in (key, key.upper())
        for arg in ("--env", variant + "=")
    ]
    args = [
        "run",
        "-i",
        "--rm",
        "--name",
        name,
        "--label",
        LABEL,
        "--pull",
        "never",
        *_POLICY,
        "--network",
        "none",
        "--pids-limit",
        "64",
        "--cpus",
        "1",
        "--memory",
        "896m",
        "--memory-swap",
        "896m",
        "--ulimit",
        "cpu=60:65",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=256m,mode=1777",
        *clear_proxy,
        settings.preview_image,
        ext,
        view,
        str(sheet),
        str(offset),
    ]
    try:
        if not settings.allow_unenforced_cgroups:
            require_resource_limits()
        code, output, clipped = _capture(
            args,
            limit=MAX_SHEET if view == "sheet" else MAX_PDF,
            timeout=100,
            data=data,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        try:
            _remove("container", name)
        except (OSError, RuntimeError):
            raise PreviewError(503, "预览超时，Docker 未能确认转换容器已清理") from None
        raise PreviewError(504, "文档转换超时，可下载原文件") from None
    except (OSError, RuntimeError):
        raise PreviewError(
            503, "预览服务暂不可用，请检查转换镜像和 Docker 资源限制"
        ) from None
    if clipped or code in {41, 137, 152, 153}:
        raise PreviewError(413, "文档内容或转换结果超过预览资源上限")
    if code == 40:
        raise PreviewError(422, "文档无法读取，请检查文件格式、保护设置或工作表页码")
    if code == 42:
        raise PreviewError(422, "文件受保护或与扩展名不符，请下载后核对格式及密码")
    if code == 45:
        raise PreviewError(504, "文档转换超时，可下载原文件")
    if code:
        raise PreviewError(503, "文档转换服务暂不可用")
    if view == "sheet":
        try:
            parsed = json.loads(output)
        except (UnicodeError, ValueError):
            raise PreviewError(503, "工作表转换服务返回无效数据") from None
        if not isinstance(parsed, dict):
            raise PreviewError(503, "工作表转换服务返回无效数据")
    elif not output.startswith(b"%PDF-"):
        raise PreviewError(503, "文档转换服务返回无效 PDF")
    return output


class Previews:
    """One event loop per service. Identical requests share one conversion."""

    def __init__(self, settings):
        self.settings = settings
        self.pending = {}
        self.cache = OrderedDict()
        self.size = 0

    async def get(self, uid, sid, data, ext, view, sheet, offset):
        if ext == "pdf":
            if not data.startswith(b"%PDF-"):
                raise PreviewError(422, "文件缺少有效的 PDF 标识")
            return data
        digest = await asyncio.to_thread(lambda: hashlib.sha256(data).digest())
        key = (uid, sid, digest, ext, view, sheet, offset)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        task = self.pending.get(key)
        if task is None:
            if len(self.pending) >= 2:
                raise PreviewError(429, "其他文档正在转换，请稍后重试")
            task = asyncio.create_task(
                self._render(key, data, ext, view, sheet, offset)
            )
            self.pending[key] = task
            # An HTTP disconnect releases the caller, but the running process
            # continues counting toward capacity and can populate the cache.
            task.add_done_callback(
                lambda done: None if done.cancelled() else done.exception()
            )
        return await asyncio.shield(task)

    async def discard_session(self, uid, sid):
        # The caller holds the deletion lease. Shield converters that outlived
        # their HTTP client before releasing their cached document snapshots.
        owner = (uid, sid)
        tasks = [
            asyncio.shield(task)
            for key, task in self.pending.items()
            if key[:2] == owner
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for key in tuple(self.cache):
            if key[:2] == owner:
                self.size -= len(self.cache.pop(key))

    async def _render(self, key, data, ext, view, sheet, offset):
        try:
            result = await asyncio.to_thread(
                convert, self.settings, data, ext, view, sheet, offset
            )
            self.cache[key] = result
            self.size += len(result)
            while self.size > CACHE_BYTES or len(self.cache) > 64:
                _, evicted = self.cache.popitem(last=False)
                self.size -= len(evicted)
            return result
        finally:
            self.pending.pop(key, None)
