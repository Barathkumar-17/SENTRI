"""Nothing secret leaves Sentri: keys and provider account ids are scrubbed wherever text is stored or shown."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from sentri.dashboard import create_app
from sentri.feed.bus import EventBus
from sentri.ledger import Ledger
from sentri.llm.provider import LLMResult
from sentri.redact import REDACTED, RedactingFilter, redact, register_secret
from sentri.report import build_report
from sentri.verdict.scorer import compute

ORG = "org_01m81q084weh29v5f4ay6s6dp9"  # the shape Groq and OpenAI quote in rate-limit errors
PROVIDER_ERROR = (
    'rate limit beyond the wait cap: {"error":{"message":"Rate limit reached for model `x` in organization '
    f'`{ORG}` service tier `on_demand`"}}}}'
)


@pytest.mark.parametrize(
    "secret",
    [
        ORG,
        "proj_AbCdEfGhIjKlMnOp",
        "gsk_" + "a1B2c3D4e5F6g7H8i9J0",
        "sk-ant-api03-" + "x" * 30,
        "sk-or-v1-" + "y" * 30,
        "AIza" + "S" * 35,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.SflKxwRJSMeKKF2QT4fwpM",
        "ghp_" + "z" * 30,
    ],
)
def test_known_secret_shapes_are_scrubbed(secret):
    assert secret not in redact(f"error from provider: {secret} (retry later)")


def test_headers_bearer_tokens_and_query_keys_are_scrubbed():
    text = redact("Authorization: Bearer abc.def.ghi-123456\nGET /v1/models?api_key=hunter2hunter2&x=1")
    assert "abc.def" not in text and "hunter2" not in text and "x=1" in text


def test_ordinary_words_survive():
    text = "the organization and project pages; user b received user a's data"
    assert redact(text) == text


def test_a_key_typed_at_runtime_is_scrubbed_once_registered():
    key = "runtime-only-key-7f3a91"
    register_secret(key)
    assert redact(f"call failed for {key}") == f"call failed for {REDACTED}"


def test_provider_errors_are_scrubbed_where_they_are_made():
    assert ORG not in (LLMResult(ok=False, error=PROVIDER_ERROR).error or "")


def test_the_feed_and_the_ledger_store_no_account_ids(tmp_path):
    lg = Ledger(tmp_path / "l.db")
    run = lg.create_run("abc", "main", "", "ch")
    EventBus(lg, run).emit("llm_fallback", f"explorer step 1: {PROVIDER_ERROR}", PROVIDER_ERROR, detail=PROVIDER_ERROR)
    action = {"kind": "type", "selector": "#pw", "text": "Secret-pw-123"}
    lg.add_step(run, {"step": 1, "thought": PROVIDER_ERROR, "error": PROVIDER_ERROR, "action": action})
    lg.finish_run(run, "finished", None, 1, None, "llm", compute([], 0).__dict__, usage={"stop_reason": PROVIDER_ERROR})
    lg.close()
    raw = (tmp_path / "l.db").read_bytes()
    assert ORG.encode() not in raw
    assert b"Secret-pw-123" in raw  # replay steps stay exact: a replay must re-send what was typed


def test_old_ledgers_leak_nothing_through_the_api_the_evidence_or_the_report(tmp_path):
    lg = Ledger(tmp_path / "l.db")
    run = lg.create_run("abc", "main", "", "ch")
    # a row written before redaction existed, straight into the table
    lg.conn.execute(
        "INSERT INTO events (run_id, ts, kind, message, reason, data) VALUES (?,?,?,?,?,?)",
        (
            run,
            "2026-11-22T18:51:16Z",
            "llm_fallback",
            PROVIDER_ERROR,
            PROVIDER_ERROR,
            json.dumps({"e": PROVIDER_ERROR}),
        ),
    )
    lg.conn.commit()
    lg.finish_run(run, "finished", None, 1, None, "llm", compute([], 0).__dict__)
    lg.conn.execute("UPDATE runs SET usage=? WHERE id=?", (json.dumps({"stop_reason": PROVIDER_ERROR}), run))
    lg.conn.commit()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "old.json").write_text(json.dumps({"error": PROVIDER_ERROR}), encoding="utf-8")
    report = build_report(lg, run, None)
    lg.close()

    client = TestClient(create_app(tmp_path / "l.db", evidence, tmp_path / "live.jpg"), base_url="http://127.0.0.1")
    for url in (f"/api/events?run_id={run}", "/api/runs", f"/api/runs/{run}", "/evidence/old.json"):
        body = client.get(url).text
        assert ORG not in body, url
    assert REDACTED in client.get(f"/api/events?run_id={run}").text
    assert ORG not in report


def test_log_lines_are_scrubbed():
    record = logging.LogRecord("sentri", logging.WARNING, __file__, 1, "provider said %s", (PROVIDER_ERROR,), None)
    RedactingFilter().filter(record)
    assert ORG not in record.getMessage()
