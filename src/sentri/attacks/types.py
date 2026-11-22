"""The six fixed attack types (docs/reference/attacks.md). Algorithms are fixed; only payloads vary."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sentri.attacks.base import APPLIES, Applicability, Attack, AttackContext, Payload, PayloadSlot
from sentri.contract.models import Flow
from sentri.contract.placeholders import tiny_png
from sentri.runner.fence import set_throttled

SLOW_3G = {"offline": False, "latency": 2000, "downloadThroughput": 50_000, "uploadThroughput": 50_000}
NO_THROTTLE = {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1}


class EmptyInput(Attack):
    id = "empty_input"
    description = "Submit with blank or whitespace-only fields."

    def applies_to(self, flow: Flow) -> Applicability:
        if any(s.kind in ("fill", "select") for s in flow.steps[: flow.submit_index() or 0]):
            return APPLIES
        return Applicability(False, "no input fields")

    def payload_slots(self, flow: Flow) -> list[PayloadSlot]:
        return [
            PayloadSlot(f"field_{i}", "blank", f"value for {step.selector} (blank-like or one character)")
            for i, step in enumerate(flow.steps)
            if step.kind == "fill" and i in flow.prefix_indexes()
        ]

    def validate(self, slot: PayloadSlot, value: Any) -> bool:
        return isinstance(value, str) and len(value) <= 64 and (value.strip() == "" or len(value) == 1)

    def finalize(self, flow: Flow, payload: Payload) -> Payload:
        if payload and not any(v.strip() == "" for v in payload.values()):
            first = next(iter(payload))
            payload = {**payload, first: ""}
        return payload

    async def execute(self, ctx: AttackContext, payload: Payload) -> None:
        overrides: dict[int, Any] = {int(k.split("_")[1]): v for k, v in payload.items()}
        await ctx.executor.start()
        await ctx.executor.run_prefix(overrides)
        await ctx.executor.submit()


class DoubleSubmit(Attack):
    id = "double_submit"
    description = "Double-click the submit control within about 80 ms."

    async def execute(self, ctx: AttackContext, payload: Payload) -> None:
        await ctx.executor.start()
        await ctx.executor.run_prefix()
        gap = int(payload.get("gap_ms", "80"))  # the Fixer gate's held-out variant changes the timing
        await ctx.executor.js_click_burst([0, gap])


class ExpiredSession(Attack):
    id = "expired_session"
    description = "Remove cookies and storage mid-flow, then act."

    def applies_to(self, flow: Flow) -> Applicability:
        return APPLIES if flow.requires_login else Applicability(False, "flow has no session")

    def payload_slots(self, flow: Flow) -> list[PayloadSlot]:
        choices = ("before_submit", *(f"after_step:{i}" for i in flow.prefix_indexes()))
        return [PayloadSlot("point", "point", "when to expire the session", choices)]

    async def execute(self, ctx: AttackContext, payload: Payload) -> None:
        flow = ctx.executor.flow
        prefix = flow.prefix_indexes()
        point = payload.get("point", "before_submit")
        cut = len(prefix) if point == "before_submit" else int(point.split(":")[1]) + 1
        await ctx.executor.start()
        await ctx.executor.run_steps(prefix[:cut])
        ctx.emit("expire session: clear cookies and storage")
        await ctx.context.clear_cookies()
        await ctx.page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }")
        await ctx.executor.run_steps(prefix[cut:])
        await ctx.executor.submit()


class SlowNetwork(Attack):
    id = "slow_network"
    description = "Throttle to Slow 3G, then act (optionally reload)."

    def payload_slots(self, flow: Flow) -> list[PayloadSlot]:
        return [PayloadSlot("action", "action", "what to do while slow", ("submit", "submit_then_reload"))]

    async def execute(self, ctx: AttackContext, payload: Payload) -> None:
        cdp = await ctx.context.new_cdp_session(ctx.page)
        ctx.cdp = cdp
        await cdp.send("Network.enable")
        ctx.emit("throttle network to Slow 3G")
        await cdp.send("Network.emulateNetworkConditions", SLOW_3G)
        set_throttled(ctx.context, True)
        await ctx.executor.start()
        await ctx.executor.run_prefix()
        await ctx.executor.submit()
        if payload.get("action") == "submit_then_reload":
            await ctx.page.wait_for_timeout(500)
            await ctx.executor.reload(wait_until="commit")
        # Throttling stays on through settle; the runner removes it before counting duplicates.


async def remove_throttle(ctx: AttackContext) -> None:
    cdp, ctx.cdp = ctx.cdp, None
    set_throttled(ctx.context, False)
    if cdp is not None:
        try:
            await cdp.send("Network.emulateNetworkConditions", NO_THROTTLE)
            await cdp.detach()
        except Exception:  # noqa: BLE001 - page may be gone
            pass


class BadFile(Attack):
    id = "bad_file"
    description = "Upload a 0-byte, oversized, wrong-extension, corrupt or oddly named file."
    SHAPES = ("zero_byte", "oversized", "wrong_extension", "corrupt", "weird_name")

    def applies_to(self, flow: Flow) -> Applicability:
        return APPLIES if flow.has_kind("upload") else Applicability(False, "no upload")

    def payload_slots(self, flow: Flow) -> list[PayloadSlot]:
        return [
            PayloadSlot("shape", "shape", "file shape", self.SHAPES),
            PayloadSlot("filename", "filename", "file name to send (used by weird_name; <=255 chars)"),
        ]

    def build_file(self, shape: str, filename: str, ext: str, size_mb: int) -> dict[str, Any]:
        png = tiny_png()
        if shape == "zero_byte":
            return {"name": f"empty{ext}", "mimeType": "image/png", "buffer": b""}
        if shape == "oversized":
            return {"name": f"huge{ext}", "mimeType": "image/png", "buffer": png + b"\x00" * (size_mb * 1024 * 1024)}
        if shape == "wrong_extension":
            return {"name": "image.exe", "mimeType": "application/octet-stream", "buffer": png}
        if shape == "corrupt":
            return {"name": f"corrupt{ext}", "mimeType": "image/png", "buffer": os.urandom(2048)}
        return {"name": filename or f"weird name{ext}", "mimeType": "image/png", "buffer": png}

    async def execute(self, ctx: AttackContext, payload: Payload) -> None:
        flow = ctx.executor.flow
        upload_index = next(i for i, s in enumerate(flow.steps) if s.kind == "upload")
        original = Path(ctx.executor.gen.resolve(flow.steps[upload_index].file or ""))
        ext = original.suffix or ".png"
        file = self.build_file(payload.get("shape", "zero_byte"), payload.get("filename", ""), ext, ctx.bad_file_mb)
        ctx.emit(f"prepare bad file: {payload.get('shape')} ({len(file['buffer'])} bytes)")
        await ctx.executor.start()
        await ctx.executor.run_prefix({upload_index: file})
        await ctx.executor.submit()


class RapidRepeat(Attack):
    id = "rapid_repeat"
    description = "Repeat the same action about 10 times in one second."

    def payload_slots(self, flow: Flow) -> list[PayloadSlot]:
        return [PayloadSlot("action", "action", "what to repeat", ("submit", "reload_submit"))]

    async def execute(self, ctx: AttackContext, payload: Payload) -> None:
        await ctx.executor.start()
        await ctx.executor.run_prefix()
        if payload.get("action") == "reload_submit":
            await ctx.executor.submit()
            for _ in range(10):
                await ctx.page.wait_for_timeout(100)
                await ctx.executor.reload(wait_until="commit")
            return
        await ctx.executor.js_click_burst([i * 100 for i in range(10)])


ATTACKS: dict[str, Attack] = {
    a.id: a for a in (EmptyInput(), DoubleSubmit(), ExpiredSession(), SlowNetwork(), BadFile(), RapidRepeat())
}
