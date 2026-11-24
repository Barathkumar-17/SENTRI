"""DockerSandbox startup, readiness and teardown with a FAKE Docker client.

Docker is not installed on every machine (it is not on the development machine used so far), so these tests prove the
sandbox's own logic: what it does when the build, the run or the readiness check fails, and that nothing is left
running. They do NOT prove that real Docker images build or that a real container serves the app: that needs a Docker
daemon and is a separate validation."""

from __future__ import annotations

import asyncio
import socket

import pytest

from sentri.contract.models import AppConfig
from sentri.sandbox.base import SandboxError
from sentri.sandbox.docker_sandbox import DockerSandbox


class _Container:
    status = "running"

    def __init__(self):
        self.removed = False

    def reload(self):
        pass

    def logs(self, **kw):
        return iter([b"listening\n"])

    def remove(self, force=False):
        self.removed = True
        self.status = "removed"


class _Containers:
    def __init__(self, client):
        self.client = client

    def run(self, image, **kw):
        if self.client.run_error:
            raise self.client.run_error
        c = _Container()
        self.client.run_kwargs.append(kw)
        self.client.started.append(c)
        return c


class _Images:
    def __init__(self, client):
        self.client = client

    def build(self, **kw):
        if self.client.build_error:
            raise self.client.build_error


class _Client:
    def __init__(self, build_error=None, run_error=None):
        self.build_error, self.run_error = build_error, run_error
        self.started: list[_Container] = []
        self.run_kwargs: list[dict] = []
        self.containers = _Containers(self)
        self.images = _Images(self)

    def ping(self):
        return True


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _sandbox(tmp_path, client, startup_timeout_s=1.0, port=None):
    port = port or _free_port()
    app = AppConfig.model_validate(
        {
            "docker": {"context": ".", "dockerfile": "Dockerfile"},
            "port": port,
            "base_url": f"http://127.0.0.1:{port}",
            "startup_timeout_s": startup_timeout_s,
        }
    )
    sb = DockerSandbox(app, tmp_path)
    sb._client = client
    return sb


async def test_a_container_that_never_becomes_ready_is_removed_not_left_running(tmp_path):
    client = _Client()
    sb = _sandbox(tmp_path, client, startup_timeout_s=0.6)  # nothing listens: readiness times out
    with pytest.raises(SandboxError, match="did not open in time"):
        await sb.start()
    assert len(client.started) == 1 and client.started[0].removed  # no orphan holding the port


async def test_a_cancelled_start_removes_the_container_too(tmp_path):
    client = _Client()
    sb = _sandbox(tmp_path, client, startup_timeout_s=30)
    task = asyncio.create_task(sb.start())
    for _ in range(100):  # wait until the container is running and the start is waiting for its port
        if client.started:
            break
        await asyncio.sleep(0.1)
    assert client.started
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(client.started) == 1 and client.started[0].removed


async def test_a_docker_run_error_is_a_sandbox_error_with_the_reason(tmp_path):
    client = _Client(run_error=RuntimeError("port is already allocated"))
    sb = _sandbox(tmp_path, client)
    with pytest.raises(SandboxError, match="docker run failed: port is already allocated"):
        await sb.start()
    assert client.started == []


async def test_a_build_failure_is_a_sandbox_error_and_starts_nothing(tmp_path):
    client = _Client(build_error=RuntimeError("Dockerfile not found"))
    sb = _sandbox(tmp_path, client)
    with pytest.raises(SandboxError, match="docker build failed: Dockerfile not found"):
        await sb.start()
    assert client.started == []


async def test_a_port_already_in_use_is_refused_before_building_anything(tmp_path):
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = _Client()
    sb = _sandbox(tmp_path, client, port=port)
    try:
        with pytest.raises(SandboxError, match="already in use"):
            await sb.start()
    finally:
        server.close()
        await server.wait_closed()
    assert client.started == []


async def test_stop_removes_a_running_container_and_is_safe_to_repeat(tmp_path):
    client = _Client()
    sb = _sandbox(tmp_path, client)
    sb._run()
    await sb.stop()
    await sb.stop()  # idempotent
    assert client.started[0].removed and not sb.is_alive()


async def test_without_a_docker_daemon_the_error_says_so(tmp_path, monkeypatch):
    app = AppConfig.model_validate(
        {"docker": {"context": ".", "dockerfile": "Dockerfile"}, "port": _free_port(), "startup_timeout_s": 1}
    )
    sb = DockerSandbox(app, tmp_path)

    class _Docker:
        @staticmethod
        def from_env():
            raise RuntimeError("Error while fetching server API version: connection refused")

    import sys

    monkeypatch.setitem(sys.modules, "docker", _Docker)
    with pytest.raises(SandboxError, match="Docker is not available: Error while fetching server API version"):
        await sb.start()


async def test_the_container_port_is_published_on_the_host_loopback_only(tmp_path):
    client = _Client()
    port = _free_port()
    sb = _sandbox(tmp_path, client, startup_timeout_s=0.6, port=port)
    with pytest.raises(SandboxError):  # nothing listens, but the run call is all this test needs
        await sb.start()
    # (host ip, host port): a bare port number would publish on every host interface (0.0.0.0 and ::)
    assert client.run_kwargs[0]["ports"] == {f"{port}/tcp": ("127.0.0.1", port)}
