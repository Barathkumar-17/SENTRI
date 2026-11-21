"""Optional Fixer: proposes a patch for a confirmed bug and opens a DRAFT pull request if the gate passes.

It never merges, never pushes outside `sentri/` branches, and never marks a bug fixed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from sentri.config import Settings
from sentri.contract.models import Contract
from sentri.fixer.gate import GateCheck, run_gate
from sentri.ledger.ledger import Ledger
from sentri.llm import BudgetedProvider, PatchProposal, load_prompt
from sentri.redact import redact

log = logging.getLogger(__name__)

_PY_FRAME = re.compile(r'File "([^"]+)", line (\d+)')
_JS_FRAME = re.compile(r"\(?([^\s()]+\.(?:js|ts|mjs|cjs|tsx|jsx)):(\d+):\d+\)?")
MAX_FILES = 10
MAX_BYTES = 200_000


class FixerError(Exception):
    pass


@dataclass
class FixResult:
    bug_id: str
    branch: str | None = None
    worktree: Path | None = None
    gate: list[GateCheck] = field(default_factory=list)
    pr_url: str | None = None
    summary: str = ""

    @property
    def gate_passed(self) -> bool:
        return bool(self.gate) and all(c.passed for c in self.gate) and len(self.gate) == 4


def _git(args: list[str], cwd: Path, check: bool = True, input_text: str | None = None) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, input=input_text, check=False)
    if check and proc.returncode != 0:
        raise FixerError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def source_files_from_logs(log_lines: list[str], repo: Path) -> list[Path]:
    """Map stack-trace paths (possibly container paths like /app/app.py) to files in the repo."""
    found: list[Path] = []
    all_files = [p for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts and ".venv" not in p.parts]
    for line in log_lines:
        for m in [*_PY_FRAME.finditer(line), *_JS_FRAME.finditer(line)]:
            raw = m.group(1).replace("\\", "/")
            if "site-packages" in raw or "node_modules" in raw or raw.startswith("<"):
                continue
            parts = raw.split("/")
            for n in range(len(parts), 0, -1):
                suffix = "/".join(parts[-n:])
                matches = [p for p in all_files if p.as_posix().endswith("/" + suffix)]
                if len(matches) == 1:
                    if matches[0] not in found:
                        found.append(matches[0])
                    break
    return found[:MAX_FILES]


class Fixer:
    def __init__(
        self, contract: Contract, workdir: Path, settings: Settings, ledger: Ledger, provider: BudgetedProvider
    ):
        self.contract = contract
        self.workdir = workdir.resolve()
        self.settings = settings
        self.ledger = ledger
        self.provider = provider

    def _latest_hit(self, bug_id: str) -> tuple[dict[str, object], list[str]]:
        row = self.ledger.conn.execute(
            "SELECT c.* FROM bug_hits h JOIN checks c ON c.id=h.check_id WHERE h.bug_id=? ORDER BY c.id DESC LIMIT 1",
            (bug_id,),
        ).fetchone()
        if row is None:
            raise FixerError(f"{bug_id} has no recorded failing check")
        logs: list[str] = []
        evidence: dict[str, object] = {}
        if row["evidence_path"] and Path(row["evidence_path"]).exists():
            data = json.loads(Path(row["evidence_path"]).read_text(encoding="utf-8"))
            ev = data.get("evidence", {})
            logs = [line["text"] for line in ev.get("logs", [])]
            evidence = {
                "http": [h for h in ev.get("http", []) if h.get("same_origin")][:40],
                "console": ev.get("console", [])[:20],
                "log_tail": logs[-80:],
                "oracles": [o for o in data.get("oracles", []) if o.get("fired")],
                "actions": ev.get("actions", []),
            }
        return {"check": dict(row), "evidence": evidence}, logs

    async def propose(self, bug_id: str, repo: Path) -> tuple[PatchProposal, dict[str, object]]:
        bug = self.ledger.bug(bug_id)
        if bug is None:
            raise FixerError(f"unknown bug {bug_id}")
        if bug["status"] not in ("open", "flaky"):
            raise FixerError(f"{bug_id} is {bug['status']}; only open or flaky bugs are fixed")
        context, logs = self._latest_hit(bug_id)
        files = source_files_from_logs(logs, repo)
        if not files:
            raise FixerError("no source location in the evidence logs; the Fixer needs a stack trace")
        sources, size = {}, 0
        for f in files:
            text = f.read_text(encoding="utf-8", errors="replace")
            if size + len(text) > MAX_BYTES:
                break
            sources[f.relative_to(repo).as_posix()] = text
            size += len(text)
        if not self.provider.available:
            raise FixerError("the Fixer needs a model (set LLM_API_KEY)")
        request = {
            "bug": dict(bug),
            "failing_check": context["check"],
            "evidence": context["evidence"],
            "files": sources,
        }
        result = await self.provider.complete_json(
            "fixer",
            load_prompt("fixer"),
            redact(json.dumps(request, indent=1, default=str)),
            PatchProposal,
            max_tokens=16000,
        )
        if not result.ok or result.value is None:
            raise FixerError(f"model did not return a patch: {result.error}")
        return result.value, context

    async def fix(self, bug_id: str, open_pr: bool = True, test_globs: tuple[str, ...] = ()) -> FixResult:
        repo = Path(_git(["rev-parse", "--show-toplevel"], self.workdir))
        rel_workdir = self.workdir.relative_to(repo)
        proposal, context = await self.propose(bug_id, repo)
        result = FixResult(bug_id, summary=proposal.summary)

        branch = f"sentri/fix-{bug_id.lower()}"
        worktree = repo / ".sentri" / "fix" / bug_id
        if worktree.exists():
            _git(["worktree", "remove", "--force", str(worktree)], repo, check=False)
        _git(["worktree", "add", "-B", branch, str(worktree), "HEAD"], repo)
        result.branch, result.worktree = branch, worktree
        diff = proposal.diff if proposal.diff.endswith("\n") else proposal.diff + "\n"
        try:
            _git(["apply", "--check", "-"], worktree, input_text=diff)
        except FixerError as exc:
            raise FixerError(f"patch does not apply: {exc}") from None
        _git(["apply", "-"], worktree, input_text=diff)

        check = context["check"]
        assert isinstance(check, dict)
        flow = self.contract.flow(str(check["flow_id"]))
        payload = json.loads(str(check["payload"] or "{}"))
        result.gate = await run_gate(
            self.contract,
            worktree / rel_workdir,
            self.workdir,
            self.settings,
            flow,
            str(check["attack_id"]),
            payload,
            diff,
            test_globs,
        )
        if not result.gate_passed:
            return result

        _git(["add", "-A"], worktree)
        _git(["commit", "-m", f"Fix {bug_id}: {flow.id} x {check['attack_id']}\n\n{proposal.summary}"], worktree)
        if open_pr:
            _git(["push", "-u", "origin", branch], worktree)
            body = self._pr_body(bug_id, proposal, result.gate)
            proc = await asyncio.to_thread(
                subprocess.run,
                [
                    "gh",
                    "pr",
                    "create",
                    "--draft",
                    "--head",
                    branch,
                    "--title",
                    f"Sentri fix for {bug_id}",
                    "--body",
                    body,
                ],
                cwd=worktree,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                raise FixerError(f"gh pr create failed: {proc.stderr.strip()}")
            result.pr_url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else None
        return result

    @staticmethod
    def _pr_body(bug_id: str, proposal: PatchProposal, gate: list[GateCheck]) -> str:
        rows = "\n".join(f"| {c.name} | {'pass' if c.passed else 'FAIL'} | {c.detail} |" for c in gate)
        return (
            f"Draft fix proposed by Sentri for **{bug_id}**. A human must review and merge it.\n\n"
            f"{proposal.summary}\n\n### Gate (plain code)\n| Check | Result | Detail |\n|---|---|---|\n{rows}\n"
        )
