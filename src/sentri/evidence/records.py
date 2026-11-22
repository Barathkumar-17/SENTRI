"""Count distinct records that hold a value, on an HTML list page or in a JSON response. Plain code.

`text.count(value)` is not a record count: one record can print its title twice (heading and tooltip), a flash
message can echo it, and an attribute can repeat it. Here a record is a list-like element (the app map's list
selector, or li / tr / article / option, or an element whose data-testid or class says row, item, card or entry)
and only the innermost record containing the value counts. In JSON, a record is the innermost object holding the
value in one of its own string fields.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

RECORD_TAGS = frozenset({"li", "tr", "article", "option"})
_RECORD_HINT = re.compile(r"row|item|card|entry|record", re.I)
_TESTID_SELECTOR = re.compile(r"""^\[data-testid=["']?([^"'\]]+)["']?\]$""")
_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    parent: _Node | None = None
    children: list[_Node] = field(default_factory=list)
    text: list[str] = field(default_factory=list)

    def full_text(self) -> str:
        return " ".join([*self.text, *(c.full_text() for c in self.children)])


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {})
        self.cur = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {k: v or "" for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_endtag(self, tag: str) -> None:
        node: _Node | None = self.cur
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.cur = node.parent

    def handle_data(self, data: str) -> None:
        self.cur.text.append(data)


def _is_record(node: _Node, testids: set[str]) -> bool:
    testid = node.attrs.get("data-testid", "")
    if testids:
        return testid in testids
    return (
        node.tag in RECORD_TAGS
        or bool(testid and _RECORD_HINT.search(testid))
        or bool(_RECORD_HINT.search(node.attrs.get("class", "")))
    )


def _count_html(body: str, token: str, selectors: list[str]) -> int | None:
    tree = _Tree()
    try:
        tree.feed(body)
        tree.close()
    except Exception:  # noqa: BLE001 - malformed markup: no structure to count
        return None
    testids = {m.group(1) for s in selectors if (m := _TESTID_SELECTOR.match(s.strip()))}

    def walk(node: _Node) -> int:
        inner = sum(walk(c) for c in node.children)
        if inner:
            return inner
        return 1 if _is_record(node, testids) and token in node.full_text() else 0

    found = walk(tree.root)
    if found == 0 and token in tree.root.full_text():
        return None  # the value is on the page but not inside anything that looks like a record
    return found


def _count_json(data: object, token: str) -> int:
    if isinstance(data, list):
        return sum(_count_json(item, token) for item in data)
    if isinstance(data, dict):
        inner = sum(_count_json(v, token) for v in data.values() if isinstance(v, (dict, list)))
        if inner:
            return inner
        return 1 if any(isinstance(v, str) and token in v for v in data.values()) else 0
    return 0


def count_records(
    body: str, token: str, content_type: str | None = None, selectors: list[str] | None = None
) -> int | None:
    """Distinct records holding `token`; None when the page has no structure to count it in (inconclusive)."""
    if not body or token not in body:
        return 0
    if "json" in (content_type or "") or body.lstrip()[:1] in ("[", "{"):
        try:
            return _count_json(json.loads(body), token)
        except ValueError:
            pass
    return _count_html(body, token, selectors or [])
