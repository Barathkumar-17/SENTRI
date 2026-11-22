from __future__ import annotations

from pathlib import Path

from sentri.contract.models import AppConfig
from sentri.sandbox.base import Sandbox, SandboxError
from sentri.sandbox.command_sandbox import CommandSandbox
from sentri.sandbox.docker_sandbox import DockerSandbox
from sentri.sandbox.external import ExternalSandbox


def make_sandbox(app: AppConfig, workdir: Path, extra_env: dict[str, str] | None = None) -> Sandbox:
    if app.docker is not None:
        return DockerSandbox(app, workdir, extra_env)
    if app.run is not None:
        return CommandSandbox(app, workdir, extra_env)
    return ExternalSandbox(app)  # URL mode: the app is already running somewhere


__all__ = ["CommandSandbox", "DockerSandbox", "ExternalSandbox", "Sandbox", "SandboxError", "make_sandbox"]
