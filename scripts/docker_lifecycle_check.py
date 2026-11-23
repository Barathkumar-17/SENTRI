"""No-model check of Sentri's REAL Docker lifecycle, for a machine with a Docker daemon.

    python scripts/docker_lifecycle_check.py

Uses examples/buggy-app (its Dockerfile) and a throwaway "never listens" image. Checks, in isolation from other
containers (it only ever removes containers it started, found by id):
  1. config -> build -> start -> readiness -> a usable app answers GET /, 2. reset (restart) works,
  3. stop removes the container and frees the port, 4. a container that never opens its port fails readiness with
  a clear error and is removed, 5. a cancelled start removes its container, 6. a port already in use is refused
  before anything is built.
Exit 0 = all pass, 1 = a check failed, 2 = blocked (no Docker client or daemon: nothing was tested).
This proves the lifecycle only. It does not prove model-driven exploration inside Docker mode."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sentri.contract.models import AppConfig  # noqa: E402
from sentri.sandbox.base import SandboxError  # noqa: E402
from sentri.sandbox.docker_sandbox import DockerSandbox  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def sandbox(context: Path, container_port: int, port: int, timeout_s: float) -> DockerSandbox:
    app = AppConfig.model_validate(
        {
            "docker": {"context": str(context), "dockerfile": "Dockerfile", "container_port": container_port},
            "port": port,
            "base_url": f"http://127.0.0.1:{port}",
            "startup_timeout_s": timeout_s,
        }
    )
    return DockerSandbox(app, context)


async def get_status(port: int) -> int:
    def fetch() -> int:
        return int(urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10).status)  # noqa: S310 - local

    return await asyncio.to_thread(fetch)


def containers(client) -> set[str]:
    return {c.id for c in client.containers.list(all=True, filters={"label": "sentri=target"})}


async def main() -> int:
    try:
        import docker

        client = docker.from_env()
        info = client.version()
    except Exception as exc:  # noqa: BLE001
        print(f"BLOCKED: no usable Docker (client or daemon): {str(exc)[:160]}\nNothing was tested.")
        return 2
    print(f"Docker {info.get('Version', '?')} on {info.get('Os', '?')}/{info.get('Arch', '?')}")
    before = containers(client)
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")

    def leaked() -> set[str]:
        return containers(client) - before

    # 1-3: the demo app, from its own Dockerfile
    port = free_port()
    sb = sandbox(ROOT / "examples" / "buggy-app", 5055, port, 180)
    try:
        await sb.start()
        status = await get_status(port)
        record("start: build, run, ready, usable app", status == 200, f"GET / -> {status}")
        await sb.reset()
        status = await get_status(port)
        record("reset restarts the container and it is usable again", status == 200, f"GET / -> {status}")
    except Exception as exc:  # noqa: BLE001
        record("start/reset", False, str(exc)[:200])
    finally:
        await sb.stop()
    await asyncio.sleep(1)
    record(
        "stop removes the container and frees the port", not leaked() and not port_open(port), f"leaked={len(leaked())}"
    )

    # 4-6: an image whose app never listens
    with tempfile.TemporaryDirectory() as tmp:
        ctx = Path(tmp)
        (ctx / "Dockerfile").write_text('FROM python:3.14-slim\nCMD ["sleep", "600"]\n', encoding="utf-8")
        port = free_port()
        bad = sandbox(ctx, 8000, port, 5)
        try:
            await bad.start()
            record("readiness failure is reported", False, "start() returned for an app that never listens")
        except SandboxError as exc:
            # A real daemon publishes the host port before the app listens, so the port check passes and the health
            # check reports the reset connection; a fake client never opens the port. Either is a clear reason.
            reason = "did not open in time" in str(exc) or "health check" in str(exc)
            record("readiness failure is reported with a reason", reason, str(exc)[:80])
        await asyncio.sleep(1)
        record("readiness failure leaves no container behind", not leaked(), f"leaked={len(leaked())}")

        port = free_port()
        slow = sandbox(ctx, 8000, port, 120)
        task = asyncio.create_task(slow.start())
        for _ in range(240):  # until the container is running and start() is waiting for its port
            if containers(client) - before:
                break
            await asyncio.sleep(0.5)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await asyncio.sleep(1)
        record(
            "a cancelled start removes its container", not leaked() and not port_open(port), f"leaked={len(leaked())}"
        )

        server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        taken = server.sockets[0].getsockname()[1]
        clash = sandbox(ctx, 8000, taken, 5)
        try:
            await clash.start()
            record("a port already in use is refused", False, "start() returned")
        except SandboxError as exc:
            record("a port already in use is refused before building", "already in use" in str(exc), str(exc)[:70])
        finally:
            server.close()
            await server.wait_closed()
    record("no container created by this check remains", not leaked(), f"leaked={len(leaked())}")
    failed = [n for n, ok, _ in results if not ok]
    print(
        f"\n{len(results) - len(failed)}/{len(results)} checks passed"
        + (f"; FAILED: {', '.join(failed)}" if failed else "")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
