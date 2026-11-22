from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from sentri.contract import ContractError, contract_hash, load_contract, parse_contract
from sentri.contract.loader import approve_contract
from sentri.contract.placeholders import DataGenerator, tiny_png
from tests.conftest import BUGGY_APP


def test_minimal_contract_defaults(minimal_contract):
    c = parse_contract(minimal_contract)
    assert c.app.url == "http://localhost:5055"
    assert c.attacks_enabled == [
        "empty_input",
        "double_submit",
        "expired_session",
        "slow_network",
        "bad_file",
        "rapid_repeat",
    ]
    assert c.defaults.action_timeout_s == 10
    assert not c.approved
    assert c.flows[0].submit_index() == 1


def test_example_contracts_validate():
    for name in ("contract.yaml", "contract.docker.yaml"):
        c = load_contract(BUGGY_APP / name)
        assert not c.approved  # shipped unapproved: approval is a human step
        assert not c.fixtures  # no seeded credentials
        assert c.flow("create_task").expected_creates == 1  # default when list_selector is set


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d.pop("version"), "version"),
        (lambda d: d["app"].update(docker={"context": "."}), "at most one of app.docker or app.run"),
        (lambda d: [d["app"].pop("run"), d["app"].pop("port")], "app.base_url for an app that is already running"),
        (lambda d: d["app"].update(port=70000), "port"),
        (lambda d: d["flows"][0].update(id="Bad-Id"), "flow id"),
        (lambda d: d["flows"][0]["steps"].pop(), "submit step"),
        (lambda d: d["flows"][0].update(success={}), "success needs"),
        (lambda d: d["flows"][0]["steps"].append({"teleport": "x"}), "unknown step"),
        (lambda d: d["flows"][0]["steps"].insert(0, {"fill": {"selector": "x"}}), "value is required"),
        (lambda d: d["flows"][0].update(requires_login=True), "need auth"),
        (lambda d: d["flows"][0]["steps"][0]["fill"].update(value="{nope}"), "unknown placeholders"),
        (lambda d: d.update(attacks_enabled=["teleport"]), "attacks_enabled"),
        (lambda d: d["flows"].append(copy.deepcopy(d["flows"][0])), "duplicate flow ids"),
        (lambda d: d["app"].update(ignore_console=["("]), "invalid regex"),
        (lambda d: d["app"].update(reset="command"), "reset_command"),
    ],
)
def test_validation_errors(minimal_contract, mutate, message):
    mutate(minimal_contract)
    with pytest.raises(ContractError) as exc:
        parse_contract(minimal_contract)
    assert any(message in e for e in exc.value.errors), exc.value.errors


def test_error_paths_point_at_yaml_location(minimal_contract):
    minimal_contract["flows"][0]["steps"][0] = {"fill": {"selector": "x"}}
    with pytest.raises(ContractError) as exc:
        parse_contract(minimal_contract)
    assert exc.value.errors[0].startswith("flows[0].steps[0]")


def test_contract_hash_ignores_approval(minimal_contract):
    a = parse_contract(minimal_contract)
    minimal_contract["meta"] = {"approved_by": "x", "approved_at": "y"}
    b = parse_contract(minimal_contract)
    assert contract_hash(a) == contract_hash(b)


def test_approve_stamps_meta(tmp_path: Path, minimal_contract):
    src = tmp_path / "draft.yaml"
    src.write_text(yaml.safe_dump(minimal_contract))
    out = tmp_path / "contract.yaml"
    approve_contract(src, "alice", out)
    assert load_contract(out).approved


def test_generator_is_deterministic_and_unique(tmp_path: Path):
    a = DataGenerator(42, {}, tmp_path, "r1-1")
    b = DataGenerator(42, {}, tmp_path, "r1-1")
    c = DataGenerator(43, {}, tmp_path, "r1-2")
    assert a.resolve("{email}") == b.resolve("{email}")
    assert a.resolve("{email}") != c.resolve("{email}")
    assert a.resolve("{email}") == a.resolve("{email}")  # stable within one attempt
    assert DataGenerator(1, {"password": "pinned"}, tmp_path, "t").resolve("{password}") == "pinned"
    path = Path(a.resolve("{file}"))
    assert path.read_bytes() == tiny_png()
    assert tiny_png().startswith(b"\x89PNG")


def test_api_auth_and_api_only_contracts(minimal_contract):
    api_only = {
        "version": 1,
        "app": {"run": "x", "port": 8000, "clean": ["budget.db"]},
        "auth": {
            "api": {
                "register": {"path": "/auth/register", "json": {"username": "{username}", "password": "{password}"}}
            }
        },
    }
    c = parse_contract(api_only)
    assert c.flows == [] and c.auth.api.token_field == "token" and c.app.clean == ["budget.db"]
    bad = {**api_only, "auth": {"api": {"register": {"path": "/r", "json": {"u": "{nope}"}}}}}
    with pytest.raises(ContractError, match="unknown placeholders"):
        parse_contract(bad)
    with pytest.raises(ContractError, match="register and/or login"):
        parse_contract({**api_only, "auth": {"api": {}}})
    with pytest.raises(ContractError, match="exactly one"):
        parse_contract({**api_only, "auth": {"api": api_only["auth"]["api"], "login_flow": "x"}})


def test_username_placeholder_fits_strict_rules(tmp_path: Path):
    import re

    gen = DataGenerator(7, {}, tmp_path, "r12-3")
    name = gen.resolve("{username}")
    assert re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", name)
    assert gen.resolve_obj({"u": "{username}", "n": [1, "{username}"]}) == {"u": name, "n": [1, name]}


def test_clean_files_stay_inside_the_app(tmp_path: Path):
    from sentri.contract.models import AppConfig
    from sentri.sandbox import CommandSandbox
    from sentri.sandbox.base import SandboxError

    (tmp_path / "budget.db").write_text("x")
    (tmp_path / "budget.db-wal").write_text("x")
    CommandSandbox(AppConfig.model_validate({"run": "x", "port": 1, "clean": ["budget.db"]}), tmp_path)._clean()
    assert not (tmp_path / "budget.db").exists() and not (tmp_path / "budget.db-wal").exists()
    escape = CommandSandbox(AppConfig.model_validate({"run": "x", "port": 1, "clean": ["../outside.db"]}), tmp_path)
    with pytest.raises(SandboxError):
        escape._clean()


def test_an_agent_only_contract_may_have_just_its_login_flow(minimal_contract):
    """Found while setting up the live flow-shop validation: a login helper alone was rejected, so an agent-only
    contract with a browser login was impossible (API-auth contracts could already have zero flows)."""
    from sentri.contract import ContractError

    minimal_contract["flows"][0].update(id="login", attack=False)
    minimal_contract["auth"] = {"login_flow": "login"}
    contract = parse_contract(minimal_contract)
    assert contract.attacked_flows() == []
    del minimal_contract["auth"]
    with pytest.raises(ContractError, match="attack: true"):
        parse_contract(minimal_contract)  # helper flows with no login role are still a mistake
