"""Playwright lifecycle. Chromium only: slow-network throttling needs CDP.

Every context gets the origin fence (runner/fence.py): requests that would leave the app's origin are blocked or
stubbed, for every context, whatever triggered them."""

from __future__ import annotations

from typing import Any

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from sentri.runner.fence import OffsiteAssets, install


class BrowserManager:
    def __init__(self, headless: bool = True, fence_origin: str | None = None, offsite_assets: OffsiteAssets = "allow"):
        self.headless = headless
        self.fence_origin = fence_origin  # default app origin for contexts that do not name one
        self.offsite_assets: OffsiteAssets = offsite_assets
        self._pw: Playwright | None = None
        self._browser: Browser | None = None

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)

    async def new_context(self, fence_origin: str | None = None, **kwargs: Any) -> BrowserContext:
        assert self._browser is not None, "BrowserManager.start() not called"
        ctx = await self._browser.new_context(viewport={"width": 1280, "height": 800}, **kwargs)
        await install(ctx, fence_origin or self.fence_origin, self.offsite_assets)
        return ctx

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
