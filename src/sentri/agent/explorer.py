"""Agentic exploration: the model drives the browser one validated action at a time.

Each step: observe -> the model picks one action -> code runs it -> plain-code oracles check what happened ->
the step is logged with the model's reasoning. Paths of steps that look broken (an oracle fired, or the model
suspects something) become candidates with exact reproduction steps, confirmed later by replay.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from sentri.agent.actions import (
    ACTION_ADAPTER,
    Action,
    Burst,
    Click,
    Finish,
    Navigate,
    Replay,
    SendRequest,
    SwitchUser,
    describe,
)
from sentri.agent.recon import SKIP_WORDS, AppMap
from sentri.agent.session import AgentSession
from sentri.agent.strategy import Hypothesis, Strategy
from sentri.llm import BudgetedProvider, load_prompt
from sentri.llm.untrusted import build_user_prompt
from sentri.verdict.agent_oracles import evaluate_agent
from sentri.verdict.oracles import OracleConfig
from sentri.verdict.signature import normalize_path

Kind = Literal[
    "click",
    "type",
    "navigate",
    "upload",
    "request",
    "replay",
    "burst",
    "new_tab",
    "switch_tab",
    "back",
    "refresh",
    "clear_session",
    "throttle",
    "switch_user",
    "wait",
    "finish",
]


class ActionSpec(BaseModel):
    """Flat action shape for the model (works with every provider's structured output); code converts it."""

    kind: Kind
    selector: str | None = None
    text: str | None = None
    repeat: int | None = None
    submit: bool | None = None
    path: str | None = None
    shape: str | None = None
    filename: str | None = None
    size_mb: int | None = None
    method: str | None = None
    from_request: int | None = None
    body: str | None = None
    content_type: str | None = None
    request_id: int | None = None
    times: int | None = None
    concurrent: bool | None = None
    interval_ms: int | None = None
    index: int | None = None
    on: bool | None = None
    user: str | None = None
    ms: int | None = None
    reason: str | None = None


class Decision(BaseModel):
    thought: str = Field(default="", max_length=1500)
    hypothesis_id: str = Field(default="", max_length=60)
    action: ActionSpec
    suspect: bool = False
    suspect_reason: str = Field(default="", max_length=500)
    new_path: bool = False


def to_action(spec: ActionSpec) -> Action:
    """Keep only the fields the chosen kind accepts, then validate strictly (bounds, origin rules come later)."""
    data = {k: v for k, v in spec.model_dump().items() if v is not None}
    allowed = set(_KIND_CLASSES[spec.kind].model_fields)
    return ACTION_ADAPTER.validate_python({k: v for k, v in data.items() if k in allowed})


def _kind_classes() -> dict[str, type[BaseModel]]:
    from sentri.agent import actions as a

    classes: list[type[BaseModel]] = [
        a.Click,
        a.TypeText,
        a.Navigate,
        a.Upload,
        a.SendRequest,
        a.Replay,
        a.Burst,
        a.NewTab,
        a.SwitchTab,
        a.Back,
        a.Refresh,
        a.ClearSession,
        a.Throttle,
        a.SwitchUser,
        a.Wait,
        a.Finish,
    ]
    return {cls.model_fields["kind"].default: cls for cls in classes}


_KIND_CLASSES = _kind_classes()


@dataclass
class ReproStep:
    action: dict[str, Any]
    match: dict[str, str] | None = None  # for replay/request: which observed request, by method + path
    expect_ok: bool = True  # it ran cleanly during exploration, so it must run cleanly on replay

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "match": self.match, "expect_ok": self.expect_ok}


@dataclass
class Candidate:
    hypothesis_id: str
    title: str
    from_step: int
    to_step: int
    repro: list[ReproStep]
    signals: list[dict[str, Any]]
    suspect_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "title": self.title,
            "from_step": self.from_step,
            "to_step": self.to_step,
            "repro": [r.to_dict() for r in self.repro],
            "signals": self.signals,
        }


@dataclass
class ExploreBudget:
    max_steps: int = 60
    max_seconds: float = 600
    max_candidates: int = 8
    steps_per_hypothesis: int = 10  # base allotment per focus hypothesis
    max_steps_per_hypothesis: int = 20  # ceiling while it keeps making progress (mid-flow)


@dataclass
class ExploreResult:
    steps: int = 0
    candidates: list[Candidate] = field(default_factory=list)
    stopped: str = ""
    fallbacks: int = 0  # steps chosen by code because the model gave no usable answer
    invalid: int = 0  # steps lost to invalid actions
    stalls: int = 0  # times the same action repeated with no change forced a focus rotation


