"""xss_reflection and csrf_missing: code-owned probes, plain-code verdicts."""

from __future__ import annotations

import json

from sentri.agent.explorer import ExploreBudget, Explorer
from sentri.agent.recon import AppMap, PageEntry
from sentri.agent.session import strip_csrf
from sentri.agent.strategy import Strategy
from sentri.evidence.agent_models import AgentEvidence, CsrfProbe, ResponseRecord
from sentri.evidence.models import Evidence
from sentri.llm import BudgetedProvider, NullProvider
from sentri.verdict.agent_oracles import csrf_missing, xss_reflection
from sentri.verdict.oracles import OracleConfig

CFG = OracleConfig(flow_id="agentic", attack_id="explore")
MARK = '"><sentri-xss-abc123>'


def _xss(text: str, ctype: str = "text/html; charset=utf-8") -> AgentEvidence:
    r = ResponseRecord("anon", "GET", "/search", 200, text, None, ctype)
    return AgentEvidence(base=Evidence(), responses=(r,), xss_markers=(MARK,))


def test_unescaped_marker_in_html_is_flagged():
    assert xss_reflection(_xss(f"<p>Results for {MARK}</p>"), CFG).fired
    assert not xss_reflection(_xss("<p>Results for &quot;&gt;&lt;sentri-xss-abc123&gt;</p>"), CFG).fired  # escaped
    assert not xss_reflection(_xss(json.dumps({"q": MARK}), "application/json"), CFG).fired  # JSON is not a page
    assert not xss_reflection(_xss('<input value="&quot;><sentri-xss-abc123>">'), CFG).fired  # quote escaped


def test_csrf_fields_are_stripped_from_every_body_kind():
    body, name = strip_csrf(b"theme=dark&csrf_token=abc&x=1", "application/x-www-form-urlencoded")
    assert name == "csrf_token" and body == b"theme=dark&x=1"
    body, name = strip_csrf(b'{"title": "t", "_csrf": "abc"}', "application/json")
    assert name == "_csrf" and json.loads(body) == {"title": "t"}
    multipart = (
        b'--XyZ\r\nContent-Disposition: form-data; name="authenticity_token"\r\n\r\nabc\r\n'
        b'--XyZ\r\nContent-Disposition: form-data; name="title"\r\n\r\nhello\r\n--XyZ--\r\n'
    )
    body, name = strip_csrf(multipart, "multipart/form-data; boundary=XyZ")
    assert name == "authenticity_token" and b"abc" not in body and b'name="title"' in body
    assert strip_csrf(b"theme=dark", "application/x-www-form-urlencoded") is None


def _csrf(**kw) -> AgentEvidence:
    data = dict(
        identity="a",
        method="POST",
        path="/settings",
        token="csrf_token",
        with_status=302,
        with_location="/settings",
        with_text="",
        without_status=302,
        without_location="/settings",
        without_text="",
    )
    data.update(kw)
    return AgentEvidence(base=Evidence(), csrf_probes=(CsrfProbe(**data),), login_path="/login")


def test_csrf_missing_needs_the_same_success_without_the_token():
    assert csrf_missing(_csrf(), CFG).fired
    assert not csrf_missing(_csrf(without_status=403, without_location=None), CFG).fired  # rejected
    assert not csrf_missing(_csrf(without_location="/login?next=/settings"), CFG).fired  # bounced to login
    page = "<h1>Settings</h1><p>Saved your theme</p>"
    assert csrf_missing(_csrf(with_status=200, without_status=200, with_text=page, without_text=page), CFG).fired
    error = "<h1>Settings</h1><p>Form expired, please reload and try again</p><form>...</form>"
    assert not csrf_missing(_csrf(with_status=200, without_status=200, with_text=page, without_text=error), CFG).fired


def test_reflection_probes_cover_get_forms_and_query_strings():
    class S:
        base_url = "http://x"
        current = "anon"
        xss_marker = MARK

    app_map = AppMap(
        base_url="http://x",
        pages=[
            PageEntry(path="/items?page=2&sort=new"),
            PageEntry(
                path="/",
                forms=[
                    {"method": "get", "action": "/search", "fields": [{"name": "q", "type": "search"}]},
                    {"method": "post", "action": "/login", "fields": [{"name": "email", "type": "email"}]},
                ],
            ),
        ],
    )
    explorer = Explorer(S(), BudgetedProvider(NullProvider(), 1, 1), Strategy(), app_map, ExploreBudget(), print)  # type: ignore[arg-type]
    paths = [d.action.path for d in explorer._reflection_probes()]
    assert paths == [
        "/items?page=%22%3E%3Csentri-xss-abc123%3E&sort=%22%3E%3Csentri-xss-abc123%3E",
        "/search?q=%22%3E%3Csentri-xss-abc123%3E",
    ]


async def test_code_probes_run_before_the_model_and_are_replayable():
    from tests.unit.test_agent_explorer import STRATEGY, FakeSession, ScriptedModel, d

    class WithMarker(FakeSession):
        xss_marker = MARK

    session = WithMarker(leak_on=99)
    steps: list[dict] = []
    app_map = AppMap(base_url="http://127.0.0.1", pages=[PageEntry(path="/find?q=x")])
    explorer = Explorer(
        session,
        BudgetedProvider(ScriptedModel([d("finish")]), 1, 5),
        STRATEGY,
        app_map,
        ExploreBudget(max_steps=5),
        steps.append,
    )
    result = await explorer.run()
    assert [a.kind for a in session.executed] == ["navigate", "finish"]
    assert steps[0]["hypothesis_id"] == "reflected_input" and steps[0]["thought"].startswith("code probe")
    assert result.fallbacks == 0 and result.stopped.startswith("model finished")
