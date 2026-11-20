"""Isolation, lifecycle and artifact contracts, including real Docker execution."""

import os
import shutil
import subprocess
from uuid import uuid4

import pytest

from famsun import sandbox as module
from famsun.sandbox import ArtifactTooLarge, DockerSandbox


@pytest.mark.parametrize(
    "path",
    [
        "/etc/hosts",
        "",
        "../document",
        "a/../b",
        ".env",
        "report//image",
        "data\\notes",
        "test\x00file",
        "file\nname",
    ],
)
def test_invalid_artifact_paths_are_rejected_before_docker(path, monkeypatch):
    monkeypatch.setattr(
        module,
        "_docker",
        lambda *args, **kwargs: pytest.fail("unexpected Docker access"),
    )
    with pytest.raises(ValueError):
        DockerSandbox("a", "b").read_artifact(path)


def test_cgroup_enforcement_is_required_by_default(monkeypatch):
    def info(*args, **kwargs):
        assert args[0] == "info"
        return subprocess.CompletedProcess([], 0, b"none false false false\n", b"")

    monkeypatch.setattr(module, "_docker", info)
    with pytest.raises(RuntimeError, match="Docker must enforce"):
        DockerSandbox("alice", "run").start()


def test_timed_out_reader_removes_only_its_own_container(monkeypatch):
    box = DockerSandbox("alice", "one", helper_image="test-reader:small")
    issued, removed = [], []
    monkeypatch.setattr(module, "_exists", lambda kind, resource: True)
    monkeypatch.setattr(
        module, "_remove", lambda kind, resource: removed.append((kind, resource))
    )

    def timeout(*args, **kwargs):
        assert args[0] == "run" and "test-reader:small" in args
        assert module.IMAGE not in args
        assert args[args.index("--network") + 1] == "none"
        assert args[args.index("--mount") + 1].endswith("dst=/workspace,readonly")
        issued.append(args[args.index("--name") + 1])
        raise module.DockerTimeout("simulated slow engine")

    monkeypatch.setattr(module, "_docker", timeout)
    for _ in range(2):
        with pytest.raises(module.DockerTimeout):
            box.list_artifacts()
    assert issued[0] != issued[1]
    assert removed == [("container", name) for name in issued]


def test_reader_startup_error_does_not_report_file_missing(monkeypatch):
    monkeypatch.setattr(module, "_exists", lambda kind, resource: True)
    monkeypatch.setattr(
        module,
        "_docker",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [], 2, b"", b"python cannot start the reader"
        ),
    )
    with pytest.raises(RuntimeError, match="reader failed"):
        DockerSandbox("alice", "one").read_artifact("model.glb")


def test_distinct_ids_for_user_and_session():
    assert (
        len(
            {DockerSandbox(*pair).id for pair in (("a", "bc"), ("ab", "c"), ("a", "c"))}
        )
        == 3
    )


@pytest.fixture
def docker_ready():
    cmd = shutil.which("docker")
    image = os.getenv("FAMSUN_SANDBOX_IMAGE", module.IMAGE)
    if (
        not cmd
        or subprocess.run(
            [cmd, "image", "inspect", image], capture_output=True, check=False
        ).returncode
    ):
        if os.getenv("FAMSUN_REQUIRE_DOCKER_TESTS") == "true":
            pytest.fail("sandbox Docker image is required for this test run")
        pytest.skip("build deploy/sandbox.Dockerfile to run Docker integration tests")
    helper = os.getenv("FAMSUN_HELPER_IMAGE", module.HELPER_IMAGE)
    if subprocess.run(
        [cmd, "image", "inspect", helper], capture_output=True, check=False
    ).returncode:
        if os.getenv("FAMSUN_REQUIRE_DOCKER_TESTS") == "true":
            pytest.fail(
                "artifact helper image is required for Docker integration tests"
            )
        pytest.skip("build deploy/helper.Dockerfile to run Docker integration tests")
    return image, helper