class Explorer:
    def __init__(
        self,
        session: AgentSession,
        provider: BudgetedProvider,
        strategy: Strategy,
        app_map: AppMap,
        budget: ExploreBudget,
        record_step: Callable[[dict[str, Any]], None],
        emit: Callable[..., Any] | None = None,
        oracle_cfg: OracleConfig | None = None,
    ):
        self.s = session
        self.provider = provider
        self.strategy = strategy
        self.app_map = app_map
        self.budget = budget
        self.record_step = record_step
        self.emit = emit or (lambda *a, **k: None)
        self.history: list[dict[str, Any]] = []
        self.cfg = oracle_cfg or OracleConfig(flow_id="agentic", attack_id="explore", app_origin=session.base_url)
        self.flagged: list[str] = []
        self._signal_keys: set[str] = set()
        self._suspect_reasons: set[str] = set()
        self._observation: dict[str, Any] = {}
        self._visited: set[str] = set()
        self._clicked: set[str] = set()
        self._focus_i = 0
        self._focus_steps = 0
        self._focus_seen: set[str] = set()
        self._recent_progress: list[bool] = []
        self._same: list[tuple[str, bool]] = []
        self._focus_note = ""
        self.stalls = 0

    # ---- prompt ---------------------------------------------------------------------------------------
    def _request(self, step: int, focus: Hypothesis | None, seconds_left: float) -> str:
        """Sentri's task state, then everything the app produced (page, requests, step outcomes) as APP DATA."""
        observation = dict(self._observation)
        canary = observation.pop("canary_for_this_identity", None)
        task = {
            "app_kind": self.strategy.app_kind,
            "hypotheses": [
                h.model_dump(include={"id", "title", "category", "targets", "first_moves"})
                for h in self.strategy.hypotheses
            ],
            "focus_hypothesis": focus.id if focus else None,
            "steps_left": self.budget.max_steps - step + 1,
            "seconds_left": round(seconds_left),
            "identities": ["anon", "a", "b"] if self.app_map.has_login_flow else ["anon"],
            "current_identity": observation.get("identity"),
            "canary_for_this_identity": canary,
        }
        if self._focus_note:
            task["note"] = self._focus_note + "; try something different"
        data = {
            "observation": observation,
            "recent_steps": self.history[-10:],
            "already_flagged": self.flagged[-12:],  # move on: these are queued for replay already
        }
        return build_user_prompt(task, data)

    # ---- repro bookkeeping ------------------------------------------------------------------------------
    def _repro_step(self, action: Action) -> ReproStep:
        match = None
        ref = (
            action.request_id
            if isinstance(action, Replay)
            else (action.from_request if isinstance(action, SendRequest) else None)
        )
        if ref is not None:
            src = next((r for r in self.s.requests if r.id == ref), None)
            if src is not None:
                match = {"method": src.method, "path": normalize_path(src.url)}
        return ReproStep(action.model_dump(), match)

    def _is_new_break(self, signals: list[dict[str, Any]], decision: Decision) -> bool:
        """New oracle signals always count; a suspicion without signals counts once per distinct reason."""
        keys = {f"{s['oracle_id']}|{s.get('route')}|{str(s['detail'])[:80]}" for s in signals}
        if keys - self._signal_keys:
            self._signal_keys |= keys
            return True
        if signals or not decision.suspect:
            return False  # the same break again
        reason = " ".join(decision.suspect_reason.lower().split())[:80]
        if not reason or reason in self._suspect_reasons:
            return False
        if any(reason[:40] in f.lower() for f in self.flagged):
            return False
        self._suspect_reasons.add(reason)
        return True

    # ---- deterministic fallback -------------------------------------------------------------------------
    def _fallback(self, observation: dict[str, Any], focus: Hypothesis | None, error: str) -> Decision:
        """When the model gives no usable answer even after a retry: the next unexplored element, chosen by code
        (an unvisited link, then a button not clicked yet, then the start page). Destructive-looking targets are
        skipped, as in recon."""
        reason = f"fallback (model reply unusable: {error[:120]})"
        hyp = focus.id if focus else ""
        for link in observation.get("links") or []:
            path = str(link).split("#")[0]
            if path and path not in self._visited and not any(w in path.lower() for w in SKIP_WORDS):
                return Decision(thought=reason, hypothesis_id=hyp, action=ActionSpec(kind="navigate", path=path))
        buttons = [*(observation.get("buttons") or [])]
        for form in observation.get("forms") or []:
            buttons += form.get("buttons") or []
        for b in buttons:
            sel = b.get("selector") if isinstance(b, dict) else None
            text = f"{sel} {b.get('text', '')}".lower() if sel else ""
            if sel and sel not in self._clicked and not any(w in text for w in SKIP_WORDS):
                return Decision(thought=reason, hypothesis_id=hyp, action=ActionSpec(kind="click", selector=sel))
        return Decision(thought=reason, hypothesis_id=hyp, action=ActionSpec(kind="navigate", path="/"))

    async def _decide(self, step: int, focus: Hypothesis | None, seconds_left: float) -> tuple[Decision | None, str]:
        """Ask the model; on an unusable reply, ask once more with the problem shown, then give up for this step."""
        prompt = self._request(step, focus, seconds_left)
        error = ""
        for attempt in range(2):
            if not self.provider.available:
                return None, error or "model unavailable or budget used"
            answer = await self.provider.complete_json("explorer", load_prompt("explorer"), prompt, Decision, 2000)
            if answer.ok and answer.value is not None:
                return answer.value, ""
            error = answer.error or "no usable reply"
            if attempt == 0:
                prompt += (
                    f"\n\nSENTRI: your previous reply could not be used ({error}). Reply again with exactly one "
                    "JSON object matching the schema, and nothing else."
                )
        return None, error

    # ---- focus rotation ---------------------------------------------------------------------------------
    def _current_focus(self) -> Hypothesis | None:
        hyps = self.strategy.hypotheses
        return hyps[self._focus_i % len(hyps)] if hyps else None

    def _rotate(self, why: str) -> None:
        hyps = self.strategy.hypotheses
        old = self._current_focus()
        self._focus_i += 1
        self._focus_steps, self._focus_seen, self._recent_progress = 0, set(), []
        new = self._current_focus()
        if hyps and old is not None and new is not None:
            self._focus_note = f"focus moved from {old.id} to {new.id}: {why}"
            self.emit("focus", self._focus_note, hypothesis_id=new.id)

    def _after_step(self, signature: str, fp_before: str, fp_after: str, signals: list[dict[str, Any]]) -> None:
        """Rotate by progress, not by a flat step count. Progress: a page state not seen under this focus, or a
        signal. A hypothesis gets `steps_per_hypothesis` steps, more while it keeps progressing (mid-flow), up to
        `max_steps_per_hypothesis`. The same action on the same target three times with no change is a stall."""
        self._focus_steps += 1
        progressed = bool(signals) or fp_after not in self._focus_seen
        self._focus_seen.add(fp_after)
        self._recent_progress = [*self._recent_progress[-2:], progressed]
        self._same = [*self._same[-2:], (signature, fp_before == fp_after and not signals)]
        if len(self._same) == 3 and len({sig for sig, _ in self._same}) == 1 and all(still for _, still in self._same):
            self._same = []
            self.stalls += 1
            self._rotate(f"stalled: the same {signature.split(' ', 1)[0]} action three times with no change")
            return
        if self._focus_steps >= self.budget.max_steps_per_hypothesis:
            self._rotate(f"{self._focus_steps}-step ceiling reached")
        elif self._focus_steps >= self.budget.steps_per_hypothesis and not any(self._recent_progress):
            self._rotate("no progress in the last 3 steps")

    # ---- loop -------------------------------------------------------------------------------------------
    async def run(self) -> ExploreResult:
        """The app is never reset during exploration, so any step can depend on everything before it. A candidate's
        repro is therefore the whole session prefix that led to it, replayed from a clean state. `path_start` and
        `parent` only shape the attack tree (lines of attack), never what gets replayed.

        Failures: an unusable model reply (after one retry), an invalid action or a refused action each count
        toward the limit of 3 in a row and use up a visible step; a usable reply that runs resets the count."""
        result = ExploreResult()
        started = time.monotonic()
        prefix: list[ReproStep] = [ReproStep(SwitchUser(user=self.s.current).model_dump())]  # type: ignore[arg-type]
        path_start, parent, failures = 1, None, 0
        current_hyp = ""
        self._observation = await self.s.observe()
        for step in range(1, self.budget.max_steps + 1):
            seconds_left = self.budget.max_seconds - (time.monotonic() - started)
            if seconds_left <= 0:
                result.stopped = "time budget used"
                break
            if not self.provider.available:
                result.stopped = "model unavailable or budget used"
                break
            focus = self._current_focus()
            decision, error = await self._decide(step, focus, seconds_left)
            self._focus_note = ""
            by_code = decision is None
            if decision is None:
                failures += 1
                self.emit("llm_fallback", f"explorer step {step}: model gave no usable action ({error})", error=error)
                if failures >= 3:
                    result.stopped = f"model failed 3 times in a row: {error}"
                    break
                decision = self._fallback(self._observation, focus, error)
                result.fallbacks += 1
            try:
                action = to_action(decision.action)
            except ValidationError as exc:
                failures += 1
                problem = f"invalid action: {exc.error_count()} problems ({exc.errors()[0]['msg'][:120]})"
                bad = decision.action.model_dump(exclude_none=True)
                self.record_step(
                    {
                        "step": step,
                        "parent_step": parent,
                        "hypothesis_id": decision.hypothesis_id or (focus.id if focus else ""),
                        "identity": self.s.current,
                        "thought": decision.thought,
                        "action": bad,
                        "action_text": f"invalid {bad.get('kind', '?')} action",
                        "ok": False,
                        "error": problem,
                        "url_after": self._observation.get("url"),
                        "signals": [],
                    }
                )
                self.emit("explore_step", f"step {step} invalid action -> {problem}", step=step)
                self.history.append({"step": step, "invalid_action": bad, "error": problem})
                result.steps, result.invalid = step, result.invalid + 1
                if failures >= 3:
                    result.stopped = f"3 unusable or invalid actions in a row: {problem}"
                    break
                continue
            hyp = decision.hypothesis_id or (focus.id if focus else "")
            if decision.new_path or (hyp != current_hyp and current_hyp):
                path_start, parent = step, None
            current_hyp = hyp

            identity = self.s.current
            fp_before = _fingerprint(self._observation)
            mark = self.s.mark()
            outcome = await self.s.execute(action)
            action = outcome.action or action  # what really ran, e.g. with Sentri's canary added
            evidence = self.s.agent_evidence_since(mark)
            signals = [
                {"oracle_id": r.oracle_id, "detail": r.detail, "route": r.route}
                for r in evaluate_agent(evidence, self.cfg)
                if r.fired
            ]
            refused = (outcome.error or "").startswith("refused")
            if by_code:
                pass  # code chose this step; the model's failure count stands
            elif refused:
                failures += 1
            else:
                failures = 0
            if outcome.ok or outcome.new_requests:  # a step that did nothing is not part of the state
                rs = self._repro_step(action)
                rs.expect_ok = outcome.ok
                prefix.append(rs)
            if isinstance(action, Navigate):
                self._visited.add(action.path)
            if isinstance(action, (Click, Burst)):
                self._clicked.add(action.selector)
            text = describe(action)
            record = {
                "step": step,
                "parent_step": parent,
                "hypothesis_id": hyp,
                "identity": identity,
                "thought": decision.thought,
                "action": action.model_dump(),
                "action_text": text,
                "ok": outcome.ok,
                "error": outcome.error,
                "url_after": outcome.url,
                "signals": signals,
            }
            self.record_step(record)
            self.emit(
                "explore_step",
                f"step {step} [{identity}] {text}" + (f" -> {outcome.error}" if outcome.error else ""),
                reason=decision.thought[:200] or None,
                step=step,
                hypothesis_id=hyp,
                signals=signals,
            )
            self.history.append(
                {
                    "step": step,
                    "as": identity,
                    "hypothesis": hyp,
                    "action": text,
                    "ok": outcome.ok,
                    "error": outcome.error,
                    "url_after": outcome.url,
                    "signals": [f"{s['oracle_id']}: {s['detail']}" for s in signals],
                }
            )
            result.steps = step
            parent = step
            if self._is_new_break(signals, decision):
                title = next((h.title for h in self.strategy.hypotheses if h.id == hyp), hyp or "exploration")
                what = ", ".join(str(s["detail"]) for s in signals) or decision.suspect_reason
                if len(result.candidates) < self.budget.max_candidates:
                    result.candidates.append(
                        Candidate(hyp, title, path_start, step, list(prefix), signals, decision.suspect_reason)
                    )
                    self.emit("candidate", f"possible break at step {step}: {what}", step=step)
                self.flagged.append(f"step {step}: {what}"[:200])
                path_start, parent = step + 1, None
            if isinstance(action, Finish):
                result.stopped = f"model finished: {action.reason}"
                break
            if failures >= 3:
                result.stopped = f"3 unusable or refused actions in a row: {outcome.error}"
                break
            self._observation = await self.s.observe()
            self._after_step(_signature(action), fp_before, _fingerprint(self._observation), signals)
        else:
            result.stopped = "step budget used"
        result.stalls = self.stalls
        return result


def _fingerprint(observation: dict[str, Any]) -> str:
    """What the page shows (not the request log): used to tell real progress from repeating the same thing."""
    seen = {k: observation.get(k) for k in ("identity", "url", "title", "text", "forms", "links")}
    return hashlib.sha256(json.dumps(seen, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _signature(action: Action) -> str:
    """The action and everything it targets (kind, selector/path/request, values)."""
    return f"{action.kind} " + json.dumps(action.model_dump(exclude={"kind"}), sort_keys=True, default=str)[:300]
