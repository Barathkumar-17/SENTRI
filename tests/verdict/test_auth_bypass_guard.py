"""auth_bypass must not fire on a single-page app's shell, and only trusts login proven by behaviour."""

from __future__ import annotations

from sentri.agent.recon import AppMap, EndpointEntry, PageEntry, Recon
from sentri.evidence.agent_models import AgentEvidence, ResponseRecord
from sentri.evidence.models import Evidence
from sentri.verdict.agent_oracles import auth_bypass, is_shell
from sentri.verdict.oracles import OracleConfig

CFG = OracleConfig(flow_id="agentic", attack_id="explore")
SHELL = (
    '<!doctype html><html><head><script src="/main.4f9a1c2be7.js"></script></head>'
    "<body><app-root></app-root></body></html>"
)


def _anon(text: str, shells=(SHELL,)) -> AgentEvidence:
    return AgentEvidence(
        base=Evidence(),
        responses=(ResponseRecord("anon", "GET", "/dashboard", 200, text),),
        protected_paths=("/dashboard",),
        anon_shells=shells,
    )


def test_the_spa_shell_on_a_protected_route_is_not_a_bypass():
    other_build = SHELL.replace("4f9a1c2be7", "88aa77bb66")  # a nonce or build hash differs
    assert is_shell(other_build, (SHELL,))
    assert not auth_bypass(_anon(other_build), CFG).fired


def test_real_private_content_still_fires():
    page = "<h1>Dashboard</h1><table><tr><td>Invoice</td><td>Balance due</td><td>Account settings</td></tr></table>"
    r = auth_bypass(_anon(page), CFG)
    assert r.fired and r.error_key == "protected_page"
    assert auth_bypass(_anon(page, shells=()), CFG).fired  # no shell known: the old rule applies


def test_only_behaviour_proves_a_page_needs_login():
    app_map = AppMap(
        base_url="http://x",
        pages=[
            PageEntry(path="/tasks", requires_login=True, auth_evidence="redirect"),
            PageEntry(path="/maybe", requires_login=True),  # e.g. declared or inferred, never proven
        ],
        endpoints=[
            EndpointEntry(method="GET", path="/api/me", requires_login=True, auth_evidence="401"),
            EndpointEntry(method="GET", path="/api/spec-says", requires_login=True, from_spec=True),
        ],
    )
    assert app_map.proven_protected_paths() == {"/tasks", "/api/me"}
    assert app_map.protected_paths() >= {"/tasks", "/maybe", "/api/me", "/api/spec-says"}


async def test_an_error_loading_a_page_logged_out_is_not_evidence():
    class FakeSession:
        base_url = "http://x"
        login_fn = None

        async def execute_switch(self, who):
            pass

    recon = Recon(FakeSession(), gen=None)  # type: ignore[arg-type]
    recon.map.pages = [
        PageEntry(path="/broken", seen_as=["a"]),
        PageEntry(path="/tasks", seen_as=["a"]),
        PageEntry(path="/forbidden", seen_as=["a"]),
    ]
    results = {
        "/broken": None,
        "/tasks": {"final_path": "/login", "status": 200},
        "/forbidden": {"final_path": "/forbidden", "status": 403},
    }

    async def visit(path):
        return results[path]

    recon._visit = visit  # type: ignore[method-assign]
    await recon.auth_boundary()
    broken, tasks, forbidden = recon.map.pages
    assert not broken.requires_login and broken.anon_outcome == "error"
    assert tasks.requires_login and tasks.auth_evidence == "redirect"
    assert forbidden.requires_login and forbidden.auth_evidence == "403"
