"""Agentic exploration: the model drives the browser one validated action at a time.

Each step: observe -> the model picks one action -> code runs it -> plain-code oracles check what happened ->
the step is logged with the model's reasoning. Paths of steps that look broken (an oracle fired, or the model
suspects something) become candidates with exact reproduction steps, confirmed later by replay.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit

from pydantic import BaseModel, Field, ValidationError

from sentri.agent.actions import (
    ACTION_ADAPTER,
    Action,
    Burst,
    ClearSession,
    Click,
    Finish,
    Look,
    Navigate,
    Replay,
    SendRequest,
    SwitchUser,
    describe,
)
from sentri.agent.attempts import AttemptMemory, auth_block, operation_after, operation_before, params_of
from sentri.agent.recon import SKIP_WORDS, AppMap
from sentri.agent.session import AgentSession
from sentri.agent.statemap import (
    ExplorationMap,
    StateNode,
    action_sig,
    resolve_option_ref,
    route_of,
    unknown_option_ref,
)
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
    "fill_form",
    "look",
    "finish",
]


class FieldSpec(BaseModel):
    selector: str
    text: str = ""


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
    fields: list[FieldSpec] | None = None
    submit_selector: str | None = None
    part: str | None = None


class Decision(BaseModel):
    thought: str = Field(default="", max_length=1500)
    hypothesis_id: str = Field(default="", max_length=60)
    action: ActionSpec
    suspect: bool = False
    suspect_reason: str = Field(default="", max_length=500)
    new_path: bool = False
    goal_status: Literal["continue", "done", "stuck"] = "continue"  # about the focus hypothesis
    new_goal: str = Field(default="", max_length=200)  # a goal the observations suggest (grounded by code)
    repeat_reason: str = Field(default="", max_length=300)  # why repeating an unchanged attempt adds evidence


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
        a.FillForm,
        a.Look,
        a.Finish,
    ]
    return {cls.model_fields["kind"].default: cls for cls in classes}


_KIND_CLASSES = _kind_classes()


@dataclass
class ReproStep:
    action: dict[str, Any]
    match: dict[str, str] | None = None  # for replay/request: which observed request, by method + path
    expect_ok: bool = True  # it ran cleanly during exploration, so it must run cleanly on replay
    setup: bool = False  # before the candidate's own line of attack: replayed for state, not judged

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "match": self.match, "expect_ok": self.expect_ok, "setup": self.setup}


@dataclass
class Candidate:
    hypothesis_id: str
    title: str
    from_step: int
    to_step: int
    repro: list[ReproStep]
    signals: list[dict[str, Any]]
    suspect_reason: str = ""
    origin: str = "ai"  # ai | ai_suspicion | code_probe | code_fallback: who found it (not who confirmed it)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "title": self.title,
            "from_step": self.from_step,
            "to_step": self.to_step,
            "repro": [r.to_dict() for r in self.repro],
            "signals": self.signals,
            "origin": self.origin,
        }


@dataclass
class ExploreBudget:
    max_steps: int = 60
    max_seconds: float = 600
    max_candidates: int = 8
    steps_per_hypothesis: int = 10  # base allotment per focus hypothesis
    max_steps_per_hypothesis: int = 20  # ceiling while it keeps making progress (mid-flow)
    # Steps in a row, over the whole run (hypothesis changes do not reset it), that added no new evidence: the model
    # is told at `no_progress_warn`, and exploration stops honestly at `no_progress_stop`.
    no_progress_warn: int = 4
    no_progress_stop: int = 8


@dataclass
class ExploreResult:
    steps: int = 0
    candidates: list[Candidate] = field(default_factory=list)
    stopped: str = ""
    fallbacks: int = 0  # steps chosen by code because the model gave no usable answer
    invalid: int = 0  # steps lost to invalid actions
    stalls: int = 0  # times the same action repeated with no change forced a focus rotation
    stats: dict[str, Any] = field(default_factory=dict)  # measured coverage: states, actions by origin, ...


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
        goals: list[str] | None = None,
    ):
        self.goals = list(goals or [])
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
        self._code_steps: list[Decision] = []
        self.map = ExplorationMap()
        self._node: StateNode | None = None
        self.prompt_sizes: list[dict[str, Any]] = []  # per explorer call: section sizes in characters, no content
        self.attempts = AttemptMemory()  # every operation tried this run, across pages and hypothesis changes
        self._is_new = True
        self._text_changed = True
        self._detail: str | None = None  # what a `look` asked for, shown once
        self._changes: dict[str, Any] = {}
        self._goals_added = 0
        self.counts: dict[str, int] = {
            "ai_steps": 0,
            "code_probe_steps": 0,
            "fallback_steps": 0,
            "known_dead_end_refusals": 0,
            "repeat_refusals": 0,
            "unknown_option_refusals": 0,
            "looks": 0,
            "goals_done": 0,
            "goals_stuck": 0,
            "goals_added": 0,
            "actions_attempted": 0,
            "actions_executed_ok": 0,
        }

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
            "user_goals": self.goals,
            "current_identity": observation.get("identity"),
            "canary_for_this_identity": canary,
            "xss_marker": observation.pop("xss_marker", None),
        }
        if self._focus_note:
            task["note"] = self._focus_note + "; try something different"
        if self.attempts.unproductive >= self.budget.no_progress_warn:
            task["progress"] = (
                f"the last {self.attempts.unproductive} steps (any page, any hypothesis) added no new evidence; "
                f"exploration stops after {self.budget.no_progress_stop}. Choose a different experiment, act as "
                "another identity, or end the goal as stuck"
            )
        task["hypotheses"] = [
            {"id": h["id"], "title": h["title"], "targets": h.get("targets", [])} for h in task["hypotheses"]
        ]
        view = self._compact_view(observation)
        prompt = build_user_prompt(task, view)
        self._measure_prompt(step, task, view, prompt)
        return prompt

    def _measure_prompt(self, step: int, task: dict[str, Any], view: dict[str, Any], prompt: str) -> None:
        """Sizes only, never content: how many characters each part of an explorer call takes (the fixed rules and
        answer schema, each task and APP DATA section, and what pretty-printing adds), to find measured waste."""
        if len(self.prompt_sizes) >= 60:
            return

        def size(value: Any, indent: int | None = 1) -> int:
            return len(json.dumps(value, indent=indent, default=str, ensure_ascii=False))

        compact = size(task, None) + size(view, None)
        self.prompt_sizes.append(
            {
                "step": step,
                "rules_chars": len(load_prompt("explorer")),
                "schema_chars": len(json.dumps(Decision.model_json_schema())),
                "task": {k: size(v) for k, v in task.items()},
                "app_data": {k: size(v) for k, v in view.items()},
                "prompt_chars": len(prompt),
                "indent_chars": size(task) + size(view) - compact,
            }
        )

    def _compact_view(self, obs: dict[str, Any]) -> dict[str, Any]:
        """What the model needs to decide, not the whole page: the state it is in, what changed, the options with
        their known outcomes, where else there is something untried, and a short history."""
        node = self._node
        data: dict[str, Any] = {}
        if node is not None:
            data["state"] = {
                "id": node.sid,
                "new": self._is_new,
                "visits": node.visits,
                "identity": obs.get("identity"),
                "url": obs.get("url"),
                "title": obs.get("title"),
            }
            data["options"] = self.map.options_view(node) + [
                {k: v for k, v in o.items() if k != "sig"} for o in self._identity_options(node)
            ]
            data["frontier"] = [f for f in self.map.frontier() if f["state"] != node.sid]
        if self._changes:
            data["last_step_effect"] = self._changes
        if self._is_new or self._text_changed or self._detail == "text":
            limit = 2500 if self._detail == "text" else 1200
            data["text"] = str(obs.get("text") or "")[:limit]
        else:
            data["text"] = "(unchanged since your last visit to this state; `look` with part=text to see it)"
        if self._detail == "forms" or self._is_new:
            data["forms"] = obs.get("forms") or []
        if self._detail == "requests":
            data["requests"] = obs.get("recent_requests") or []
        else:
            data["recent_requests"] = (obs.get("recent_requests") or [])[-6:]
        for key in ("console_errors", "blocked_offsite", "created_records"):
            if obs.get(key):
                data[key] = obs[key]
        data["recent_steps"] = self.history[-5:]
        data["already_flagged"] = self.flagged[-8:]  # move on: these are queued for replay already
        self._detail = None
        return data

    def _identity_options(self, node: StateNode) -> list[dict[str, str]]:
        """Acting as another identity is an option like any link (e.g. a login wall seen as anon)."""
        if not self.app_map.has_login_flow:
            return []
        out = []
        labels = {"anon": "never logged in", "a": "logged-in account a", "b": "logged-in account b"}
        for i, who in enumerate(w for w in ("anon", "a", "b") if w != node.identity):
            tried = node.tried.get(f"switch_user {who}")
            status = "untried" if tried is None else f"tried {tried.count}x -> {', '.join(tried.to_states) or '?'}"
            failed = getattr(self.s, "login_failed", {}).get(who)
            if failed:
                status = f"login failed: {failed[:80]}"
            out.append(
                {
                    "id": f"U{i + 1}",
                    "sig": f"switch_user {who}",
                    "do": f"switch_user {who} ({labels[who]})",
                    "status": status,
                }
            )
        return out

    def _observe_state(self, step: int) -> None:
        self._node, self._is_new, self._text_changed = self.map.visit(self._observation, step)

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

    # ---- code-owned probes ------------------------------------------------------------------------------
    def _reflection_probes(self, limit: int = 4) -> list[Decision]:
        """Steps code runs first, whatever the model does: every GET form and every page seen with a query
        string, loaded with Sentri's inert XSS marker in each text parameter (xss_reflection judges it)."""
        marker = getattr(self.s, "xss_marker", None)
        if not marker:
            return []
        urls: list[str] = []
        for page in self.app_map.pages:
            base, _, query = page.path.partition("?")
            if query:
                params = [(k, marker) for k, _v in parse_qsl(query, keep_blank_values=True)]
                if params:
                    urls.append(f"{base}?{urlencode(params)}")
            for form in page.forms:
                if (form.get("method") or "get").lower() != "get":
                    continue
                names = [
                    f.get("name")
                    for f in form.get("fields", [])
                    if f.get("name")
                    and (f.get("type") or "text").lower() in ("text", "search", "")
                    or f.get("tag") == "textarea"
                ]
                action = form.get("action") or base
                if names and not action.startswith(("http:", "https:", "//")):
                    urls.append(f"{action.split('?')[0] or base}?{urlencode([(n, marker) for n in names if n])}")
        out = []
        for url in dict.fromkeys(urls):
            out.append(
                Decision(
                    thought="code probe: does this page write input back without escaping? (xss_reflection)",
                    hypothesis_id="reflected_input",
                    action=ActionSpec(kind="navigate", path=url),
                )
            )
        return out[:limit]

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
            # A decision is small (measured: ~165 output tokens on average); 900 leaves room for reasoning models
            # without reserving 2 000 tokens per call against token limits.
            answer = await self.provider.complete_json("explorer", load_prompt("explorer"), prompt, Decision, 900)
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
        line_index = 1  # where the current line of attack starts in `prefix`
        current_hyp = ""
        self._code_steps = self._reflection_probes()
        self._observation = await self.s.observe()
        self._observe_state(0)
        for step in range(1, self.budget.max_steps + 1):
            seconds_left = self.budget.max_seconds - (time.monotonic() - started)
            if seconds_left <= 0:
                result.stopped = "time budget used"
                break
            if not self.provider.available and not self._code_steps:
                result.stopped = f"model use stopped: {getattr(self.provider, 'stop_reason', None) or 'unavailable'}"
                break
            focus = self._current_focus()
            decision: Decision | None
            if self._code_steps:
                decision, error, by_code = self._code_steps.pop(0), "", True
            else:
                decision, error = await self._decide(step, focus, seconds_left)
                by_code = decision is None
            self._focus_note = ""
            if decision is None and not self._code_steps and not self.provider.available:
                # Model use has stopped (a limit or the provider's quota): a code fallback step would add nothing.
                result.stopped = f"model use stopped: {getattr(self.provider, 'stop_reason', None) or 'unavailable'}"
                break
            if decision is None:
                failures += 1
                self.emit("llm_fallback", f"explorer step {step}: model gave no usable action ({error})", error=error)
                if failures >= 3:
                    result.stopped = f"model failed 3 times in a row: {error}"
                    break
                decision = self._fallback(self._observation, focus, error)
                result.fallbacks += 1
            step_origin = (
                ("code_probe" if decision.hypothesis_id == "reflected_input" else "code_fallback") if by_code else "ai"
            )
            if decision.action.kind == "switch_user" and decision.action.user and self._node is not None:
                for opt in self._identity_options(self._node):  # "switch_user user=U1": the label of an identity option
                    if opt["id"] == decision.action.user:
                        decision.action.user = opt["sig"].split()[1]
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
            if self._node is not None:
                offered = [*self._node.options, *self._identity_options(self._node)]
                resolved = resolve_option_ref(action.model_dump(exclude_none=True), offered)
                if resolved is not None:
                    action = ACTION_ADAPTER.validate_python(resolved)
                stale = unknown_option_ref(action.model_dump(exclude_none=True), offered)
                if stale is not None:
                    failures += 1
                    problem = (
                        f"option {stale} is not offered in this state (option ids belong to the observation they "
                        "were shown with); use one of the current options or write the action out"
                    )
                    self.record_step(
                        {
                            "step": step,
                            "parent_step": parent,
                            "hypothesis_id": decision.hypothesis_id or (focus.id if focus else ""),
                            "identity": self.s.current,
                            "thought": decision.thought,
                            "action": action.model_dump(),
                            "action_text": describe(action),
                            "ok": False,
                            "error": f"not run: {problem}",
                            "url_after": self._observation.get("url"),
                            "signals": [],
                            "origin": step_origin,
                        }
                    )
                    self.history.append({"step": step, "action": describe(action), "not_run": problem})
                    self.counts["unknown_option_refusals"] += 1
                    result.steps = step
                    if failures >= 3:
                        result.stopped = f"3 unusable, invalid or refused actions in a row: {problem}"
                        break
                    continue
            hyp = decision.hypothesis_id or (focus.id if focus else "")
            node_before = self._node
            sig = action_sig(action.model_dump())
            if isinstance(action, Look):
                # More detail next step; nothing in the app is touched.
                self._detail = action.part
                self.counts["looks"] += 1
                self.history.append({"step": step, "look": action.part})
                result.steps = step
                continue
            route = route_of(action.model_dump(), {r.id: r.url for r in getattr(self.s, "requests", [])})
            known = None
            if node_before is not None and not by_code:
                known = self.map.dead_end(node_before, sig) or (
                    self.map.route_dead_end(node_before, route) if route else None
                )
            if known is not None:
                # Already proven pointless in this state: not run again, the model is told why.
                failures += 1
                self.counts["known_dead_end_refusals"] += 1
                self.record_step(
                    {
                        "step": step,
                        "parent_step": parent,
                        "hypothesis_id": hyp,
                        "identity": self.s.current,
                        "thought": decision.thought,
                        "action": action.model_dump(),
                        "action_text": describe(action),
                        "ok": False,
                        "error": f"not run: known dead end ({known})",
                        "url_after": self._observation.get("url"),
                        "signals": [],
                        "origin": step_origin,
                    }
                )
                self.history.append({"step": step, "action": describe(action), "not_run": f"known dead end ({known})"})
                result.steps = step
                self.stalls += 1
                self._rotate(f"stalled: chose an action already known to be a dead end here ({known})")
                if failures >= 3:
                    result.stopped = "3 unusable, invalid or dead-end actions in a row"
                    break
                continue
            intent = None if by_code else operation_before(action.model_dump(), self._observation.get("url"))
            params = params_of(action.model_dump(exclude_none=True))
            repeat = self.attempts.check(self.s.current, intent, params, decision.repeat_reason)
            if repeat is not None:
                failures += 1
                self.attempts.refusals += 1
                self.counts["repeat_refusals"] += 1
                self.record_step(
                    {
                        "step": step,
                        "parent_step": parent,
                        "hypothesis_id": hyp,
                        "identity": self.s.current,
                        "thought": decision.thought,
                        "action": action.model_dump(),
                        "action_text": describe(action),
                        "ok": False,
                        "error": f"not run: {repeat}",
                        "url_after": self._observation.get("url"),
                        "signals": [],
                        "origin": step_origin,
                    }
                )
                self.history.append({"step": step, "action": describe(action), "not_run": repeat})
                self._changes = {
                    "action": describe(action),
                    "not_run": repeat,
                    "next": "a different experiment, another identity, or end the goal as stuck",
                }
                result.steps = step
                self.attempts.unproductive += 1
                if self.attempts.unproductive >= self.budget.no_progress_stop:
                    result.stopped = f"no progress: {self.attempts.unproductive} steps in a row added no new evidence"
                    break
                if failures >= 3:
                    result.stopped = f"no progress: 3 refused actions in a row; the last: {repeat}"
                    break
                continue
            if decision.new_path or (hyp != current_hyp and current_hyp):
                path_start, parent = step, None
                line_index = len(prefix)
            current_hyp = hyp

            identity = self.s.current
            start_url = self._observation.get("url")
            fp_before = _fingerprint(self._observation)
            mark = self.s.mark()
            self.counts["actions_attempted"] += 1
            self.counts[{"ai": "ai_steps", "code_probe": "code_probe_steps"}.get(step_origin, "fallback_steps")] += 1
            outcome = await self.s.execute(action)
            if outcome.ok:
                self.counts["actions_executed_ok"] += 1
            if isinstance(action, ClearSession):
                self.map.cleared.add(identity)
            action = outcome.action or action  # what really ran, e.g. with Sentri's canary added
            evidence = self.s.agent_evidence_since(mark)
            signals = [
                {"oracle_id": r.oracle_id, "detail": r.detail, "route": r.route, "error_key": r.error_key}
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
                "origin": step_origin,
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
                        Candidate(
                            hyp,
                            title,
                            path_start,
                            step,
                            [dataclasses.replace(r, setup=i < line_index) for i, r in enumerate(prefix)],
                            signals,
                            decision.suspect_reason,
                            finding_origin(signals, step_origin),
                        )
                    )
                    self.emit("candidate", f"possible break at step {step}: {what}", step=step)
                self.flagged.append(f"step {step}: {what}"[:200])
                path_start, parent = step + 1, None
                line_index = len(prefix)
            if isinstance(action, Finish):
                untried = len(self.strategy.hypotheses) - self._focus_i - 1
                if untried <= 0:
                    result.stopped = f"model finished: {action.reason}"
                    break
                # Budget left and hypotheses never focused on: finishing early would waste the run.
                self._rotate(f"finish asked with {untried} hypotheses not tried yet")
            if failures >= 3:
                result.stopped = f"3 unusable or refused actions in a row: {outcome.error}"
                break
            self._observation = await self.s.observe()
            self._observe_state(step)
            node_after = self._node
            changed = node_after is not node_before or self._text_changed or bool(outcome.new_requests)
            if node_before is not None:
                tried = self.map.record(
                    node_before, sig, outcome.ok, outcome.error, node_after, changed, len(signals), step
                )
                if route:
                    statuses = tuple(
                        r.status for r in getattr(self.s, "requests", []) if r.id in set(outcome.new_requests)
                    )
                    state_changed = node_after is not node_before or self._text_changed
                    self.map.record_route(node_before, route, statuses, state_changed, len(signals))
                self._changes = {
                    "action": describe(action),
                    "ok": outcome.ok,
                    "error": outcome.error,
                    "from_state": node_before.sid,
                    "to_state": node_after.sid if node_after else None,
                    "requests": len(outcome.new_requests),
                    "signals": [f"{s['oracle_id']}: {s['detail']}" for s in signals],
                    "times_tried_here": tried.count,
                }
            new_reqs = [r for r in getattr(self.s, "requests", []) if r.id in set(outcome.new_requests)]
            intended = action.path if isinstance(action, Navigate) else None
            blocked = auth_block(new_reqs, self._observation, intended, start_url) if outcome.ok else None
            if isinstance(action, SwitchUser) and outcome.ok:
                self.attempts.identity_changed()
            writes = [r for r in new_reqs if str(getattr(r, "method", "GET")).upper() != "GET" and r.status is not None]
            wrote = outcome.ok and not blocked and any(r.status < 400 for r in writes)
            final = urlsplit(str(self._observation.get("url") or "")).path or "/"
            answered = "/".join(map(str, sorted({r.status for r in writes}))) + " " if writes else ""
            outcome_text = (
                f"error: {(outcome.error or '')[:60]}"
                if not outcome.ok
                else f"{answered}-> {final}" + (" (blocked)" if blocked else "")
            )
            op = operation_after(intent, new_reqs, getattr(self.s, "base_url", "")) if intent else None
            added = self.attempts.record(
                identity,
                intent,
                op,
                outcome_text,
                self._is_new,
                len(signals),
                wrote,
                bool(decision.repeat_reason.strip()),
                params,
            )
            if self._changes is not None:
                self._changes["added_evidence"] = added
                if blocked:
                    others = [w for w in ("a", "b") if w != identity and w not in getattr(self.s, "login_failed", {})]
                    self._changes["auth"] = {
                        "blocked": blocked,
                        "acting_as": identity,
                        "authorized_identities_available": others if self.app_map.has_login_flow else [],
                    }
            if self.attempts.unproductive >= self.budget.no_progress_stop:
                result.stopped = f"no progress: {self.attempts.unproductive} steps in a row added no new evidence"
                break
            if not by_code:
                self._goal_update(decision, step)
            self._after_step(_signature(action), fp_before, _fingerprint(self._observation), signals)
        else:
            result.stopped = "step budget used"
        result.stalls = self.stalls
        result.stats = {
            **self.map.stats(),
            **self.counts,
            "stalls": self.stalls,
            "no_evidence_streak": self.attempts.unproductive,
            "prompt_sizes": self.prompt_sizes,
        }
        return result

    def _goal_update(self, decision: Decision, step: int) -> None:
        """The model's view of the focus goal moves the focus (done or stuck); a new goal it proposes is kept only
        if grounded in the state it was observed in, and at most a few are added."""
        if decision.new_goal and self._goals_added < 4 and self._node is not None:
            self._goals_added += 1
            self.counts["goals_added"] += 1
            self.strategy.hypotheses.append(
                Hypothesis(
                    id=f"goal_{self._goals_added}",
                    title=decision.new_goal[:200],
                    targets=[self._node.path],
                    rationale=f"proposed at step {step} from state {self._node.sid}",
                )
            )
        if decision.goal_status in ("done", "stuck"):
            self.counts["goals_" + decision.goal_status] += 1
            self._rotate(f"the model reports the goal {decision.goal_status}")


CODE_PROBE_SIGNALS = {("cross_user_exposure", "other_user_record"), ("csrf_missing", None)}


def finding_origin(signals: list[dict[str, Any]], step_origin: str) -> str:
    """Who discovered a candidate. A signal from Sentri's own probes (the direct-by-id check, the CSRF re-send)
    is a code-probe discovery even when a model-chosen action led to it; model commentary does not change that."""
    if not signals:
        return "ai_suspicion" if step_origin == "ai" else step_origin
    from_probe = all(
        (s["oracle_id"], s.get("error_key")) in CODE_PROBE_SIGNALS or (s["oracle_id"], None) in CODE_PROBE_SIGNALS
        for s in signals
    )
    return "code_probe" if from_probe else step_origin


def _fingerprint(observation: dict[str, Any]) -> str:
    """What the page shows (not the request log): used to tell real progress from repeating the same thing."""
    seen = {k: observation.get(k) for k in ("identity", "url", "title", "text", "forms", "links")}
    return hashlib.sha256(json.dumps(seen, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _signature(action: Action) -> str:
    """The action and everything it targets (kind, selector/path/request, values)."""
    return f"{action.kind} " + json.dumps(action.model_dump(exclude={"kind"}), sort_keys=True, default=str)[:300]
