"""A confirmed finding is titled by what the oracles measured, not by the model's hypothesis wording."""

from sentri.agent.replay import measured_title
from sentri.verdict.engine import CheckVerdict, OracleHit

HYPOTHESIS = "A new user can sign up, log in and complete a purchase"


def hit(oracle: str, detail: str) -> OracleHit:
    return OracleHit(oracle, 3, "sig", detail)


def test_http_500_title_names_the_failing_request():
    verdict = CheckVerdict("fail", 3, (hit("http_500", "POST /checkout/confirm -> 500"),))
    assert measured_title(HYPOTHESIS, verdict, "fail") == "HTTP 500 on POST /checkout/confirm"


def test_other_oracles_use_their_own_detail_and_many_hits_are_summarised():
    hits = tuple(hit("console_error", f"error {i}") for i in range(5))
    title = measured_title(HYPOTHESIS, CheckVerdict("flaky", 3, hits), "flaky")
    assert title == "console_error: error 0; console_error: error 1; console_error: error 2 (+2 more)"


def test_unconfirmed_finding_keeps_the_candidate_wording():
    assert measured_title(HYPOTHESIS, CheckVerdict("pass", 3), "unconfirmed") == HYPOTHESIS
