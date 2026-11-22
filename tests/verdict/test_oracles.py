from __future__ import annotations

from sentri.evidence.models import ConsoleRecord, Evidence, HttpRecord, LogLine
from sentri.verdict import oracles
from sentri.verdict.oracles import OracleConfig, evaluate_all


def http(url="http://localhost:5055/api/x", method="POST", status=200, same=True, started=0.0, body="b", **kw):
    return HttpRecord(url, method, status, 10.0, started, body, same, **kw)


def cfg(attack="empty_input", **kw):
    return OracleConfig(flow_id="f", attack_id=attack, ignore_console=(r"favicon\.ico",), **kw)


TRACE = [
    "[2026] ERROR in app: Exception on /api/signup [POST]",
    "Traceback (most recent call last):",
    '  File "/app/app.py", line 93, in api_signup',
    '    domain = email.split("@")[1]',
    "             ~~~~~~~~~~~~~~~~^^^",
    "IndexError: list index out of range",
    "",
]


def logs(lines):
    return tuple(LogLine("t", "stderr", line) for line in lines)


def test_http_500_same_origin_only():
    assert oracles.http_500(Evidence(http=(http(status=503),)), cfg()).fired
    assert not oracles.http_500(Evidence(http=(http(status=500, same=False),)), cfg()).fired
    assert not oracles.http_500(Evidence(http=(http(status=404),)), cfg()).fired
    r = oracles.http_500(Evidence(http=(http(url="http://localhost:5055/tasks/123/edit", status=500),)), cfg())
    assert r.route == "POST /tasks/:id/edit" and r.error_key == "500"


def test_exception_extracts_exception_line():
    r = oracles.exception(Evidence(response_errors=logs(TRACE)), cfg())
    assert r.fired
    assert r.error_key == "indexerror: list index out of range"
    assert not oracles.exception(Evidence(response_errors=logs(['127.0.0.1 "POST /x" 500 -'])), cfg()).fired


def test_exception_extra_patterns():
    ev = Evidence(response_errors=logs(["boom: custom failure"]))
    assert not oracles.exception(ev, cfg()).fired
    assert oracles.exception(ev, cfg(extra_log_patterns=(r"^boom:",))).fired


def test_console_error_rules():
    fav = ConsoleRecord("console_error", "Failed to load resource", "http://localhost:5055/favicon.ico")
    four = ConsoleRecord("console_error", "Failed to load resource: the server responded with a status of 401 ()")
    five = ConsoleRecord("console_error", "Failed to load resource: the server responded with a status of 500 ()")
    page = ConsoleRecord("page_error", "AbortError: signal is aborted without reason")
    assert not oracles.console_error(Evidence(console=(fav, four)), cfg()).fired
    assert oracles.console_error(Evidence(console=(five,)), cfg()).fired
    assert oracles.console_error(Evidence(console=(page,)), cfg()).fired


def test_duplicates_only_for_duplicate_attacks():
    ev = Evidence(list_count_before=1, list_count_after=3, duplicate_method="list_selector")
    assert not oracles.duplicates(ev, cfg("empty_input")).fired
    assert oracles.duplicates(ev, cfg("double_submit", expected_creates=1)).fired
    ok = Evidence(list_count_before=1, list_count_after=2, duplicate_method="list_selector")
    assert not oracles.duplicates(ok, cfg("double_submit", expected_creates=1)).fired


def test_duplicates_post_heuristic():
    two = (http(started=0), http(started=80))
    assert oracles.duplicates(Evidence(http=two), cfg("double_submit", expected_creates=1)).fired
    far = (http(started=0), http(started=1500))
    assert not oracles.duplicates(Evidence(http=far), cfg("double_submit", expected_creates=1)).fired
    diff_body = (http(started=0, body="a"), http(started=80, body="b"))
    assert not oracles.duplicates(Evidence(http=diff_body), cfg("double_submit", expected_creates=1)).fired


def test_duplicates_rapid_repeat_needs_text_confirmation():
    posts = tuple(http(started=i * 100) for i in range(10))
    assert not oracles.duplicates(Evidence(http=posts), cfg("rapid_repeat", expected_creates=1)).fired
    ev = Evidence(http=posts, page_text_rows=("Item X", "Item X"), unique_values=("Item X",))
    assert oracles.duplicates(ev, cfg("rapid_repeat", expected_creates=1)).fired


def test_duplicates_text_heuristic():
    ev = Evidence(page_text_rows=("a@x.test", "b", "a@x.test"), unique_values=("a@x.test",))
    assert oracles.duplicates(ev, cfg("double_submit", expected_creates=1)).fired


