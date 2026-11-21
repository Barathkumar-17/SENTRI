"""Smoke test: proves a fresh checkout works end to end on this machine.

    python scripts/smoke_test.py            # quick: install checks, one attacked cell, report, dashboard API
    python scripts/smoke_test.py --full     # the whole grid against the planted-bug app

Exits 0 on success. Uses the CLI exactly as a user would, in a temporary directory.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "examples" / "buggy-app"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def sentri(*args: str, env: dict[str, str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "sentri.cli", *args]
    print(f"$ sentri {' '.join(args)}", flush=True)
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    tail = lines if os.environ.get("CI") else lines[-12:]
    print("  " + "\n  ".join(tail), flush=True)
    if check and proc.returncode != 0:
        raise SystemExit(f"FAILED (exit {proc.returncode}): sentri {' '.join(args)}")
    return proc


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="run the whole grid")
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="sentri-smoke-"))
    app_dir = work / "buggy-app"
    shutil.copytree(APP, app_dir, ignore=shutil.ignore_patterns(".contract-*", "__pycache__"))
    port = free_port()
    contract = yaml.safe_load((app_dir / "contract.yaml").read_text(encoding="utf-8"))
    contract["app"].update(
        port=port, base_url=f"http://127.0.0.1:{port}", env={"PORT": str(port), "FLAKY_STATE": str(work / "flaky.txt")}
    )
    (app_dir / "contract.yaml").write_text(yaml.safe_dump(contract), encoding="utf-8")

    env = {
        **os.environ,
        "SENTRI_DB": str(work / "ledger.db"),
        "SENTRI_EVIDENCE_DIR": str(work / "evidence"),
        "SENTRI_LLM_PROVIDER": "none",
        "SENTRI_BAD_FILE_MB": "6",
        "PYTHONIOENCODING": "utf-8",
        "COLUMNS": "160",
    }
    started = time.monotonic()

    sentri("--version", env=env, cwd=app_dir)
    doctor = sentri("doctor", env=env, cwd=app_dir, check=False)
    if doctor.returncode not in (0,):
        raise SystemExit("doctor reports missing requirements; run `sentri setup`")
    sentri("contract", "check", "contract.yaml", env=env, cwd=app_dir)

    only = [] if args.full else ["--only", "signup:empty_input", "--only", "profile:double_submit"]
    sentri("run", "--contract", "contract.yaml", "--out", "report.md", *only, env=env, cwd=app_dir)
    report = (app_dir / "report.md").read_text(encoding="utf-8")
    assert "Sentri readiness" in report, report
    assert "❌" in report, "the planted signup bug must fail"

    bugs = json.loads(sentri("bug", "list", "--json", env=env, cwd=app_dir).stdout)
    assert any(b["flow_id"] == "signup" and b["status"] == "open" for b in bugs), bugs

    # Second run on unchanged code reuses the passing cell.
    rerun = sentri("run", "--contract", "contract.yaml", "--only", "profile:double_submit", env=env, cwd=app_dir)
    assert "reused" in rerun.stdout, "a passing check on unchanged code must be reused"

    # Dashboard API over the same ledger.
    dash_port = free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "sentri.cli", "serve", "--port", str(dash_port)],
        cwd=app_dir,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{dash_port}/api/runs", timeout=2) as r:
                    runs = json.loads(r.read())
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise SystemExit("dashboard did not start")
        assert len(runs) >= 2, runs
        with urllib.request.urlopen(f"http://127.0.0.1:{dash_port}/api/runs/{runs[-1]['id']}/grid") as r:
            assert json.loads(r.read())["cells"]
        print(f"  dashboard API ok ({len(runs)} runs)")
    finally:
        server.terminate()
        threading.Thread(target=server.wait, daemon=True).start()

    print(f"\nSMOKE TEST PASSED in {time.monotonic() - started:.0f}s  (workdir: {work})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
