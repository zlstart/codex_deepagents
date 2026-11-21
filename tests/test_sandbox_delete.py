"""Deletion owns only the exact tenant/session resources, including old artifact helpers."""

import os
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import ExitStack
from uuid import uuid4

import pytest

from famsun import sandbox as module
from famsun.sandbox import DockerSandbox


def test_unknown_lookup_error_preserves_deletion_for_retry(monkeypatch):
    def failed(*args, **kwargs):
        assert args[:2] == ("volume", "inspect")
        return subprocess.CompletedProcess(
            [], 1, b"", b"Error: volume driver plugin not found"
        )

    monkeypatch.setattr(module, "_docker", failed)
    with pytest.raises(RuntimeError, match="lookup failed"):
        module._remove("volume", "session-files")


def test_unowned_name_collision_is_not_removed(monkeypatch):
    def inspect(*args, **kwargs):
        assert args[:2] == ("volume", "inspect")
        return subprocess.CompletedProcess(
            [], 0, b"session-files session-files another-owner", b""
        )

    monkeypatch.setattr(module, "_docker", inspect)
    with pytest.raises(RuntimeError, match="unowned"):
        module._remove("volume", "session-files")


def test_delete_waits_for_all_admitted_readers_and_stale_read_cannot_recreate(
    monkeypatch,
):
    uid, sid = uuid4().hex, uuid4().hex
    handles = [DockerSandbox(uid, sid) for _ in range(3)]
    volume_present = {handles[0]._volume}
    in_reader = threading.Barrier(3)
    release = [threading.Event(), threading.Event()]
    deleting = threading.Event()

    def read_volume(i):
        in_reader.wait(3)
        assert release[i].wait(3)
        return b"[]"

    def delete():
        deleting.set()
        handles[2].delete()

    monkeypatch.setattr(module, "_exists", lambda kind, name: name in volume_present)
    monkeypatch.setattr(
        module, "_remove", lambda kind, name: volume_present.discard(name)
    )
    monkeypatch.setattr(DockerSandbox, "_remove_artifact_helpers", lambda self: None)
    for i, box in enumerate(handles[:2]):
        monkeypatch.setattr(
            box, "_read_volume", lambda *args, index=i: read_volume(index)
        )
    with ThreadPoolExecutor(max_workers=3) as pool:
        first, second = (pool.submit(box.list_artifacts) for box in handles[:2])
        try:
            in_reader.wait(3)
            cleanup = pool.submit(delete)
            assert deleting.wait(3)
            release[0].set()
            with pytest.raises(TimeoutError):
                cleanup.result(timeout=0.05)
            assert volume_present
        finally:
            for gate in release:
                gate.set()
        assert first.result(timeout=3) == second.result(timeout=3) == []
        cleanup.result(timeout=3)
    assert not volume_present
    assert handles[0].list_artifacts() == []
    with pytest.raises(FileNotFoundError):
        handles[0].read_artifact("anything")


def test_real_delete_removes_legacy_readers_and_preserves_unrelated_mounts():
    def docker(*args, check=True):
        return subprocess.run(
            ["docker", *args], capture_output=True, text=True, check=check
        )

    image = os.getenv("FAMSUN_HELPER_IMAGE", module.HELPER_IMAGE)
    if (
        not shutil.which("docker")
        or docker("image", "inspect", image, check=False).returncode
    ):
        if os.getenv("FAMSUN_REQUIRE_DOCKER_TESTS") == "true":
            pytest.fail("real deletion verification requires the helper image")
        pytest.skip("build the helper image to verify container/volume deletion")

    namespace = uuid4().hex
    target = DockerSandbox("first-" + namespace, "same-id")
    other = DockerSandbox("second-" + namespace, "same-id")
    # Run-scoped cleanup might have completed before another worker acquired
    # resources for this session. Deletion must still inspect them afresh.
    target.stop()
    with ExitStack() as stack:
        # All objects below belong exclusively to this test.
        for v in (target._volume, target.id + "-home", other._volume):
            docker("volume", "create", "--label", module.LABEL, v)
            stack.callback(docker, "volume", "rm", v, check=False)
        docker("network", "create", "--label", module.LABEL, target._network)
        stack.callback(docker, "network", "rm", target._network, check=False)
        for name in (target.id, target._proxy):
            docker(
                "create",
                "--name",
                name,
                "--label",
                module.LABEL,
                "--network",
                target._network,
                image,
                "/bin/true",
            )
            stack.callback(docker, "rm", "-f", name, check=False)

        def reader(volume, command, *extra):
            identifier = docker(
                "create",
                "--rm",
                "--label",
                module.LABEL,
                "--network",
                "none",
                *extra,
                "--mount",
                f"type=volume,src={volume},dst=/workspace,readonly",
                image,
                *command,
            ).stdout.strip()
            stack.callback(docker, "rm", "-f", identifier, check=False)
            return identifier

        read_cmd = ["/usr/local/bin/python", "-I", "/opt/famsun/artifacts.py", "list"]
        legacy = reader(target._volume, read_cmd)
        named = reader(
            target._volume, read_cmd, "--name", target.id + "-reader-" + uuid4().hex
        )
        other_reader = reader(other._volume, read_cmd)
        # A non-reader holding the volume must block physical deletion and be
        # preserved until its owner releases it. The same handle can then retry.
        foreign = reader(target._volume, ["/bin/true"])
        with pytest.raises(RuntimeError, match="volume removal failed"):
            target.delete()
        for identifier in (target.id, target._proxy, legacy, named):
            assert (
                docker("container", "inspect", identifier, check=False).returncode != 0
            )
        assert (
            docker("network", "inspect", target._network, check=False).returncode != 0
        )
        for identifier in (foreign, other_reader):
            assert (
                docker("container", "inspect", identifier, check=False).returncode == 0
            )
        docker("rm", "-f", foreign)
        target.delete()
        target.delete()
        assert docker("volume", "inspect", target._volume, check=False).returncode != 0
        assert (
            docker("volume", "inspect", target.id + "-home", check=False).returncode
            != 0
        )
        assert docker("volume", "inspect", other._volume, check=False).returncode == 0
        assert docker("container", "inspect", other_reader, check=False).returncode == 0
