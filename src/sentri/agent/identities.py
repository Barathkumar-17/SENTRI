"""Log identities in through the contract's login flow, each with its own freshly generated account."""

from __future__ import annotations

from pathlib import Path

from playwright.async_api import Page

from sentri.agent.session import LoginFn
from sentri.contract.models import Contract
from sentri.contract.placeholders import DataGenerator, seed_for
from sentri.runner.flow import AttackStop, FlowExecutor


class LoginFailed(Exception):
    pass


def make_login(contract: Contract, tmp_dir: Path, run_tag: str) -> LoginFn | None:
    if contract.auth is None:
        return None
    flow = contract.flow(contract.auth.login_flow)
    timeout_ms = contract.action_timeout_s(flow) * 1000

    async def login(page: Page, who: str) -> None:
        gen = DataGenerator(
            seed_for(run_tag, "identity", who), contract.fixtures, tmp_dir / f"login-{who}", f"{run_tag}{who}"
        )
        ex = FlowExecutor(page, flow, gen, contract.app.url, timeout_ms)
        try:
            await ex.start()
            await ex.run_all()
        except AttackStop as exc:
            raise LoginFailed(f"login flow for identity {who!r} failed: {exc}") from None
        ok, detail = await ex.check_success()
        if not ok:
            raise LoginFailed(f"login flow for identity {who!r} did not succeed: {detail}")

    return login
