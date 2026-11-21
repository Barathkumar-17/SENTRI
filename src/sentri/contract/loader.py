"""Load, validate, hash and approve contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from sentri.contract.models import Contract


class ContractError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


def _format_loc(loc: tuple[Any, ...]) -> str:
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += ("." if out else "") + str(part)
    return out or "(root)"


def parse_contract(data: Any) -> Contract:
    if not isinstance(data, dict):
        raise ContractError(["(root): contract must be a mapping"])
    try:
        return Contract.model_validate(data)
    except ValidationError as exc:
        errors = []
        for err in exc.errors():
            loc = tuple(p for p in err["loc"] if p not in ("function-after[_check()]",))
            msg = err["msg"].removeprefix("Value error, ")
            errors.append(f"{_format_loc(loc)}: {msg}")
        raise ContractError(errors) from None


def load_contract(path: str | Path) -> Contract:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ContractError([f"{path}: file not found"]) from None
    except yaml.YAMLError as exc:
        raise ContractError([f"{path}: invalid YAML: {exc}"]) from None
    return parse_contract(data)


def contract_hash(contract: Contract) -> str:
    """Hash of everything that affects verdicts; approval metadata is excluded."""
    data = contract.model_dump(mode="json", exclude={"meta"})
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def contract_to_yaml(contract: Contract) -> str:
    data = contract.model_dump(mode="json", exclude_none=True)
    data["flows"] = []
    for flow in contract.flows:
        f = flow.model_dump(mode="json", exclude_none=True, exclude={"steps"})
        f["steps"] = [s.to_yaml() for s in flow.steps]
        data["flows"].append(f)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def approve_contract(src: str | Path, approved_by: str, out: str | Path) -> Contract:
    raw = yaml.safe_load(Path(src).read_text(encoding="utf-8"))
    contract = parse_contract(raw)
    contract.meta.approved_by = approved_by
    contract.meta.approved_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw["meta"] = contract.meta.model_dump()
    Path(out).write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return contract
