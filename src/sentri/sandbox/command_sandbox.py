"""Command mode: runs `app.run` as a local process. No isolation; reset = kill and restart."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO, Literal
from urllib.parse import urlsplit

from sentri.contract.models import AppConfig
from sentri.evidence.models import LogLine
from sentri.sandbox.base import (
    LogBuffer,
    ResponseErrors,
    SandboxError,
    wait_for_health,
    wait_for_port,
    wait_for_port_closed,
)

log = logging.getLogger(__name__)


def expand_command(command: str) -> str:
    """`{python}` expands to the interpreter running Sentri, so examples work inside any venv."""
    python = sys.executable
    quoted = f'"{python}"' if os.name == "nt" else shlex.quote(python)
    return command.replace("{python}", quoted)


class CommandSandbox(ResponseErrors):
    reset_kind = "restart"

    def __init__(self, app: AppConfig, workdir: Path, extra_env: dict[str, str] | None = None):
        assert app.run is not None
        self.app = app
        self.command = expand_command(app.run)
        self.workdir = (workdir / app.cwd).resolve()
        # PORT tells most frameworks where to listen; the contract's port wins over the shell's, and an explicit
        # app.env PORT wins over both.
        self.env = {
            **os.environ,
            "PORT": str(app.port),
            **app.env,
            **(extra_env or {}),
            "PYTHONUNBUFFERED": "1",
        }
        self.base_url = app.url
        self.logs = LogBuffer()
        self._proc: subprocess.Popen[bytes] | None = None
        self._threads: list[threading.Thread] = []
        parts = urlsplit(self.base_url)
        self._host = parts.hostname or "localhost"
        assert app.port is not None  # validated: required with app.run
        self._port: int = app.port

    def _pump(self, pipe: IO[bytes], stream: Literal["stdout", "stderr"]) -> None:
        for raw in iter(pipe.readline, b""):
            self.logs.append(raw.decode("utf-8", errors="replace"), stream)

    def _spawn(self) -> None:
        windows = os.name == "nt"
        log.info("starting app: %s (cwd=%s)", self.command, self.workdir)
        self._proc = subprocess.Popen(  # noqa: S602 - the contract's run command is a shell command by design
            self.command,
            shell=True,
            cwd=self.workdir,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if windows else 0,
            start_new_session=not windows,
        )
        assert self._proc.stdout and self._proc.stderr
        self._threads = [
            threading.Thread(target=self._pump, args=(self._proc.stdout, "stdout"), daemon=True),
            threading.Thread(target=self._pump, args=(self._proc.stderr, "stderr"), daemon=True),
        ]
        for t in self._threads:
            t.start()

    async def start(self) -> None:
        if not await wait_for_port_closed(self._host, self._port, timeout_s=5):
            raise SandboxError(f"port {self._port} is already in use by another process")
        self._clean()
        self._spawn()
        deadline = time.monotonic() + self.app.startup_timeout_s
        try:
            await wait_for_port(self._host, self._port, deadline, alive=self.is_alive)
            await wait_for_health(self.base_url + self.app.health_path, deadline, alive=self.is_alive)
        except SandboxError as exc:
            tail = "\n".join(line.text for line in self.logs.tail(20))
            await self.stop()
            raise SandboxError(f"{exc}\n--- app log tail ---\n{tail}") from None

    def _clean(self) -> None:
        """Delete the contract's app.clean files (e.g. a SQLite database) for a clean, seeded state."""
        for rel in self.app.clean:
            target = (self.workdir / rel).resolve()
            if not target.is_relative_to(self.workdir):
                raise SandboxError(f"app.clean path {rel!r} is outside the app directory")
            for path in (target, *target.parent.glob(target.name + "-*")):  # also SQLite -wal / -shm files
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()

    def _kill_tree(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)
        else:
            with contextlib.suppress(ProcessLookupError):
                getattr(os, "killpg")(proc.pid, getattr(signal, "SIGKILL"))  # noqa: B009 - POSIX only
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)

    async def stop(self) -> None:
        await asyncio.to_thread(self._kill_tree)
        self._proc = None
        await wait_for_port_closed(self._host, self._port, timeout_s=10)

    async def reset(self) -> None:
        if self.app.reset == "command":
            assert self.app.reset_command is not None
            proc = await asyncio.create_subprocess_shell(
                expand_command(self.app.reset_command), cwd=self.workdir, env=self.env
            )
            if await proc.wait() != 0:
                raise SandboxError(f"reset_command failed with exit code {proc.returncode}")
            return
        await self.stop()
        await self.start()

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def log_cursor(self) -> int:
        return self.logs.cursor()

    def logs_since(self, cursor: int) -> list[LogLine]:
        return self.logs.since(cursor)

    def log_tail(self, n: int = 40) -> list[LogLine]:
        return self.logs.tail(n)