def test_docker_lifecycle_isolation_and_reader(docker_ready):
    namespace = uuid4().hex

    def handle(user, sid):
        # CI validates cgroup policy separately. The managed rootless test daemon
        # does not delegate cgroups; exercise real container isolation there too.
        return DockerSandbox(
            user + namespace,
            sid,
            image=docker_ready[0],
            helper_image=docker_ready[1],
            allow_unenforced_cgroups=True,
        )

    first = handle("a", "one")
    second = handle("a", "two")
    other = handle("b", "one")
    resumed = handle("a", "one")
    try:
        assert first.list_artifacts() == []
        first.start()
        assert first.write("/workspace/design/files.txt", "hello").error is None
        assert first.read("/workspace/design/files.txt").error is None
        assert (
            first.edit("/workspace/design/files.txt", "hello", "verified").error is None
        )
        assert (
            first.execute(
                "set -e; ln -s /etc/hosts link; mkfifo pipe; truncate -s 34000000 large; "
                "python -c 'import numpy,pandas,scipy,trimesh,matplotlib,openpyxl,PIL'; "
                'ls /opt/famsun/skills/AGENTS.md && test -z "$LLM_API_KEY" && test ! -S /var/run/docker.sock'
            ).exit_code
            == 0
        )
        cad = first.execute("""python - <<'PYW'
import trimesh
from shapely.geometry import box
solid = trimesh.creation.box(extents=[2, 2, 2])
assert solid.contains([[0, 0, 0], [2, 2, 2]]).tolist() == [True, False]
scene = trimesh.Scene()
scene.add_geometry(solid, node_name="frame")
assert scene.graph.to_networkx().has_edge("world", "frame")
cut = solid.difference(trimesh.creation.box(), engine='manifold')
assert cut.is_watertight and abs(cut.volume - 7) < 1e-6
plate = trimesh.creation.extrude_polygon(box(0, 0, 2, 1), height=3, engine='earcut')
assert plate.is_watertight and abs(plate.volume - 6) < 1e-6
PYW""")
        assert cad.exit_code == 0, cad.output
        assert first.execute("id -u").output.strip() == "10001"
        assert first.execute("touch /opt/famsun/immutable").exit_code != 0
        assert first.execute("false | tail -n 1").exit_code == 1
        timing = first.execute("cd /workspace && time python3 -c 'print(42)'")
        assert (
            timing.exit_code == 0 and "42" in timing.output and "real" in timing.output
        )
        assert first.execute("sleep 5", timeout=1).exit_code == 124
        assert {f["path"] for f in first.list_artifacts()} == {
            "design/files.txt",
            "large",
        }
        for name in ("link", "pipe", "design"):
            with pytest.raises(ValueError):
                first.read_artifact(name)
        with pytest.raises(ArtifactTooLarge):
            first.read_artifact("large")
        assert first.read_artifact("design/files.txt") == b"verified"

        # Exercise uv package installation and persistence without a remote service.
        wheel = first.execute("""python - <<'PYW'
from zipfile import ZipFile
with ZipFile('/tmp/famsuncheck-1.0-py3-none-any.whl','w') as z:
    z.writestr('famsuncheck.py','answer = 42')
    z.writestr('famsuncheck-1.0.dist-info/METADATA','Metadata-Version: 2.1\\nName: famsuncheck\\nVersion: 1.0\\n')
    z.writestr('famsuncheck-1.0.dist-info/WHEEL','Wheel-Version: 1.0\\nGenerator: famsun\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n')
    z.writestr('famsuncheck-1.0.dist-info/RECORD','')
PYW
uv pip install --no-index /tmp/famsuncheck-1.0-py3-none-any.whl""")
        assert wheel.exit_code == 0, wheel.output
        first.stop()
        with pytest.raises(RuntimeError, match="stopped"):
            first.execute("pwd")
        assert resumed.read_artifact("design/files.txt") == b"verified"
        resumed.start()
        first.stop()  # A delayed cleanup must leave the next turn running.
        transfers = resumed.download_files(
            ["/workspace/design/files.txt", "/workspace/design", "/missing"]
        )
        assert transfers[0].content == b"verified"
        assert [item.error for item in transfers] == [
            None,
            "is_directory",
            "file_not_found",
        ]
        assert (
            resumed.execute(
                "python -c 'import famsuncheck; print(famsuncheck.answer)'"
            ).output.strip()
            == "42"
        )

        for isolated in (second, other):
            assert (
                isolated.execute("test ! -e /workspace/design/files.txt").exit_code == 0
            )
            assert isolated.execute("python -c 'import famsuncheck'").exit_code != 0
        blocked = resumed.execute("""python - <<'PYW'
import socket
from urllib.request import urlopen
try:
    urlopen('http://169.254.169.254/', timeout=4)
except Exception as e:
    assert getattr(e,'code',None)==403, e
else:
    raise AssertionError('proxy allowed link-local destination')
try:
    socket.create_connection(('1.1.1.1', 443),timeout=2)
except OSError:
    pass
else:
    raise AssertionError('sandbox has direct external route')
PYW""")
        assert blocked.exit_code == 0, blocked.output
    finally:
        for sandbox in (resumed, second, other):
            sandbox.delete()
