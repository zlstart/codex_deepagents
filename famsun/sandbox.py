"""Deep Agents Docker backend. The Docker daemon is a trusted control plane."""

from __future__ import annotations

import hashlib
import json
import math
import os
import selectors
import subprocess
import tempfile
import threading
import time
from uuid import uuid4

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

IMAGE = "famsun-sandbox:local"
HELPER_IMAGE = "famsun-helper:local"
CREATE_TIMEOUT = 150
LABEL = "io.famsun.sandbox=v1"
MAX_FILE = 32 * 1024 * 1024
MAX_OUTPUT = 1024 * 1024
_LOCKS = [threading.RLock() for _ in range(64)]
_POLICY = [
    "--user",
    "10001:10001",
    "--read-only",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges:true",
    "--init",
    "--ulimit",
    "core=0:0",
    "--ulimit",
    "nofile=1024:1024",
    "--ulimit",
    "nproc=128:128",
    "--ulimit",
    "fsize=268435456:268435456",
    "--ulimit",
    "data=4294967296:4294967296",
    "--log-driver",
    "none",
]
_PROXY_ENV = [
    arg
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY")
    for arg in ("--env", key + "=http://egress:3128")
] + [
    arg
    for key in (
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
        "FTP_PROXY",
        "ftp_proxy",
    )
    for arg in ("--env", key + "=")
]


class ArtifactTooLarge(ValueError):
    """The file or listing exceeds the transport budget."""


class DockerTimeout(RuntimeError):
    """The daemon did not finish the requested control operation in time."""


