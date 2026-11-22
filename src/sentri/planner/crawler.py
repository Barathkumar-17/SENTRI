"""Same-origin crawl that extracts forms, inputs and buttons with code-built selectors."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from sentri.runner.extract import EXTRACT_JS

log = logging.getLogger(__name__)


@dataclass
class PageInfo:
    url: str
    path: str
    title: str
    forms: list[dict[str, Any]] = field(default_factory=list)
    loose_fields: list[dict[str, Any]] = field(default_factory=list)
    loose_buttons: list[dict[str, Any]] = field(default_factory=list)
    list_testids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "forms": self.forms,
            "loose_fields": self.loose_fields,
            "loose_buttons": self.loose_buttons,
            "list_item_selectors": [f'[data-testid="{t}"]' for t in self.list_testids],
        }


async def crawl(page: Page, base_url: str, max_pages: int = 20, max_depth: int = 2) -> list[PageInfo]:
    origin = urlsplit(base_url)
    seen: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(base_url.rstrip("/") + "/", 0)])
    pages: list[PageInfo] = []
    while queue and len(pages) < max_pages:
        url, depth = queue.popleft()
        parts = urlsplit(url)
        key = parts.path or "/"
        if key in seen or (parts.scheme, parts.netloc) != (origin.scheme, origin.netloc):
            continue
        seen.add(key)
        if any(word in key.lower() for word in ("logout", "signout", "delete")):
            continue  # never follow destructive links while crawling
        try:
            await page.goto(url, wait_until="load", timeout=15000)
            data = await page.evaluate(EXTRACT_JS)
        except PlaywrightError as exc:
            log.info("crawl: skip %s (%s)", url, exc.message.splitlines()[0])
            continue
        pages.append(
            PageInfo(
                url=url,
                path=key,
                title=data["title"],
                forms=data["forms"],
                loose_fields=data["loose_fields"],
                loose_buttons=data["loose_buttons"],
                list_testids=data["list_testids"],
            )
        )
        if depth < max_depth:
            for href in data["links"]:
                if href and not href.startswith(("#", "javascript:", "mailto:")):
                    queue.append((urljoin(url, href).split("#")[0], depth + 1))
    return pages
