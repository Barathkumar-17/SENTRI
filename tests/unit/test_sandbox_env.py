from __future__ import annotations

from pathlib import Path

from sentri.contract.models import AppConfig
from sentri.sandbox import CommandSandbox, DockerSandbox


def test_command_sandbox_tells_the_app_its_port(tmp_path: Path):
    sb = CommandSandbox(AppConfig.model_validate({"run": "x", "port": 5077}), tmp_path)
    assert sb.env["PORT"] == "5077"
    explicit = CommandSandbox(AppConfig.model_validate({"run": "x", "port": 5077, "env": {"PORT": "9"}}), tmp_path)
    assert explicit.env["PORT"] == "9"


def test_docker_sandbox_uses_the_container_port(tmp_path: Path):
    app = AppConfig.model_validate({"docker": {"context": ".", "container_port": 8000}, "port": 5077})
    assert DockerSandbox(app, tmp_path).env["PORT"] == "8000"


def test_fingerprint_changes_with_the_inherited_environment(tmp_path: Path):
    from sentri.runner.fingerprint import app_fingerprint

    (tmp_path / "app.py").write_text("print(1)")
    base = {"PATH": "/bin", "PWD": "/x"}
    same = app_fingerprint(tmp_path, env={**base, "PWD": "/elsewhere"})  # volatile variable ignored
    assert app_fingerprint(tmp_path, env=base) == same
    assert app_fingerprint(tmp_path, env={**base, "FIX_SIGNUP_EMPTY": "1"}) != same
    assert "FIX" not in app_fingerprint(tmp_path, env={**base, "FIX_SIGNUP_EMPTY": "1"})  # values never stored
