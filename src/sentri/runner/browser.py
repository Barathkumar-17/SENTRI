"""Playwright lifecycle. Chromium only: slow-network throttling needs CDP."""

from __future__ import annotations

from typing import Any

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright


class BrowserManager:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self._pw: Playwright | None = None
        self._browser: Browser | None = None

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)

    async def new_context(self, **kwargs: Any) -> BrowserContext:
        assert self._browser is not None, "BrowserManager.start() not called"
        return await self._browser.new_context(viewport={"width": 1280, "height": 800}, **kwargs)

    async def stop(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    async def __aenter__(self) -> BrowserManager:
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.stop()
