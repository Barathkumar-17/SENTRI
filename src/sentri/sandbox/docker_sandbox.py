"""Managed Docker mode: builds the image, runs a throwaway container, tails its logs."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sentri.contract.models import AppConfig
from sentri.evidence.models import LogLine
from sentri.sandbox.base import LogBuffer, ResponseErrors, SandboxError, wait_for_health, wait_for_port

log = logging.getLogger(__name__)


class DockerSandbox(ResponseErrors):
    reset_kind = "restart"

    def __init__(self, app: AppConfig, workdir: Path, extra_env: dict[str, str] | None = None):
        assert app.docker is not None and app.port is not None  # validated: port is required with app.docker
        self.app = app
        self.port: int = app.port
        self.spec = app.docker
        self.context = (workdir / self.spec.context).resolve()
        self.env = {"PORT": str(self.spec.container_port or app.port), **app.env, **self.spec.env, **(extra_env or {})}
        self.base_url = app.url
        self.logs = LogBuffer()
        tag = hashlib.sha256(str(self.context).encode()).hexdigest()[:10]
        self.image_tag = f"sentri-target:{tag}"
        self._client: Any = None
        self._container: Any = None
        self._log_thread: threading.Thread | None = None
        parts = urlsplit(self.base_url)
        self._host = parts.hostname or "localhost"

    def _docker(self) -> Any:
        if self._client is None:
            try:
                import docker

                self._client = docker.from_env()
                self._client.ping()
            except Exception as exc:  # noqa: BLE001 - any failure means Docker is unusable
                raise SandboxError(f"Docker is not available: {exc}") from None
        return self._client

    def _build(self) -> None:
        log.info("building image %s from %s", self.image_tag, self.context)
        try:
            self._docker().images.build(
                path=str(self.context), dockerfile=self.spec.dockerfile, tag=self.image_tag, rm=True
            )
        except Exception as exc:  # noqa: BLE001
            raise SandboxError(f"docker build failed: {exc}") from None

    def _run(self) -> None:
        container_port = self.spec.container_port or self.app.port
        self._container = self._docker().containers.run(
            self.image_tag,
            detach=True,
            ports={f"{container_port}/tcp": self.port},
            environment=self.env,
            labels={"sentri": "target"},
        )
        self._log_thread = threading.Thread(target=self._pump_logs, args=(self._container,), daemon=True)
        self._log_thread.start()

    def _pump_logs(self, container: Any) -> None:
        buf = b""
        with contextlib.suppress(Exception):
            for chunk in container.logs(stream=True, follow=True, stdout=True, stderr=True):
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self.logs.append(line.decode("utf-8", errors="replace"))

    async def _wait_ready(self) -> None:
        deadline = time.monotonic() + self.app.startup_timeout_s
        try:
            await wait_for_port(self._host, self.port, deadline, alive=self.is_alive)
            await wait_for_health(self.base_url + self.app.health_path, deadline, alive=self.is_alive)
        except SandboxError as exc:
            tail = "\n".join(line.text for line in self.logs.tail(20))
            raise SandboxError(f"{exc}\n--- container log tail ---\n{tail}") from None

    async def start(self) -> None:
        await asyncio.to_thread(self._build)
        await asyncio.to_thread(self._run)
        await self._wait_ready()

    async def reset(self) -> None:
        if self.app.reset == "command":
            assert self.app.reset_command is not None
            code, output = await asyncio.to_thread(self._container.exec_run, ["sh", "-c", self.app.reset_command])
            if code != 0:
                raise SandboxError(f"reset_command failed ({code}): {output[-500:]!r}")
            return
        await self._remove()
        await asyncio.to_thread(self._run)
        await self._wait_ready()

    async def _remove(self) -> None:
        if self._container is not None:
            container, self._container = self._container, None
            with contextlib.suppress(Exception):
                await asyncio.to_thread(container.remove, force=True)

    async def stop(self) -> None:
        await self._remove()

    def is_alive(self) -> bool:
        if self._container is None:
            return False
        try:
            self._container.reload()
            return bool(self._container.status == "running")
        except Exception:  # noqa: BLE001
            return False

    def log_cursor(self) -> int:
        return self.logs.cursor()

    def logs_since(self, cursor: int) -> list[LogLine]:
        return self.logs.since(cursor)

    def log_tail(self, n: int = 40) -> list[LogLine]:
        return self.logs.tail(n)