def test_timeout_rules():
    assert oracles.timeout(Evidence(timed_out=True, timeout_detail="no response within 10s: POST /x"), cfg()).fired
    assert oracles.timeout(Evidence(page_crashed=True), cfg()).error_key == "crash"
    failed = http(status=None, failed=True, failure="net::ERR_TIMED_OUT")
    assert oracles.timeout(Evidence(http=(failed,)), cfg()).fired
    aborted = http(status=None, failed=True, failure="net::ERR_ABORTED")
    assert not oracles.timeout(Evidence(http=(aborted,)), cfg()).fired


def test_evaluate_all_is_deterministic():
    ev = Evidence(http=(http(status=500),), response_errors=logs(TRACE))
    assert evaluate_all(ev, cfg()) == evaluate_all(ev, cfg())
    assert [r.oracle_id for r in evaluate_all(ev, cfg())] == list(oracles.ORACLE_IDS)


def test_evidence_round_trip():
    ev = Evidence(
        http=(http(),), console=(ConsoleRecord("page_error", "x"),), response_errors=logs(["a"]), timed_out=True
    )
    assert Evidence.from_dict(ev.to_dict()) == ev


def test_duplicates_needs_a_create_hint():
    """Two identical profile saves are an idempotent update, not a duplicate record."""
    two = (http(started=0), http(started=80))
    r = oracles.duplicates(Evidence(http=two), cfg("double_submit"))
    assert not r.fired and "not checked" in r.detail


def test_console_ignores_third_party_resource_failures_only():
    app = OracleConfig(flow_id="f", attack_id="a", app_origin="http://127.0.0.1:8765")
    cdn = ConsoleRecord(
        "console_error",
        "Access to script at 'https://www.gstatic.com/flutter-canvaskit/x/canvaskit.js'"
        " from origin 'http://127.0.0.1:8765' has been blocked by CORS policy",
    )
    font = ConsoleRecord(
        "console_error",
        "Access to fetch at 'https://fonts.gstatic.com/s/roboto.woff2' from origin "
        "'http://localhost:8765' has been blocked by CORS policy",
    )
    own = ConsoleRecord(
        "console_error",
        "Access to fetch at 'http://127.0.0.1:8765/api/x' from origin "
        "'http://127.0.0.1:8765' has been blocked by CORS policy",
    )
    script = ConsoleRecord("page_error", "TypeError: cannot read properties of undefined")
    assert not oracles.console_error(Evidence(console=(cdn, font)), app).fired
    assert oracles.console_error(Evidence(console=(own,)), app).fired  # the app's own resource still counts
    assert oracles.console_error(Evidence(console=(script,)), app).fired  # script errors always count
    assert oracles.console_error(Evidence(console=(cdn,)), cfg()).fired  # without an origin nothing is filtered


def test_console_errors_from_the_origin_fence_are_not_app_bugs():
    from sentri.evidence.models import ConsoleRecord, Evidence
    from sentri.verdict.oracles import OracleConfig, console_error

    cfg = OracleConfig(flow_id="f", attack_id="a")
    fenced = Evidence(console=(ConsoleRecord("console_error", "Failed to load resource: net::ERR_BLOCKED_BY_CLIENT"),))
    assert not console_error(fenced, cfg).fired
    real = Evidence(console=(ConsoleRecord("console_error", "TypeError: x is undefined"),))
    assert console_error(real, cfg).fired


def test_browser_local_network_policy_errors_are_not_app_bugs():
    from sentri.evidence.models import ConsoleRecord, Evidence
    from sentri.verdict.oracles import OracleConfig, console_error

    text = (
        "WebSocket connection to 'ws://127.0.0.1:3123/socket.io/' failed: Error in connection establishment: "
        "net::ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS"
    )
    assert not console_error(Evidence(console=(ConsoleRecord("console_error", text),)), OracleConfig("f", "a")).fired


def test_node_traces_are_keyed_by_the_error_line_above_the_frames():
    from sentri.evidence.models import Evidence, LogLine
    from sentri.verdict.oracles import OracleConfig, exception

    lines = [
        'Error: Unrecognized target URL for redirect: "><x>',
        "    at /app/build/routes/redirect.js:54:18",
        "    at Layer.handle [as handle_request] (/app/node_modules/express/lib/router/layer.js:95:5)",
        "info: Solved 1-star errorHandlingChallenge (Error Handling)",
    ]
    ev = Evidence(response_errors=tuple(LogLine("2026-01-01T00:00:00Z", "stdout", t) for t in lines))
    r = exception(ev, OracleConfig("f", "a"))
    assert r.fired and r.error_key is not None and r.error_key.startswith("error: unrecognized target url")