def _docker(*args, check=True, timeout=30):
    try:
        result = subprocess.run(
            ["docker", *args], capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as error:
        raise DockerTimeout(f"Docker {args[0]} exceeded {timeout} seconds") from error
    except OSError as error:
        raise RuntimeError("Docker control plane unavailable") from error
    if check and result.returncode:
        reason = result.stderr.decode(errors="replace").strip()[:1024]
        raise RuntimeError(f"Docker operation failed: {reason}")
    return result


def _exists(kind, name):
    result = _docker(kind, "inspect", name, check=False)
    if result.returncode == 0:
        return True
    if b"no such" in result.stderr.lower() or b"not found" in result.stderr.lower():
        return False
    raise RuntimeError("Docker resource lookup failed")


def _remove(kind, name):
    if _exists(kind, name):
        args = ["--force"] if kind == "container" else []
        _docker(kind, "rm", *args, name)


def _capture(args, *, limit, timeout, data=None):
    """Drain output without buffering or spooling unbounded sandbox output."""
    with tempfile.TemporaryFile() as source:
        if data is not None:
            source.write(data)
            source.seek(0)
        with subprocess.Popen(
            ["docker", *args],
            stdin=source,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        ) as proc:
            assert proc.stdout is not None
            deadline = time.monotonic() + timeout
            output = bytearray()
            truncated = False
            try:
                with selectors.DefaultSelector() as sel:
                    sel.register(proc.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not sel.select(remaining):
                            raise subprocess.TimeoutExpired(args[0], timeout)
                        chunk = os.read(proc.stdout.fileno(), 65536)
                        if not chunk:
                            break
                        available = limit - len(output)
                        output.extend(chunk[:available])
                        truncated |= len(chunk) > available
                code = proc.wait(timeout=max(0.01, deadline - time.monotonic()))
            finally:
                if proc.poll() is None:
                    proc.kill()
            return code, bytes(output), truncated


# Transfer inside the same container so /tmp and other sandbox paths work with
# the upstream BaseSandbox edit and output-offload implementations.
_WRITE = """import os,sys,tempfile
p=os.path.abspath(sys.argv[1])
d=os.path.dirname(p)
os.makedirs(d,exist_ok=True)
with tempfile.NamedTemporaryFile(dir=d,delete=False) as tmp:
    tmp.write(sys.stdin.buffer.read())
os.replace(tmp.name,p)
"""
_READ = """import os,stat,sys
p=sys.argv[1]
try:
    fd=os.open(p,os.O_RDONLY|os.O_NONBLOCK)
except FileNotFoundError:
    sys.exit(2)
except PermissionError:
    sys.exit(5)
mode=os.fstat(fd).st_mode
if stat.S_ISDIR(mode): sys.exit(4)
if not stat.S_ISREG(mode): sys.exit(6)
with os.fdopen(fd,'rb') as f:
    data=f.read(33554433)
    if len(data)>33554432: sys.exit(3)
    sys.stdout.buffer.write(data)
"""


def _artifact_path(path):
    if (
        not isinstance(path, str)
        or len(path) > 4096
        or "\\" in path
        or any(ord(c) < 32 or ord(c) == 127 for c in path)
        or any(not p or p.startswith(".") for p in path.split("/"))
    ):
        raise ValueError("invalid artifact path")
    return path


class DockerSandbox(BaseSandbox):
    """A workspace and venv survive stop/start but are unique per owner and session."""

    grep_exclude_dirs = (".git", ".venv", "node_modules")

    def __init__(
        self,
        user_id: str,
        session_id: str,
        *,
        image: str = IMAGE,
        helper_image: str = HELPER_IMAGE,
        timeout: int = 120,
        allow_unenforced_cgroups: bool = False,
        volume_driver: str = "local",
        volume_options: dict[str, str] | None = None,
    ):
        digest = hashlib.sha256(
            json.dumps([str(user_id), str(session_id)]).encode()
        ).hexdigest()[:32]
        self._name = "famsun-" + digest
        self._lock = _LOCKS[int(digest[:2], 16) % len(_LOCKS)]
        self.image = image
        self.helper_image = helper_image
        self.allow_unenforced_cgroups = allow_unenforced_cgroups
        self.volume_driver = volume_driver
        self.volume_options = dict(volume_options or {})
        self.timeout = max(1, min(int(timeout), 3600))
        self._container_id = None
        self._stopped = False
        self._cleaned = False

    @property
    def id(self):
        return self._name

    @property
    def _volume(self):
        return self.id + "-files"

    @property
    def _network(self):
        return self.id + "-net"

    @property
    def _proxy(self):
        return self.id + "-proxy"

    def available(self):
        return _exists("image", self.image) and _exists("image", self.helper_image)

    def start(self):
        with self._lock:
            if self._stopped:
                raise RuntimeError("This sandbox handle has been stopped")
            if self._container_id:
                return self._container_id
            if not self.allow_unenforced_cgroups:
                info = _docker(
                    "info",
                    "--format",
                    "{{.CgroupDriver}} {{.MemoryLimit}} {{.CPUCfsQuota}} {{.PidsLimit}}",
                )
                caps = info.stdout.decode().strip().split()
                if (
                    len(caps) != 4
                    or caps[0] == "none"
                    or caps[1:] != ["true", "true", "true"]
                ):
                    raise RuntimeError(
                        "Docker must enforce memory, CPU and PID limits for isolated execution"
                    )
            self._clear_runtime()
            options = [
                arg
                for k, v in self.volume_options.items()
                for arg in ("--opt", f"{k}={v}")
            ]
            try:
                for volume in (self._volume, self.id + "-home"):
                    _docker(
                        "volume",
                        "create",
                        "--label",
                        LABEL,
                        "--driver",
                        self.volume_driver,
                        *options,
                        volume,
                    )
                # The isolated gateway mode prevents access even to host services
                # bound to the bridge address. Requires Docker Engine >= 28.
                _docker(
                    "network",
                    "create",
                    "--internal",
                    "--label",
                    LABEL,
                    "--opt",
                    "com.docker.network.bridge.gateway_mode_ipv4=isolated",
                    self._network,
                )
                _docker(
                    "create",
                    "--name",
                    self._proxy,
                    "--label",
                    LABEL,
                    *_POLICY,
                    "--memory",
                    "128m",
                    "--cpus",
                    "0.5",
                    "--pids-limit",
                    "32",
                    "--network",
                    self._network,
                    "--network-alias",
                    "egress",
                    "--env",
                    "FAMSUN_EGRESS_UPSTREAM",
                    self.helper_image,
                    "/usr/local/bin/python",
                    "-I",
                    "/opt/famsun/proxy.py",
                    timeout=CREATE_TIMEOUT,
                )
                _docker("network", "connect", "bridge", self._proxy)
                _docker("start", self._proxy)
                result = _docker(
                    "run",
                    "--detach",
                    "--name",
                    self.id,
                    "--label",
                    LABEL,
                    *_POLICY,
                    "--memory",
                    "1g",
                    "--memory-swap",
                    "1g",
                    "--cpus",
                    "1.5",
                    "--pids-limit",
                    "128",
                    "--tmpfs",
                    "/tmp:rw,exec,nosuid,nodev,size=128m,mode=1777",
                    "--network",
                    self._network,
                    "--mount",
                    f"type=volume,src={self._volume},dst=/workspace",
                    "--mount",
                    f"type=volume,src={self.id}-home,dst=/home/worker",
                    *_PROXY_ENV,
                    self.image,
                    timeout=CREATE_TIMEOUT,
                )
                # Tool calls queued for a cancelled run must never attach to a
                # subsequent run that happens to reuse the session's container name.
                self._container_id = result.stdout.decode("ascii").strip()
                return self._container_id
            except BaseException:
                self._clear_runtime()
                raise

    def _clear_runtime(self):
        for name in (self.id, self._proxy):
            _remove("container", name)
        _remove("network", self._network)

    def stop(self):
        with self._lock:
            if self._cleaned:
                return
            self._stopped = True
            self._clear_runtime()
            self._cleaned = True

    close = stop

    def delete(self):
        with self._lock:
            self.stop()
            for volume in (self._volume, self.id + "-home"):
                _remove("volume", volume)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        container_id = self.start()
        seconds = min(max(1, math.ceil(timeout or self.timeout)), self.timeout)
        args = [
            "exec",
            container_id,
            "/usr/bin/timeout",
            "--kill-after=2",
            str(seconds),
            "/bin/bash",
            "-o",
            "pipefail",
            "-c",
            command,
        ]
        try:
            code, output, truncated = _capture(
                args, limit=MAX_OUTPUT, timeout=seconds + 10
            )
        except subprocess.TimeoutExpired:
            self.stop()
            return ExecuteResponse(output="Sandbox command timed out", exit_code=124)
        except OSError as error:
            return ExecuteResponse(
                output=f"Sandbox command failed: {error.strerror}", exit_code=1
            )
        message = output.decode("utf-8", "replace")
        if truncated:
            message += "\n[output truncated]"
        if code == 124:
            message += f"\n[command timed out after {seconds}s]"
        return ExecuteResponse(output=message, exit_code=code, truncated=truncated)

    def upload_files(self, files):
        container_id = self.start()
        results = []
        for path, content in files:
            error = None
            try:
                if len(content) > MAX_FILE:
                    raise ValueError("file exceeds transfer limit")
                args = [
                    "exec",
                    "-i",
                    container_id,
                    "/usr/local/bin/python",
                    "-I",
                    "-c",
                    _WRITE,
                    path,
                ]
                code, _, _ = _capture(args, limit=1024, timeout=30, data=content)
                if code:
                    error = "permission_denied"
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                error = str(exc)
            results.append(FileUploadResponse(path=path, error=error))
        return results

    def download_files(self, paths):
        container_id = self.start()
        results = []
        for path in paths:
            try:
                args = [
                    "exec",
                    container_id,
                    "/usr/local/bin/python",
                    "-I",
                    "-c",
                    _READ,
                    path,
                ]
                code, data, clipped = _capture(args, limit=MAX_FILE, timeout=30)
                errors = {
                    0: None,
                    2: "file_not_found",
                    3: "file exceeds transfer limit",
                    4: "is_directory",
                    5: "permission_denied",
                    6: "invalid_path",
                }
                error = (
                    "file exceeds transfer limit"
                    if clipped
                    else errors.get(code, "file transfer failed")
                )
            except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                data, error = None, str(exc)
            results.append(
                FileDownloadResponse(
                    path=path, content=None if error else data, error=error
                )
            )
        return results

    def _artifacts(self, *args):
        if not _exists("volume", self._volume):
            if args[0] == "list":
                return b"[]"
            raise FileNotFoundError
        # Separate namespaces, immutable interpreter and no network. Even a
        # running agent cannot modify the reader or see its file descriptors.
        reader = f"{self.id}-reader-{uuid4().hex}"
        try:
            result = _docker(
                "run",
                "--rm",
                "--name",
                reader,
                "--network",
                "none",
                "--label",
                LABEL,
                *_POLICY,
                "--memory",
                "192m",
                "--cpus",
                "0.5",
                "--pids-limit",
                "16",
                "--mount",
                f"type=volume,src={self._volume},dst=/workspace,readonly",
                self.helper_image,
                "/usr/local/bin/python",
                "-I",
                "/opt/famsun/artifacts.py",
                *args,
                check=False,
                timeout=60,
            )
        except DockerTimeout:
            # Docker CLI cancellation can leave a container in Created state on
            # slow storage. Address this request's exact name, never its volume.
            _remove("container", reader)
            raise
        if result.returncode == 42:
            raise FileNotFoundError
        if result.returncode == 43:
            raise ArtifactTooLarge("workspace artifact limit exceeded")
        if result.returncode == 44:
            raise ValueError("invalid artifact")
        if result.returncode:
            raise RuntimeError("Docker artifact reader failed")
        return result.stdout

    def list_artifacts(self):
        return json.loads(self._artifacts("list"))

    def read_artifact(self, path):
        return self._artifacts("read", _artifact_path(path))


def create_sandbox(*, user_id, session_id, settings):
    return DockerSandbox(
        user_id,
        session_id,
        image=settings.sandbox_image,
        helper_image=settings.helper_image,
        timeout=settings.sandbox_timeout,
        allow_unenforced_cgroups=settings.allow_unenforced_cgroups,
        volume_driver=settings.volume_driver,
        volume_options=settings.volume_options,
    )
