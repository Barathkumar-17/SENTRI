"""Log identities in, each with its own freshly generated account.

Two kinds, from the contract's `auth`:
- `login_flow`: a browser flow (for apps with HTML login forms);
- `api`: token login over HTTP (register and/or login requests; the returned token is sent on every request
  of that identity as e.g. `Authorization: Bearer <token>`), for single-page apps and API backends.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from sentri.agent.session import LoginFn
from sentri.contract.models import ApiAuth, ApiStep, Contract
from sentri.contract.placeholders import DataGenerator, seed_for
from sentri.runner.fence import set_identity_headers
from sentri.runner.flow import AttackStop, FlowExecutor


class LoginFailed(Exception):
    pass


def _dig(data: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        data = data.get(part) if isinstance(data, dict) else None
    return data


async def _api_call(page: Page, base_url: str, step: ApiStep, gen: DataGenerator, timeout_ms: float) -> Any:
    body = gen.resolve_obj(step.json_body) if step.json_body is not None else None
    try:
        resp = await page.context.request.fetch(
            base_url + "/" + step.path.lstrip("/"),
            method=step.method,
            data=json.dumps(body) if body is not None else None,
            headers={"content-type": "application/json"} if body is not None else {},
            timeout=timeout_ms,
        )
    except PlaywrightError as exc:
        raise LoginFailed(f"{step.method} {step.path} failed: {exc.message.splitlines()[0]}") from None
    if not 200 <= resp.status < 300:
        raise LoginFailed(f"{step.method} {step.path} returned HTTP {resp.status}: {(await resp.text())[:200]}")
    try:
        return await resp.json()
    except Exception:  # noqa: BLE001 - a non-JSON success simply carries no token
        return None


async def api_login(page: Page, base_url: str, api: ApiAuth, gen: DataGenerator, timeout_ms: float) -> dict[str, str]:
    token = None
    for step in (api.register, api.login):
        if step is None:
            continue
        data = await _api_call(page, base_url, step, gen, timeout_ms)
        token = _dig(data, api.token_field) or token
    if not token:
        raise LoginFailed(f"no token at '{api.token_field}' in the login response")
    headers = {api.header: f"{api.scheme} {token}".strip()}
    await set_identity_headers(page.context, headers)  # same-origin requests only (runner/fence.py)
    return headers


def make_login(contract: Contract, tmp_dir: Path, run_tag: str) -> LoginFn | None:
    auth = contract.auth
    if auth is None:
        return None
    base_url = contract.app.url

    if auth.api is not None:
        api = auth.api

        async def login_api(page: Page, who: str) -> dict[str, str] | None:
            gen = DataGenerator(
                seed_for(run_tag, "identity", who), contract.fixtures, tmp_dir / f"login-{who}", f"{run_tag}{who}"
            )
            return await api_login(page, base_url, api, gen, contract.defaults.action_timeout_s * 1000)

        return login_api

    assert auth.login_flow is not None
    flow = contract.flow(auth.login_flow)
    timeout_ms = contract.action_timeout_s(flow) * 1000

    async def login_flow(page: Page, who: str) -> dict[str, str] | None:
        gen = DataGenerator(
            seed_for(run_tag, "identity", who), contract.fixtures, tmp_dir / f"login-{who}", f"{run_tag}{who}"
        )
        ex = FlowExecutor(page, flow, gen, base_url, timeout_ms)
        try:
            await ex.start()
            await ex.run_all()
        except AttackStop as exc:
            raise LoginFailed(f"login flow for identity {who!r} failed: {exc}") from None
        ok, detail = await ex.check_success()
        if not ok:
            raise LoginFailed(f"login flow for identity {who!r} did not succeed: {detail}")
        return None

    return login_flow


async def start_authenticated(session: Any, who: str, pages: list[str]) -> tuple[bool, str]:
    """Begin exploration as test account `who`, verified against real app state, never a cookie or a label.

    Logs in through the ordinary identity mechanism, then opens login-only pages that recon proved (anon was
    redirected or refused while a user got the page): the account is authenticated only if the server really serves
    one of them to this session (same path, no login form). On any failure the session is returned to the logged-out
    identity so a run never believes it is logged in when it is not. Returns (ok, one-line detail without secrets)."""
    if not pages:
        await session.execute_switch("anon")
        return False, "recon proved no login-only page, so a login cannot be verified; exploration starts logged out"
    try:
        await session.execute_switch(who)
    except Exception as exc:  # noqa: BLE001 - a login that did not succeed must not pass as authenticated
        await session.execute_switch("anon")
        return False, f"login as {who} failed ({str(exc)[:120]}); exploration starts logged out"
    page = session.page
    base = session.base_url
    tried = []
    for path in pages[:3]:
        try:
            await page.goto(base + path, wait_until="load", timeout=session.timeout_ms)
        except PlaywrightError as exc:
            tried.append(f"{path}: {exc.message.splitlines()[0][:60]}")
            continue
        here = page.url.removeprefix(base).split("?")[0].split("#")[0] or "/"
        has_login_form = await page.locator("input[type=password]").count() > 0
        if here.rstrip("/") == path.rstrip("/") and not has_login_form:
            await page.goto(base + "/", wait_until="load", timeout=session.timeout_ms)
            return True, f"as {who}: login-only page {path} is served to this session (anon is redirected there)"
        tried.append(f"{path}: ended on {here or '/'}")
    await session.execute_switch("anon")
    return False, f"as {who}: no login-only page was served to this session ({'; '.join(tried)}); starting logged out"
