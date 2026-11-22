"""Origin fence for every browser context Sentri opens.

Nothing the browser does may reach another origin with the app's credentials, whatever caused it (a model
action, a click on a link, a form whose action points elsewhere, a popup, a script, a redirect):

- off-origin navigations (any frame, popups included) are aborted;
- off-origin fetch/XHR/beacons get a local empty stub (204), so nothing leaves and the app sees a quiet failure
  instead of a network error;
- off-origin websockets are closed;
- off-origin static assets (scripts, styles, images, fonts, media) load by default, because many apps need
  their CDN; they never carry identity tokens. `app.offsite_assets: block` blocks them too;
- identity tokens (contract `auth.api`) are attached per request, to same-origin requests only. Those requests
  are fetched with redirects disabled, so a redirect to another origin is stubbed before the token can follow.

Cookies are origin-scoped by the browser, so a cookie-only redirect hop to another origin carries none of the
app's credentials; the agent session notices when a page lands off-origin and returns to the app.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

from playwright.async_api import BrowserContext, Route, WebSocketRoute
from playwright.async_api import Error as PlaywrightError

STATIC_TYPES = frozenset({"script", "stylesheet", "image", "font", "media", "manifest", "texttrack"})
TOKEN_TYPES = frozenset({"document", "xhr", "fetch"})  # fetched by the fence so redirects can be checked
OffsiteAssets = Literal["allow", "block"]
BLOCKED_NOTE = "blocked by the Sentri origin fence"


def origin_key(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in ("localhost", "::1"):
        host = "127.0.0.1"
    port = parts.port or (443 if parts.scheme in ("https", "wss") else 80)
    scheme = {"ws": "http", "wss": "https"}.get(parts.scheme, parts.scheme)
    return scheme, f"{host}:{port}"


@dataclass
class Blocked:
    method: str
    url: str
    resource_type: str
    how: str  # aborted | stubbed | redirect_stubbed | ws_closed


@dataclass
class OriginFence:
    base_url: str | None
    offsite_assets: OffsiteAssets = "allow"
    headers: dict[str, str] = field(default_factory=dict)  # identity token headers, same-origin only
    blocked: list[Blocked] = field(default_factory=list)

    def same_origin(self, url: str) -> bool:
        if self.base_url is None:
            return True
        if url.startswith(("data:", "blob:", "about:")):
            return True
        return origin_key(url) == origin_key(self.base_url)

    def _cors(self) -> dict[str, str]:
        return {
            "access-control-allow-origin": self.base_url or "*",
            "access-control-allow-credentials": "true",
            "x-sentri-fence": "blocked",
        }

    async def handle(self, route: Route) -> None:
        req = route.request
        url = req.url
        try:
            if not self.same_origin(url):
                await self._offsite(route, req.method, url, req.resource_type)
                return
            if self.headers and req.resource_type in TOKEN_TYPES:
                await self._with_token(route, req.headers, url, req.resource_type)
                return
            if self.headers:
                await route.fallback(headers={**req.headers, **self.headers})
                return
            await route.fallback()
        except PlaywrightError:
            pass  # the page or context went away mid-request

    async def _offsite(self, route: Route, method: str, url: str, rtype: str) -> None:
        if rtype in STATIC_TYPES and method in ("GET", "HEAD") and self.offsite_assets == "allow":
            await route.fallback()  # no identity headers: those are only ever added to same-origin requests
            return
        if rtype in ("xhr", "fetch", "eventsource", "ping", "other"):
            self.blocked.append(Blocked(method, url, rtype, "stubbed"))
            await route.fulfill(status=204, headers=self._cors(), body="")
            return
        self.blocked.append(Blocked(method, url, rtype, "aborted"))
        await route.abort("blockedbyclient")

    async def _with_token(self, route: Route, headers: dict[str, str], url: str, rtype: str) -> None:
        resp = await route.fetch(headers={**headers, **self.headers}, max_redirects=0)
        location = resp.headers.get("location")
        if 300 <= resp.status < 400 and location and not self.same_origin(urljoin(url, location)):
            target = urljoin(url, location)
            self.blocked.append(Blocked(route.request.method, target, rtype, "redirect_stubbed"))
            if rtype == "document":
                body = f"<!doctype html><title>Blocked</title><p>{BLOCKED_NOTE}: redirect to another origin</p>"
                await route.fulfill(status=200, content_type="text/html", body=body)
            else:
                await route.fulfill(status=204, headers=self._cors(), body="")
            return
        await route.fulfill(response=resp)

    async def handle_ws(self, ws: WebSocketRoute) -> None:
        self.blocked.append(Blocked("GET", ws.url, "websocket", "ws_closed"))
        with contextlib.suppress(PlaywrightError):
            await ws.close(code=1008, reason=BLOCKED_NOTE)


_FENCES: dict[int, OriginFence] = {}


async def install(ctx: BrowserContext, base_url: str | None, offsite_assets: OffsiteAssets = "allow") -> OriginFence:
    fence = OriginFence(base_url.rstrip("/") if base_url else None, offsite_assets)
    await ctx.route("**/*", fence.handle)
    if base_url is not None:
        await ctx.route_web_socket(lambda url: not fence.same_origin(url), fence.handle_ws)
    key = id(ctx)
    _FENCES[key] = fence

    def forget(_ctx: BrowserContext) -> None:
        _FENCES.pop(key, None)

    ctx.on("close", forget)
    return fence


def fence_of(ctx: BrowserContext) -> OriginFence | None:
    return _FENCES.get(id(ctx))


async def set_identity_headers(ctx: BrowserContext, headers: dict[str, str]) -> None:
    """Attach an identity's token headers to its context: same-origin requests only, redirects checked."""
    fence = fence_of(ctx)
    if fence is None:  # a context Sentri did not create through BrowserManager
        fence = await install(ctx, None)
    fence.headers = dict(headers)


def blocked_summary(ctx: BrowserContext, limit: int = 10) -> list[dict[str, Any]]:
    fence = fence_of(ctx)
    if fence is None:
        return []
    return [b.__dict__ for b in fence.blocked[-limit:]]
