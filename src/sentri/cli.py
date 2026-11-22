"""Command-line entry points (docs/reference/cli.md). A thin layer over the engine modules."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, Literal, cast

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape
from rich.table import Table

from sentri import __version__
from sentri.config import Settings
from sentri.contract.loader import ContractError, approve_contract, load_contract
from sentri.ledger import HumanDecision, Ledger
from sentri.llm import LLMConfigError, describe_provider, make_provider

app = typer.Typer(
    help="Sentri: attacks a running web app like a hostile user. Plain code decides pass/fail.",
    no_args_is_help=True,
    add_completion=False,
)
contract_app = typer.Typer(help="Validate and approve demo contracts.", no_args_is_help=True)
bug_app = typer.Typer(help="Inspect bugs and record human decisions.", no_args_is_help=True)
app.add_typer(contract_app, name="contract")
app.add_typer(bug_app, name="bug")

for _stream in (sys.stdout, sys.stderr):
    # Legacy Windows code pages cannot print every character; never crash on output.
    if hasattr(_stream, "reconfigure") and (getattr(_stream, "encoding", "") or "").lower() not in ("utf-8", "utf8"):
        _stream.reconfigure(errors="replace")

console = Console()
err = Console(stderr=True)

EXIT_OK, EXIT_GATE, EXIT_CONTRACT, EXIT_APP, EXIT_INFRA = 0, 1, 2, 3, 4

FEED_STYLE = {
    "check_finished": "bold",
    "regression": "bold red",
    "bug_opened": "red",
    "bug_fixed": "green",
    "run_aborted": "bold red",
    "run_finished": "bold green",
    "llm_fallback": "yellow",
    "action": "dim",
    "attempt_finished": "dim",
}


def _echo(kind: str, message: str, reason: str | None) -> None:
    style = FEED_STYLE.get(kind, "")
    text = escape(message)
    line = f"[{style}]{text}[/{style}]" if style else text
    if reason:
        line += f"  [italic cyan]({escape(reason)})[/italic cyan]"
    console.print(line, highlight=False)


def _settings(
    db: Path | None,
    provider: str | None = None,
    base_url: str | None = None,
    model_fast: str | None = None,
    model_smart: str | None = None,
    ollama: str | None = None,
) -> Settings:
    s = Settings()
    if ollama:
        s.use_ollama(ollama)
    if db is not None:
        s.db_path = db
    if provider:
        s.llm_provider = provider
    if base_url:
        s.llm_base_url = base_url
    if model_fast:
        s.model_fast = model_fast
    if model_smart:
        s.model_smart = model_smart or model_fast or s.model_smart
    elif model_fast:
        s.model_smart = model_fast
    return s


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(console=err, show_path=False)],
    )
    if not verbose:
        logging.getLogger("sentri.feed").setLevel(logging.WARNING)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"sentri {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")] = False,
    version: Annotated[bool, typer.Option("--version", callback=_version_callback, is_eager=True)] = False,
) -> None:
    _setup_logging(verbose)


DbOpt = Annotated[Path | None, typer.Option("--db", help="Ledger path (default .sentri/ledger.db or SENTRI_DB).")]
ProviderOpt = Annotated[
    str | None,
    typer.Option(
        "--llm-provider",
        help="auto | claude_code | codex | gemini_cli | anthropic | openai_compatible | none (see `sentri models`)",
    ),
]
BaseUrlOpt = Annotated[str | None, typer.Option("--llm-base-url", help="Base URL for openai_compatible.")]
FastOpt = Annotated[
    str | None, typer.Option("--model", "--model-fast", help="Model for payloads, probes and the planner.")
]
SmartOpt = Annotated[str | None, typer.Option("--model-smart", help="Model for the fixer.")]
OllamaOpt = Annotated[
    str | None,
    typer.Option(
        "--ollama", help="Use a local Ollama model (e.g. qwen2.5:7b). No API key; nothing leaves the machine."
    ),
]


def _provider(settings: Settings, enabled: bool = True):  # type: ignore[no-untyped-def]
    try:
        return make_provider(settings, enabled)
    except LLMConfigError as exc:
        err.print(f"[red]LLM configuration error:[/red] {exc}")
        raise typer.Exit(EXIT_CONTRACT) from None


def _load(contract: Path):  # type: ignore[no-untyped-def]
    try:
        return load_contract(contract)
    except ContractError as exc:
        err.print(f"[red]Contract invalid:[/red] {contract}")
        for e in exc.errors:
            err.print(f"  - {e}")
        raise typer.Exit(EXIT_CONTRACT) from None


# ---- setup / doctor -----------------------------------------------------------------------------------
@app.command()
def setup(
    with_deps: Annotated[bool, typer.Option(help="Also install OS packages (Linux, needs sudo).")] = False,
) -> None:
    """Install the Chromium browser Sentri drives."""
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    if with_deps:
        cmd.append("--with-deps")
    console.print(f"running: {' '.join(cmd)}")
    raise typer.Exit(subprocess.call(cmd))


def _check_chromium() -> tuple[bool, str]:
    async def probe() -> str:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            version = browser.version
            await browser.close()
            return version

    try:
        return True, f"Chromium {asyncio.run(probe())}"
    except Exception as exc:  # noqa: BLE001
        return False, f"not usable ({str(exc).splitlines()[0][:120]}); run `sentri setup`"


def _check_docker() -> tuple[bool, str]:
    if shutil.which("docker") is None:
        return False, "docker CLI not found (only needed for app.docker mode)"
    try:
        import docker

        client = docker.from_env()
        info = client.version()
        return True, f"Docker {info.get('Version', '?')}"
    except Exception as exc:  # noqa: BLE001
        return False, f"daemon not reachable ({str(exc)[:100]}); start Docker Desktop / dockerd"


def _check_reverse_dns(limit_s: float = 3.0) -> tuple[bool | None, str]:
    """Slow reverse DNS for 127.0.0.1 makes Python dev servers (Flask, http.server) start slowly on every reset."""
    import socket
    import threading

    result: list[float] = []

    def probe() -> None:
        t0 = time.monotonic()
        socket.getfqdn("127.0.0.1")
        result.append(time.monotonic() - t0)

    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    thread.join(limit_s)
    if result and result[0] < 1.0:
        return True, f"reverse lookup of 127.0.0.1 in {result[0]:.2f}s"
    took = f"{result[0]:.1f}s" if result else f"over {limit_s:g}s"
    return None, (
        f"reverse lookup of 127.0.0.1 takes {took}; Flask/http.server dev servers will start that slowly on every "
        "reset. Fix: make sure /etc/hosts maps 127.0.0.1 to localhost, or bind your dev server without a lookup"
    )


def _check_endpoint(base_url: str, api_key: str | None) -> tuple[bool, str]:
    """GET {base}/models on an OpenAI-compatible server, so a missing local model server shows up here."""
    import httpx

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = httpx.get(base_url.rstrip("/") + "/models", headers=headers, timeout=5)
    except httpx.HTTPError as exc:
        hint = " (is `ollama serve` running?)" if "11434" in base_url else ""
        return False, f"not reachable: {type(exc).__name__}{hint}"
    if resp.status_code in (401, 403):
        return False, f"reachable but rejected the key (HTTP {resp.status_code})"
    if resp.status_code >= 400:
        return True, f"reachable (HTTP {resp.status_code} on /models)"
    try:
        ids = [m.get("id") for m in resp.json().get("data", [])]
    except ValueError:
        ids = []
    return True, f"reachable, {len(ids)} models listed" if ids else "reachable"


@app.command()
def doctor(
    llm_provider: ProviderOpt = None,
    llm_base_url: BaseUrlOpt = None,
    model: FastOpt = None,
    model_smart: SmartOpt = None,
    ollama: OllamaOpt = None,
) -> None:
    """Check this machine: Python, browser, Docker, git, LLM configuration."""
    s = _settings(None, llm_provider, llm_base_url, model, model_smart, ollama)
    table = Table("Check", "Status", "Detail")
    py_ok = sys.version_info >= (3, 12)
    rows: list[tuple[str, bool | None, str]] = [
        ("Python", py_ok, f"{platform.python_version()} on {platform.system()} {platform.machine()}"),
        ("Chromium", *_check_chromium()),
        ("Docker", *_check_docker()),
        ("Local DNS", *_check_reverse_dns()),
        (
            "git",
            shutil.which("git") is not None or None,
            "found" if shutil.which("git") else "not found (fingerprints still work)",
        ),
        (
            "gh CLI",
            shutil.which("gh") is not None or None,
            "found" if shutil.which("gh") else "not found (only for `sentri fix`)",
        ),
    ]
    try:
        p = make_provider(s)
        llm_ok: bool | None = True if p.inner.name != "none" else None
        detail = describe_provider(s, p)
        if llm_ok is None:
            detail += "; optional: set an API key, --llm-base-url, or --ollama MODEL"
        elif s.llm_provider == "openai_compatible" and s.llm_base_url:
            reachable, why = _check_endpoint(s.llm_base_url, s.api_key)
            llm_ok = reachable
            detail += f"; {why}"
        rows.append(("LLM", llm_ok, detail))
    except LLMConfigError as exc:
        rows.append(("LLM", False, str(exc)))
    for name, ok, detail in rows:
        status = "[green]ok[/green]" if ok else ("[yellow]optional[/yellow]" if ok is None else "[red]missing[/red]")
        if name == "Docker" and not ok:
            status = "[yellow]optional[/yellow]"
        if name == "Local DNS" and ok is None:
            status = "[yellow]slow[/yellow]"
        table.add_row(name, status, detail)
    console.print(table)
    required_ok = py_ok and rows[1][1]
    raise typer.Exit(EXIT_OK if required_ok else EXIT_INFRA)


@app.command()
def models() -> None:
    """List every model source on this machine: signed-in agent CLIs, local servers, and set key variables."""
    from sentri.control.discovery import discover_all

    found = asyncio.run(discover_all())
    table = Table("Source", "Status", "Use with")
    for c in found["agent_clis"]:
        table.add_row(
            c["label"],
            f"[green]found[/green] {c['path']}" if c["available"] else "not installed",
            f"--llm-provider {c['preset']}",
        )
    for s in found["local_servers"]:
        name = "Ollama" if s["preset"] == "ollama" else "LM Studio"
        ids = ", ".join(m["id"] for m in s["models"][:6]) or "no models installed"
        status = f"[green]running[/green]: {ids}" if s["running"] else (s["install_hint"] or "not running")
        use = (
            f"--ollama {s['models'][0]['id']}"
            if s["preset"] == "ollama" and s["models"]
            else (f"--llm-provider openai_compatible --llm-base-url {s['base_url']} --model <id>")
        )
        table.add_row(name, status, use)
    for k in found["api_keys"]:
        status = f"[green]set[/green]: {', '.join(k['variables'])}" if k["available"] else "no key variable set"
        table.add_row(f"API key: {k['preset']}", status, "choose it explicitly (keys are never used automatically)")
    console.print(table)
    choice = found["auto_choice"]
    if choice:
        model = f", model {choice['model']}" if choice.get("model") else ""
        console.print(f"--llm-provider auto would use: [bold]{choice['preset']}[/bold]{model}")
    else:
        console.print("--llm-provider auto would use: no model (basic tests with built-in payloads)")


# ---- contract -----------------------------------------------------------------------------------------
@contract_app.command("check")
def contract_check(file: Path) -> None:
    """Validate a contract file."""
    c = _load(file)
    status = "approved" if c.approved else "NOT approved"
    console.print(f"[green]valid[/green]: {len(c.flows)} flows, attacks {', '.join(c.attacks_enabled)} ({status})")


@contract_app.command("approve")
def contract_approve(
    file: Path,
    as_: Annotated[str, typer.Option("--as", help="Who approves (name or email).")],
    out: Annotated[Path, typer.Option(help="Where to write the approved contract.")] = Path("contract.yaml"),
) -> None:
    """Validate a draft and stamp the human approval into it."""
    try:
        c = approve_contract(file, as_, out)
    except ContractError as exc:
        err.print("[red]Contract invalid:[/red]")
        for e in exc.errors:
            err.print(f"  - {e}")
        raise typer.Exit(EXIT_CONTRACT) from None
    console.print(f"approved by {c.meta.approved_by} at {c.meta.approved_at} -> {out}")


# ---- plan ---------------------------------------------------------------------------------------------
@app.command()
def plan(
    app_dir: Annotated[Path, typer.Option(help="The app's directory.")] = Path("."),
    run: Annotated[str | None, typer.Option("--run", help="Command that starts the app (command mode).")] = None,
    port: Annotated[int, typer.Option(help="Port the app listens on.")] = 5000,
    dockerfile: Annotated[str | None, typer.Option(help="Use Docker mode with this Dockerfile.")] = None,
    readme: Annotated[Path | None, typer.Option(help="README to read (default: app-dir/README.md).")] = None,
    out: Annotated[Path, typer.Option(help="Draft output.")] = Path("contract.draft.yaml"),
    no_llm: Annotated[bool, typer.Option("--no-llm", help="Heuristic draft without a model.")] = False,
    llm_provider: ProviderOpt = None,
    llm_base_url: BaseUrlOpt = None,
    model: FastOpt = None,
    model_smart: SmartOpt = None,
    ollama: OllamaOpt = None,
) -> None:
    """Start the app, crawl it and propose a draft contract for a human to review."""
    from sentri.contract.models import AppConfig
    from sentri.planner import PlanResult, read_readme
    from sentri.planner import plan as run_plan
    from sentri.runner.browser import BrowserManager
    from sentri.sandbox import SandboxError, make_sandbox

    s = _settings(None, llm_provider, llm_base_url, model, model_smart, ollama)
    provider = _provider(s, not no_llm)
    if (run is None) == (dockerfile is None):
        err.print("[red]give exactly one of --run or --dockerfile[/red]")
        raise typer.Exit(EXIT_CONTRACT)
    section: dict[str, object] = {"port": port, "base_url": f"http://127.0.0.1:{port}"}
    if run:
        section["run"] = run
    else:
        section["docker"] = {"context": ".", "dockerfile": dockerfile}
    app_cfg = AppConfig.model_validate(section)
    readme_text = read_readme(readme or app_dir / "README.md")

    async def go() -> PlanResult:
        sandbox = make_sandbox(app_cfg, app_dir.resolve())
        await sandbox.start()
        try:
            async with BrowserManager(headless=s.headless) as browser:
                return await run_plan(browser, provider, section, readme_text, sandbox)
        finally:
            await sandbox.stop()

    console.print(f"planning with {describe_provider(s, provider)}")
    try:
        result = asyncio.run(go())
    except SandboxError as exc:
        err.print(f"[red]app failed to start:[/red] {exc}")
        raise typer.Exit(EXIT_APP) from None
    out.write_text(result.yaml_text, encoding="utf-8")
    console.print(
        f"draft written to {out} ({result.source}); review it, then `sentri contract approve {out} --as NAME`"
    )
    for flow_id, sels in result.unverified.items():
        console.print(f"[yellow]unverified selectors in {flow_id}:[/yellow] {', '.join(sels)}")
    for e in result.errors:
        console.print(f"[yellow]needs editing:[/yellow] {e}")


# ---- run ----------------------------------------------------------------------------------------------
def _default_commit(workdir: Path) -> tuple[str, str]:
    from sentri.runner.fingerprint import git_branch, git_commit

    return git_commit(workdir) or "local", git_branch(workdir) or "local"


@app.command("run")
def run_cmd(
    contract: Annotated[Path, typer.Option(help="Approved contract file.")] = Path("contract.yaml"),
    commit: Annotated[str | None, typer.Option(help="Commit sha (default: git HEAD).")] = None,
    branch: Annotated[str | None, typer.Option(help="Branch name (default: current git branch).")] = None,
    out: Annotated[Path | None, typer.Option(help="Write report.md here.")] = None,
    base_branch: Annotated[str, typer.Option(help="Branch to compare the score against.")] = "main",
    serve: Annotated[bool, typer.Option("--serve", help="Also start the dashboard.")] = False,
    port: Annotated[int, typer.Option(help="Dashboard port with --serve.")] = 8787,
    fresh: Annotated[bool, typer.Option("--fresh", help="Do not reuse passing checks.")] = False,
    no_llm: Annotated[bool, typer.Option("--no-llm", help="Static payloads only.")] = False,
    allow_unapproved: Annotated[bool, typer.Option("--allow-unapproved")] = False,
    only: Annotated[list[str] | None, typer.Option(help="Limit to FLOW or FLOW:ATTACK (repeatable).")] = None,
    skip_preflight: Annotated[bool, typer.Option("--skip-preflight")] = False,
    attempts: Annotated[int, typer.Option(help="Attempts per check (3 = spec rule).")] = 3,
    fail_under: Annotated[int | None, typer.Option(help="Exit 1 if the score is below this.")] = None,
    fail_on_regression: Annotated[bool, typer.Option("--fail-on-regression")] = False,
    db: DbOpt = None,
    llm_provider: ProviderOpt = None,
    llm_base_url: BaseUrlOpt = None,
    model: FastOpt = None,
    model_smart: SmartOpt = None,
    ollama: OllamaOpt = None,
) -> None:
    """Run the attack grid against the app and record verdicts in the ledger."""
    from sentri.report import build_report
    from sentri.runner.orchestrator import RunOptions, RunOrchestrator, RunResult

    c = _load(contract)
    if not c.approved and not allow_unapproved:
        err.print("[red]contract is not approved[/red]; run `sentri contract approve` (or pass --allow-unapproved)")
        raise typer.Exit(EXIT_CONTRACT)
    s = _settings(db, llm_provider, llm_base_url, model, model_smart, ollama)
    s.attempts = max(1, attempts)
    s.live_view = serve
    provider = _provider(s, not no_llm)
    workdir = contract.resolve().parent
    default_commit, default_branch = _default_commit(workdir)
    options = RunOptions(
        commit=commit or default_commit,
        branch=branch or default_branch,
        reuse=not fresh,
        only=only or [],
        preflight=not skip_preflight,
        exclude_paths=[contract, *([out] if out else [])],
    )
    ledger = Ledger(s.db_path)
    console.print(f"sentri {__version__} · llm: {describe_provider(s, provider)} · ledger: {s.db_path}")

    async def go() -> RunResult:
        server_task = None
        if serve:
            server_task = asyncio.create_task(_serve_async(s, port))
            console.print(f"dashboard: http://127.0.0.1:{port}")
        try:
            return await RunOrchestrator(c, workdir, s, ledger, provider, options, echo=_echo).run()
        finally:
            if server_task is not None:
                server_task.cancel()

    try:
        result = asyncio.run(go())
    except KeyboardInterrupt:
        err.print("[yellow]interrupted[/yellow]")
        raise typer.Exit(130) from None
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc):
            err.print("[red]Chromium is not installed[/red]; run `sentri setup`")
            raise typer.Exit(EXIT_INFRA) from None
        raise

    if out is not None:
        out.write_text(build_report(ledger, result.run_id, base_branch, s.evidence_url), encoding="utf-8")
        console.print(f"report written to {out}")
    if result.status != "finished":
        err.print(f"[red]run aborted:[/red] {result.error}")
        raise typer.Exit(EXIT_CONTRACT if result.error and "preflight" in result.error else EXIT_APP)
    b = result.breakdown
    assert b is not None
    console.print(f"[bold]readiness: {b.score if b.score is not None else 'n/a'}[/bold]   {b.formula}")
    if fail_under is not None and (b.score or 0) < fail_under:
        raise typer.Exit(EXIT_GATE)
    if fail_on_regression and result.regressions:
        raise typer.Exit(EXIT_GATE)


# ---- report -------------------------------------------------------------------------------------------
@app.command()
def report(
    run: Annotated[int | None, typer.Option(help="Run id (default: latest).")] = None,
    base_branch: Annotated[str, typer.Option()] = "main",
    out: Annotated[Path | None, typer.Option()] = None,
    efficiency: Annotated[bool, typer.Option("--efficiency", help="Time and cost per run.")] = False,
    db: DbOpt = None,
) -> None:
    """Rebuild report.md from the ledger."""
    from sentri.report import build_report, efficiency_table

    s = _settings(db)
    ledger = Ledger(s.db_path)
    if efficiency:
        console.print(efficiency_table(ledger), markup=False)
        return
    row = ledger.run(run) if run else ledger.latest_run(finished_only=False)
    if row is None:
        err.print("no runs in the ledger")
        raise typer.Exit(EXIT_CONTRACT)
    text = build_report(ledger, row["id"], base_branch, s.evidence_url)
    if out:
        out.write_text(text, encoding="utf-8")
        console.print(f"report written to {out}")
    else:
        console.print(text, markup=False)


# ---- bugs ---------------------------------------------------------------------------------------------
@bug_app.command("list")
def bug_list(
    status: Annotated[str | None, typer.Option()] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    db: DbOpt = None,
) -> None:
    """List bugs in the ledger."""
    ledger = Ledger(_settings(db).db_path)
    if as_json:
        print(json.dumps([dict(b) for b in ledger.bugs(status)], indent=1))
        return
    table = Table("Bug", "Status", "Flow", "Attack", "Oracle", "First", "Fixed", "Regr.", "Detail")
    for col in table.columns[:5]:
        col.no_wrap = True
    for b in ledger.bugs(status):
        flag = " (review)" if b["needs_review"] else ""
        table.add_row(
            b["id"],
            b["status"] + flag,
            b["flow_id"],
            b["attack_id"],
            b["oracle_id"],
            (b["first_commit"] or "")[:7],
            (b["fixed_commit"] or "")[:7],
            str(b["regressions"]),
            (b["sample_detail"] or "")[:60],
        )
    console.print(table)


@bug_app.command("mark")
def bug_mark(
    bug_id: str,
    status: Annotated[str, typer.Argument(help="intended | open")],
    note: Annotated[str, typer.Option(help="Reason (required for intended).")] = "",
    by: Annotated[str, typer.Option(help="Who decides.")] = "",
    db: DbOpt = None,
) -> None:
    """Record a human decision about a bug."""
    if status not in ("intended", "open"):
        err.print("status must be 'intended' or 'open'")
        raise typer.Exit(EXIT_CONTRACT)
    ledger = Ledger(_settings(db).db_path)
    try:
        who = by or os.environ.get("USER") or os.environ.get("USERNAME", "")
        decision = HumanDecision(bug_id.upper(), cast(Literal["intended", "open"], status), note, who)
        ledger.mark(decision)
    except (ValueError, KeyError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(EXIT_CONTRACT) from None
    console.print(f"{bug_id.upper()} -> {status}")


# ---- serve --------------------------------------------------------------------------------------------
async def _serve_async(s: Settings, port: int, host: str = "127.0.0.1") -> None:
    import uvicorn

    from sentri.dashboard import create_app

    extra = () if host in ("127.0.0.1", "localhost") else (host.lower(),) if host != "0.0.0.0" else ()
    web = create_app(s.db_path, s.evidence_dir, s.live_path, settings=s, extra_hosts=extra)
    config = uvicorn.Config(web, host=host, port=port, log_level="warning")
    await uvicorn.Server(config).serve()


def _serve(port: int, host: str, db: Path | None, open_browser: bool) -> None:
    s = _settings(db)
    if host != "127.0.0.1":
        err.print(
            "[yellow]warning: binding beyond localhost. The local app has no login, so only do this on a "
            "network you trust; requests must use that exact host name.[/yellow]"
        )
    Ledger(s.db_path).close()  # create/migrate the ledger so the API works on a fresh machine
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"
    console.print(f"Sentri is running at {url}  (Ctrl+C stops it)")
    if open_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        asyncio.run(_serve_async(s, port, host))
    except KeyboardInterrupt:
        console.print("stopped")


@app.command()
def ui(
    port: Annotated[int, typer.Option()] = 8787,
    db: DbOpt = None,
    no_browser: Annotated[bool, typer.Option("--no-browser", help="Print the URL instead of opening it.")] = False,
) -> None:
    """Open the Sentri web app: pick a project, choose a model, plan, run and read results in the browser."""
    _serve(port, "127.0.0.1", db, open_browser=not no_browser)


@app.command()
def serve(
    port: Annotated[int, typer.Option()] = 8787,
    host: Annotated[str, typer.Option(help="Bind address. Keep 127.0.0.1 unless you need network access.")] = (
        "127.0.0.1"
    ),
    db: DbOpt = None,
) -> None:
    """Serve the dashboard and control API without opening a browser (for scripts and CI)."""
    _serve(port, host, db, open_browser=False)


# ---- fix ----------------------------------------------------------------------------------------------
@app.command()
def fix(
    bug_id: str,
    contract: Annotated[Path, typer.Option()] = Path("contract.yaml"),
    no_pr: Annotated[bool, typer.Option("--no-pr", help="Run the gate but do not push or open a PR.")] = False,
    db: DbOpt = None,
    llm_provider: ProviderOpt = None,
    llm_base_url: BaseUrlOpt = None,
    model: FastOpt = None,
    model_smart: SmartOpt = None,
    ollama: OllamaOpt = None,
) -> None:
    """Optional Fixer: propose a patch, run the gate, open a DRAFT pull request if it passes."""
    from sentri.fixer import Fixer, FixerError

    c = _load(contract)
    s = _settings(db, llm_provider, llm_base_url, model, model_smart, ollama)
    provider = _provider(s)
    ledger = Ledger(s.db_path)
    fixer = Fixer(c, contract.resolve().parent, s, ledger, provider)
    try:
        result = asyncio.run(fixer.fix(bug_id.upper(), open_pr=not no_pr))
    except FixerError as exc:
        err.print(f"[red]fixer:[/red] {exc}")
        raise typer.Exit(EXIT_GATE) from None
    table = Table("Gate check", "Result", "Detail")
    for g in result.gate:
        table.add_row(g.name, "[green]pass[/green]" if g.passed else "[red]FAIL[/red]", g.detail)
    console.print(table)
    if not result.gate_passed:
        console.print(f"gate failed; worktree kept at {result.worktree} for inspection")
        raise typer.Exit(EXIT_GATE)
    console.print(f"draft PR: {result.pr_url}" if result.pr_url else f"gate passed; branch {result.branch}")
    console.print(json.dumps({"summary": result.summary}))


if __name__ == "__main__":
    app()
