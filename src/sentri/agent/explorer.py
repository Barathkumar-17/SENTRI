"""Agentic exploration: the model drives the browser one validated action at a time.

Each step: observe -> the model picks one action -> code runs it -> plain-code oracles check what happened ->
the step is logged with the model's reasoning. Paths of steps that look broken (an oracle fired, or the model
suspects something) become candidates with exact reproduction steps, confirmed later by replay.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from sentri.agent.actions import ACTION_ADAPTER, Action, Finish, Replay, SendRequest, SwitchUser, describe
from sentri.agent.recon import AppMap
from sentri.agent.session import AgentSession
from sentri.agent.strategy import Hypothesis, Strategy
from sentri.llm import BudgetedProvider, load_prompt
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

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "match": self.match}


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
    steps_per_hypothesis: int = 10


@dataclass
class ExploreResult:
    steps: int = 0
    candidates: list[Candidate] = field(default_factory=list)
    stopped: str = ""


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

    # ---- prompt ---------------------------------------------------------------------------------------
    def _focus(self, step: int) -> Hypothesis | None:
        hyps = self.strategy.hypotheses
        if not hyps:
            return None
        return hyps[min((step - 1) // max(1, self.budget.steps_per_hypothesis), len(hyps) - 1)]

    def _request(self, step: int, focus: Hypothesis | None, seconds_left: float) -> str:
        return json.dumps(
            {
                "app_kind": self.strategy.app_kind,
                "hypotheses": [
                    h.model_dump(include={"id", "title", "category", "targets", "first_moves"})
                    for h in self.strategy.hypotheses
                ],
                "focus_hypothesis": focus.id if focus else None,
                "steps_left": self.budget.max_steps - step + 1,
                "seconds_left": round(seconds_left),
                "identities": ["anon", "a", "b"] if self.app_map.has_login_flow else ["anon"],
                "recent_steps": self.history[-10:],
                "already_flagged": self.flagged[-12:],  # move on: these are queued for replay already
                "observation": self._observation,
            },
            indent=1,
            default=str,
        )

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

    # ---- loop -------------------------------------------------------------------------------------------
    async def run(self) -> ExploreResult:
        result = ExploreResult()
        started = time.monotonic()
        path: list[ReproStep] = [ReproStep(SwitchUser(user=self.s.current).model_dump())]  # type: ignore[arg-type]
        path_start, parent, failures = 1, None, 0
        current_hyp = ""
        for step in range(1, self.budget.max_steps + 1):
            seconds_left = self.budget.max_seconds - (time.monotonic() - started)
            if seconds_left <= 0:
                result.stopped = "time budget used"
                break
            if not self.provider.available:
                result.stopped = "model unavailable or budget used"
                break
            focus = self._focus(step)
            self._observation = await self.s.observe()
            answer = await self.provider.complete_json(
                "explorer", load_prompt("explorer"), self._request(step, focus, seconds_left), Decision, 2000
            )
            if not answer.ok or answer.value is None:
                failures += 1
                self.emit("llm_fallback", f"explorer step {step}: model gave no usable action", error=answer.error)
                if failures >= 3:
                    result.stopped = f"model failed 3 times: {answer.error}"
                    break
                continue
            failures = 0
            decision = answer.value
            try:
                action = to_action(decision.action)
            except ValidationError as exc:
                self.history.append(
                    {
                        "step": step,
                        "invalid_action": decision.action.model_dump(exclude_none=True),
                        "error": f"invalid action: {exc.error_count()} problems",
                    }
                )
                continue
            hyp = decision.hypothesis_id or (focus.id if focus else "")
            if decision.new_path or (hyp != current_hyp and current_hyp):
                path = [ReproStep(SwitchUser(user=self.s.current).model_dump())]  # type: ignore[arg-type]
                path_start, parent = step, None
            current_hyp = hyp

            identity = self.s.current
            mark = self.s.mark()
            outcome = await self.s.execute(action)
            evidence = self.s.agent_evidence_since(mark)
            signals = [
                {"oracle_id": r.oracle_id, "detail": r.detail, "route": r.route}
                for r in evaluate_agent(evidence, self.cfg)
                if r.fired
            ]
            path.append(self._repro_step(action))
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
                        Candidate(hyp, title, path_start, step, list(path), signals, decision.suspect_reason)
                    )
                    self.emit("candidate", f"possible break at step {step}: {what}", step=step)
                self.flagged.append(f"step {step}: {what}"[:200])
                path = [ReproStep(SwitchUser(user=self.s.current).model_dump())]  # type: ignore[arg-type]
                path_start, parent = step + 1, None
            if isinstance(action, Finish):
                result.stopped = f"model finished: {action.reason}"
                break
        else:
            result.stopped = "step budget used"
        return result

