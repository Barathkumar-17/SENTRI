"""The fixed gate a Fixer patch must pass before a human sees it. Plain code, black-box checks."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from sentri.attacks import ATTACKS, Payload
from sentri.config import Settings
from sentri.contract.models import Contract, Flow
from sentri.runner.browser import BrowserManager
from sentri.runner.check_runner import CheckRunner
from sentri.sandbox import SandboxError, make_sandbox

TEST_GLOBS = (
    "test_*.py",
    "*_test.py",
    "tests/*",
    "*/tests/*",
    "__tests__/*",
    "*/__tests__/*",
    "*.test.*",
    "*.spec.*",
    "spec/*",
    "*/spec/*",
    "test/*",
    "*/test/*",
)
_DIFF_PATH = re.compile(r"^(?:\+\+\+|---) (?:[ab]/)?(.+?)\s*$")


@dataclass
class GateCheck:
    name: str
    passed: bool
    detail: str


def diff_paths(diff: str) -> list[str]:
    paths = []
    for line in diff.splitlines():
        m = _DIFF_PATH.match(line)
        if m and m.group(1) != "/dev/null" and m.group(1) not in paths:
            paths.append(m.group(1))
    return paths


def is_test_path(path: str, extra_globs: tuple[str, ...] = ()) -> bool:
    p = PurePosixPath(path)
    for pattern in (*TEST_GLOBS, *extra_globs):
        if fnmatch.fnmatch(p.as_posix(), pattern) or fnmatch.fnmatch(p.name, pattern):
            return True
    return False


def check_no_test_files(diff: str, extra_globs: tuple[str, ...] = ()) -> GateCheck:
    paths = diff_paths(diff)
    if not paths:
        return GateCheck("no_test_files_edited", False, "patch touches no files")
    touched = [p for p in paths if is_test_path(p, extra_globs)]
    if touched:
        return GateCheck("no_test_files_edited", False, f"patch edits test files: {', '.join(touched)}")
    return GateCheck("no_test_files_edited", True, f"{len(paths)} file(s), none are tests")


def heldout_payload(flow: Flow, attack_id: str, original: Payload, rotation_start: int = 1) -> tuple[Payload, str]:
    """A payload of the same attack type that differs from the original. Timing variants for slot-less attacks."""
    attack = ATTACKS[attack_id]
    if not attack.payload_slots(flow):
        return {"gap_ms": "40"}, "timing variant (40 ms gap); weak held-out for a timing bug"
    for rotation in range(rotation_start, rotation_start + 10):
        candidate = attack.finalize(flow, attack.static_payload(flow, rotation))
        if candidate != original:
            return candidate, "static payload not seen by the Fixer"
    return original, "no distinct payload available; held-out equals original"


async def run_one(
    contract: Contract, workdir: Path, settings: Settings, flow: Flow, attack_id: str, payload: Payload, tag: str
) -> tuple[str, str]:
    """Run one check against the app in `workdir`. Returns (verdict, detail)."""
    sandbox = make_sandbox(contract.app, workdir)
    browser = BrowserManager(headless=settings.headless)
    try:
        await sandbox.start()
    except SandboxError as exc:
        return "error", f"app failed to start: {exc}"
    try:
        await browser.start()
        runner = CheckRunner(contract, sandbox, browser, attempts=settings.attempts, run_tag=tag, workdir=workdir)
        outcome = await runner.run_check(flow, attack_id, payload)
        if outcome.verdict is None:
            return "error", outcome.error or "infrastructure error"
        detail = ", ".join(f"{h.oracle_id}:{h.k}" for h in outcome.verdict.hits) or "no oracle fired"
        return outcome.verdict.verdict, detail
    finally:
        await browser.stop()
        await sandbox.stop()


async def run_gate(
    contract: Contract,
    patched_dir: Path,
    original_dir: Path,
    settings: Settings,
    flow: Flow,
    attack_id: str,
    payload: Payload,
    diff: str,
    extra_test_globs: tuple[str, ...] = (),
) -> list[GateCheck]:
    checks = [check_no_test_files(diff, extra_test_globs)]
    if not checks[0].passed:
        return checks  # checked first, before anything runs

    verdict, detail = await run_one(contract, patched_dir, settings, flow, attack_id, payload, "gate-orig")
    checks.append(GateCheck("original_failure_gone", verdict == "pass", f"{verdict}: {detail}"))
    if verdict != "pass":
        return checks

    variant, note = heldout_payload(flow, attack_id, payload)
    verdict, detail = await run_one(contract, patched_dir, settings, flow, attack_id, variant, "gate-heldout")
    checks.append(GateCheck("heldout_variant_passes", verdict == "pass", f"{verdict}: {detail} ({note})"))
    if verdict != "pass":
        return checks

    verdict, detail = await run_one(contract, original_dir, settings, flow, attack_id, payload, "gate-revert")
    checks.append(
        GateCheck("revert_brings_failure_back", verdict in ("fail", "flaky"), f"without patch: {verdict}: {detail}")
    )
    return checks
