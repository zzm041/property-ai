"""A minimal multi-turn dispatch agent: observe, decide, act -- at most five steps.

The supervisor agent in `app.service.supervisor_agent` answers one queue-review
question inside a single bounded read pass. Dispatch needs a different shape: the
answer is only reachable after a *sequence* of tool results (what is pending, what
the order needs, who is actually available today), and each result changes what the
next question is. This module implements exactly that narrow loop and nothing more:

* `max_steps = 5` planner turns; each turn is observe -> decide -> execute one tool.
* Four fixed tools, all reads except `propose_assignment`, which returns a
  suggestion and never writes an assignment row.
* A tool that fails is retried once; a second failure is recorded as
  `AGENT_TOOL_SKIPPED` and the loop keeps going with what it already knows.
* Running out of steps degrades to a deterministic rule dispatch and records
  `AGENT_STEP_LIMIT`. It never raises for a model or tool failure.
* Every tool execution writes one `ai_call_logs` row with
  `operation='agent_step'`, the step index in `attempt`, the tool and the decision
  in `biz_type`, and the latency. All rows of one loop share one trace id.

There is no autonomous planning, no graph engine and no second agent: the planner
picks from the tools that are actually available, and the server validates and
executes whatever it picked.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from time import monotonic
from uuid import uuid4

from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.time import utc_now
from app.provider.errors import ProviderError

logger = get_logger(__name__)

MAX_STEPS = 5
STEP_OPERATION = "agent_step"
STEP_PROMPT_VERSION = "dispatch-agent-loop-v1"
TOOL_NAMES = (
    "list_pending_work_orders",
    "get_work_order",
    "list_eligible_staff",
    "propose_assignment",
)
DECISIONS = ("QUEUE", "READ_ONE", "CHECK_STAFF", "PROPOSE")
TERMINAL_TOOL = "propose_assignment"
SKIPPED = "AGENT_TOOL_SKIPPED"
STEP_LIMIT = "AGENT_STEP_LIMIT"
INVALID_ACTION = "AGENT.INVALID_ACTION"
PLANNER_FAILED = "AGENT.PLANNER_FAILED"


@dataclass(frozen=True)
class Step:
    """One executed tool call, i.e. one `agent_step` audit row."""

    step_index: int
    tool: str
    decision: str
    ok: bool
    latency_ms: float
    attempts: int
    error_code: str | None
    result: object = None

    def trace(self) -> dict:
        return {
            "step": self.step_index,
            "tool": self.tool,
            "decision": self.decision,
            "ok": self.ok,
            "attempts": self.attempts,
            "latency_ms": round(self.latency_ms, 3),
            "error_code": self.error_code,
            "result": _summarise(self.result),
        }


def _summarise(result: object) -> object:
    """A short, PII-free view of what a tool returned, for the trace."""

    if isinstance(result, list):
        return {"count": len(result)}
    if isinstance(result, dict):
        keep = ("work_order_id", "version", "status", "required_duty", "required_skill",
                "eligible_count", "applied", "employee_id", "reason", "summary")
        summary = {key: result[key] for key in keep if key in result}
        if "eligible" in result and isinstance(result["eligible"], list):
            summary["eligible_count"] = len(result["eligible"])
        if "suggestion" in result:
            summary["suggestion"] = result["suggestion"]
        return summary
    return None


class DispatchAgentLoop:
    """Runs at most `max_steps` tool turns and never raises on tool failure."""

    def __init__(self, *, tools, planner, recorder=None, max_steps: int = MAX_STEPS,
                 clock=monotonic):
        if type(max_steps) is not int or not 1 <= max_steps <= MAX_STEPS:
            raise ValueError(f"max_steps must be within 1..{MAX_STEPS}")
        self.tools = tools
        self.planner = planner
        self.recorder = recorder
        self.max_steps = max_steps
        self.clock = clock

    # ------------------------------------------------------------------ run
    def run(self, *, question: str, business_date: date | None = None,
            scope: dict | None = None) -> dict:
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 500:
            raise AppError(code="AGENT.INVALID_INPUT", message="Question is invalid",
                           status_code=422)
        trace_id = f"agent-loop-{uuid4().hex}"
        scope = dict(scope or {})
        steps: list[Step] = []
        observations: list[dict] = []

        for step_index in range(1, self.max_steps + 1):
            state = {
                "question": question,
                "observations": observations,
                "tools": list(TOOL_NAMES),
                "step_index": step_index,
                "remaining_steps": self.max_steps - step_index + 1,
                "business_date": None if business_date is None else business_date.isoformat(),
            }
            try:
                decision = self.planner.decide(state)
            except ProviderError as exc:
                logger.error("dispatch_agent_planner_failed", trace_id=trace_id,
                             error=f"{type(exc).__name__}: {exc}"[:200])
                return self._degrade(trace_id, steps, observations, scope, PLANNER_FAILED)
            if isinstance(decision, dict) and decision.get("degrade"):
                return self._degrade(trace_id, steps, observations, scope,
                                     str(decision["degrade"]))
            final = self._final_decision(decision)
            if final is not None:
                return self._complete(trace_id, steps, observations, scope, final)

            tool, label, args = self._command(decision)
            if tool is None:
                return self._degrade(trace_id, steps, observations, scope, INVALID_ACTION)

            step = self._execute(step_index, tool, label, args, business_date)
            steps.append(step)
            self._record(trace_id, step, scope)
            if not step.ok:
                observations.append({"tool": tool, "skipped": True,
                                     "error_code": step.error_code})
                continue
            observations.append({"tool": tool, "result": step.result})
            if tool == TERMINAL_TOOL and isinstance(step.result, dict) \
                    and step.result.get("suggestion") is not None:
                return self._complete(trace_id, steps, observations, scope, {
                    "status": "recommended",
                    "summary": "Agent recommends an assignee; a supervisor must confirm.",
                    "proposal": step.result["suggestion"],
                    "work_order_id": step.result.get("work_order_id"),
                })

        return self._degrade(trace_id, steps, observations, scope, STEP_LIMIT)

    # ------------------------------------------------------------- decisions
    @staticmethod
    def _final_decision(decision: object) -> dict | None:
        if not isinstance(decision, dict) or "final" not in decision:
            return None
        final = decision["final"]
        if not isinstance(final, dict):
            return None
        status = final.get("status")
        if status not in {"recommended", "no_eligible_staff", "needs_human", "no_work"}:
            return None
        return {
            "status": status,
            "summary": str(final.get("summary") or "")[:500],
            "proposal": final.get("proposal"),
            "work_order_id": final.get("work_order_id"),
        }

    @staticmethod
    def _command(decision: object):
        if not isinstance(decision, dict) or set(decision) - {"tool", "args", "decision"}:
            return None, "", {}
        tool = decision.get("tool")
        label = str(decision.get("decision") or "").upper()
        args = decision.get("args") or {}
        if tool not in TOOL_NAMES or label not in DECISIONS or not isinstance(args, dict):
            return None, label, {}
        return tool, label, args

    # --------------------------------------------------------------- execute
    def _execute(self, step_index: int, tool: str, label: str, args: dict,
                 business_date: date | None) -> Step:
        method = getattr(self.tools, tool, None)
        if method is None:
            return Step(step_index, tool, label, False, 0.0, 0, SKIPPED)
        call_args = dict(args)
        if tool in {"list_eligible_staff", "propose_assignment"} \
                and "business_date" not in call_args:
            call_args["business_date"] = business_date
        started = self.clock()
        last_error: str | None = None
        for attempt in (1, 2):
            try:
                result = method(**call_args)
            except Exception as exc:  # a tool failure must degrade, never abort the loop
                last_error = f"{type(exc).__name__}"
                logger.warning("dispatch_agent_tool_failed", tool=tool, attempt=attempt,
                               error=last_error)
                continue
            latency_ms = (self.clock() - started) * 1000.0
            return Step(step_index, tool, label, True, latency_ms, attempt, None, result)
        latency_ms = (self.clock() - started) * 1000.0
        return Step(step_index, tool, label, False, latency_ms, 2, SKIPPED)

    # ----------------------------------------------------------------- audit
    def _record(self, trace_id: str, step: Step, scope: dict) -> None:
        logger.info("agent_step", trace_id=trace_id, step_index=step.step_index,
                    tool=step.tool, decision=step.decision, ok=step.ok,
                    attempts=step.attempts, latency_ms=round(step.latency_ms, 3),
                    error_code=step.error_code)
        if self.recorder is None:
            return
        try:
            self.recorder.record(trace_id=trace_id, step=step, scope=scope)
        except Exception as exc:  # an audit failure never changes the answer
            logger.error("agent_step_log_failed", trace_id=trace_id,
                         error=f"{type(exc).__name__}: {exc}"[:200])

    # -------------------------------------------------------------- outcomes
    def _complete(self, trace_id, steps, observations, scope, final) -> dict:
        extra = {key: value for key, value in final.items() if key != "status"}
        extra["outcome"] = final.get("status")
        return self._payload("completed", trace_id, steps, observations, scope,
                             error_code=None, extra=extra)

    def _degrade(self, trace_id, steps, observations, scope, error_code) -> dict:
        fallback = self._rule_dispatch(observations, error_code)
        logger.warning("dispatch_agent_degraded", trace_id=trace_id,
                       error_code=error_code, work_order_id=fallback.get("work_order_id"))
        return self._payload("degraded", trace_id, steps, observations, scope,
                             error_code=error_code, extra={"fallback": fallback})

    @staticmethod
    def _rule_dispatch(observations: list[dict], error_code: str) -> dict:
        """Deterministic fallback: oldest pending order goes to the supervisor queue."""

        queue: list = []
        for observation in observations:
            if observation.get("tool") == "list_pending_work_orders" \
                    and isinstance(observation.get("result"), list):
                queue = observation["result"]
                break
        work_order_id = None
        if queue and isinstance(queue[0], dict):
            work_order_id = queue[0].get("work_order_id")
        return {
            "mode": "RULE_DISPATCH",
            "reason": error_code,
            "work_order_id": work_order_id,
            "assignments": [],
            "escalate_to_human": True,
            "summary": "Agent stopped early; dispatch falls back to the rule queue for a human.",
        }

    def _payload(self, status, trace_id, steps, observations, scope, *, error_code, extra):
        payload = {
            "status": status,
            "trace_id": trace_id,
            "error_code": error_code,
            "step_count": len(steps),
            "max_steps": self.max_steps,
            "steps": [step.trace() for step in steps],
            "observations": observations,
            "execution_mode": getattr(self.planner, "mode", "unknown"),
            "scope": scope,
        }
        payload.update(extra)
        return payload


class AgentStepRecorder:
    """Writes one `ai_call_logs` row per executed step."""

    def __init__(self, *, session_factory, settings):
        self.session_factory = session_factory
        self.settings = settings
        self.provider_mode = "PRODUCTION" if settings.app_env == "production" else "DEVELOPMENT"
        self.model_version = getattr(settings, "supervisor_agent_model", "") or "deterministic-stub"

    def record(self, *, trace_id: str, step: Step, scope: dict) -> None:
        from app.core.database import begin_owned_transaction
        from app.model.ai import AiCallLog
        from app.repository.ai import AiRepository

        now = utc_now()
        started_at = now - __import__("datetime").timedelta(milliseconds=step.latency_ms)
        decision = step.trace()
        session = self.session_factory()
        try:
            with begin_owned_transaction(session):
                AiRepository(session).add_call_log(AiCallLog(
                    tenant_id=scope.get("tenant_id"),
                    property_id=scope.get("property_id"),
                    work_order_id=step.result.get("work_order_id")
                    if isinstance(step.result, dict) else None,
                    request_id=trace_id[:128],
                    provider_type="LLM",
                    provider_mode=self.provider_mode,
                    operation=STEP_OPERATION,
                    biz_type=f"AGENT_STEP:{step.tool}:{step.decision}"[:64],
                    prompt_version=STEP_PROMPT_VERSION,
                    model_version=str(self.model_version)[:64],
                    status="SUCCESS" if step.ok else "FAILED",
                    started_at=started_at,
                    finished_at=now,
                    latency_ms=int(step.latency_ms),
                    attempt=step.step_index,
                    error_code=step.error_code,
                    token_usage=None,
                    input_hash=hashlib.sha256(
                        json.dumps(decision, sort_keys=True, ensure_ascii=False,
                                   default=str).encode("utf-8")).hexdigest(),
                ))
        finally:
            session.close()
