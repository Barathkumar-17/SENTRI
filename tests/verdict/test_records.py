"""Duplicates are counted as distinct records, never as substring occurrences."""

from __future__ import annotations

import json

from sentri.evidence.agent_models import AgentEvidence, CountProbe
from sentri.evidence.models import Evidence
from sentri.evidence.records import count_records
from sentri.verdict.agent_oracles import replay_duplicate
from sentri.verdict.oracles import OracleConfig

T = "sentri-r1-3-ab12cd"


def test_one_record_printing_its_value_twice_is_one_record():
    page = (
        f'<p class="flash">Saved "{T}"</p><ul>'
        f'<li data-testid="task-row"><b>{T}</b> <span title="{T}">{T}</span></li>'
        '<li data-testid="task-row">other</li></ul>'
    )
    assert page.count(T) == 4
    assert count_records(page, T, "text/html", ['[data-testid="task-row"]']) == 1
    assert count_records(page, T, "text/html") == 1  # generic rows (li) without a list selector


def test_real_duplicates_are_counted():
    rows = "".join(f'<tr class="item-row"><td>{T}</td></tr>' for _ in range(3))
    assert count_records(f"<table>{rows}</table>", T, "text/html") == 3


def test_json_lists_count_innermost_objects():
    body = json.dumps({"data": [{"id": 1, "title": T, "meta": {"by": "a"}}, {"id": 2, "title": T}, {"id": 3}]})
    assert count_records(body, T, "application/json") == 2


def test_no_structure_means_unknown_not_zero():
    assert count_records(f"<p>Hello {T}</p>", T, "text/html") is None  # on the page, but in no record
    assert count_records("<ul><li>x</li></ul>", T, "text/html") == 0  # simply not there


def test_inconclusive_probes_never_fire():
    probe = CountProbe(T, "/tasks", 0, 5, 1, inconclusive=True)
    assert not replay_duplicate(AgentEvidence(base=Evidence(), count_probes=(probe,)), OracleConfig("a", "b")).fired
    real = CountProbe(T, "/tasks", 0, 2, 1)
    r = replay_duplicate(AgentEvidence(base=Evidence(), count_probes=(real,)), OracleConfig("a", "b"))
    assert r.fired and "2 new records on /tasks" in r.detail
