from __future__ import annotations

from sentri.agent.recon import AppMap, EndpointEntry, PageEntry, _body_fields

SCHEMAS = {
    "ExpenseIn": {
        "properties": {
            "amount": {"anyOf": [{"type": "number"}, {"type": "string"}], "maximum": 10000000000},
            "date": {"type": "string", "format": "date"},
            "category": {"type": "string", "minLength": 1, "maxLength": 60},
        }
    }
}


def test_body_fields_from_openapi():
    op = {"requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/ExpenseIn"}}}}}
    fields = _body_fields(op, SCHEMAS)
    assert fields["amount"] == "number/string (maximum=10000000000)"
    assert fields["date"] == "string (format=date)"
    assert fields["category"] == "string (minLength=1, maxLength=60)"
    assert _body_fields({}, SCHEMAS) == {}


def test_protected_paths_combine_pages_and_endpoints():
    m = AppMap(
        base_url="http://x",
        pages=[PageEntry(path="/tasks/7", requires_login=True), PageEntry(path="/")],
        endpoints=[
            EndpointEntry(method="GET", path="/expenses", requires_login=True),
            EndpointEntry(method="POST", path="/auth/login"),
        ],
    )
    assert m.protected_paths() == {"/tasks/:id", "/expenses"}
