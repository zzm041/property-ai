"""The dispatch agent loop: bounded steps, tool failure tolerance, one trace id."""
from datetime import date

import pytest

from app.core.errors import AppError
from app.provider.errors import ProviderTimeoutError
from app.service.dispatch_agent import (
    STEP_LIMIT,
    STEP_OPERATION,
    STEP_PROMPT_VERSION,
    SKIPPED,
    DispatchAgentLoop,
)

WORK_DATE = date(2026, 11, 12)


class Tools:
    """Small in-memory tool set, with per-tool failure scripting."""

    def __init__(self, *, queue, staff, fail=(), fail_once=()):
        self.queue = queue
        self.staff = staff
        self.fail = set(fail)
        self.fail_once = set(fail_once)
        self.calls = []

    def _call(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if name in self.fail:
            raise RuntimeError(f"{name} is down")
        if name in self.fail_once:
            self.fail_once.discard(name)
            raise RuntimeError(f"{name} timed out once")
        return None

    def list_pending_work_orders(self, limit=5):
        self._call("list_pending_work_orders", limit=limit)
        return list(self.queue)

    def get_work_order(self, work_order_id):
        self._call("get_work_order", work_order_id=work_order_id)
        return {"work_order_id": work_order_id, "version": 2, "status": "PENDING_ASSIGNMENT",
                "required_skill": self.staff.get("required_skill"),
                "required_duty": self.staff.get("required_duty")}

    def list_eligible_staff(self, work_order_id, business_date=None):
        self._call("list_eligible_staff", work_order_id=work_order_id,
                   business_date=business_date)
        return {"work_order_id": work_order_id, "eligible_count": len(self.staff["eligible"]),
                "eligible": list(self.staff["eligible"]),
                "blocked": dict(self.staff.get("blocked") or {})}

    def propose_assignment(self, work_order_id, employee_id=None, reason="", business_date=None):
        self._call("propose_assignment", work_order_id=work_order_id,
                   employee_id=employee_id, business_date=business_date)
        eligible = list(self.staff["eligible"])
        chosen = eligible[0] if eligible else None
        return {"work_order_id": work_order_id,
                "employee_id": None if chosen is None else chosen["employee_id"],
                "applied": False,
                "eligible_count": len(eligible),
                "eligible": eligible,
                "blocked": dict(self.staff.get("blocked") or {}),
                "suggestion": None if chosen is None else {
                    "work_order_id": work_order_id, "employee_id": chosen["employee_id"],
                    "requires_supervisor_confirmation": True, "applied": False}}


class Recorder:
    def __init__(self):
        self.rows = []

    def record(self, *, trace_id, step, scope):
        self.rows.append({
            "trace_id": trace_id,
            "attempt": step.step_index,
            "tool": step.tool,
            "decision": step.decision,
            "status": "SUCCESS" if step.ok else "FAILED",
            "error_code": step.error_code,
            "latency_ms": step.latency_ms,
            "biz_type": f"AGENT_STEP:{step.tool}:{step.decision}",
            "scope": scope,
        })


QUEUE_ONE = [{"work_order_id": 41, "version": 2, "status": "PENDING_ASSIGNMENT",
              "priority": "MEDIUM", "category": "ELECTRICAL", "sla_overdue": False}]
QUEUE_EMPTY = []
NOBODY = {"eligible": [], "blocked": {"OFF_DUTY": 12, "SKILL_MISMATCH": 3}}
PLUMBER = {"eligible": [{"employee_id": 62, "remaining_capacity": 2, "on_duty": True}],
           "blocked": {"OFF_DUTY": 11}}


def loop(**kwargs):
    return DispatchAgentLoop(**kwargs)


# --------------------------------------------------------------- scenarios
def test_no_eligible_staff_escalates_after_three_steps():
    """Scenario 1: queue -> proposal attempt -> staff check -> escalate."""

    tools = Tools(queue=QUEUE_ONE, staff=NOBODY)
    recorder = Recorder()
    result = loop(tools=tools, planner=_stub(), recorder=recorder).run(
        question="楼道的灯不亮，没有当班电工", business_date=WORK_DATE)

    assert result["status"] == "completed"
    assert result["outcome"] == "no_eligible_staff"
    assert result["error_code"] is None
    assert result["step_count"] >= 3
    assert [row["tool"] for row in result["steps"]] == [
        "list_pending_work_orders", "propose_assignment", "list_eligible_staff"]
    assert result["steps"][1]["result"]["suggestion"] is None
    assert result["summary"].startswith("No eligible staff for work order 41")
    assert "OFF_DUTY=12" in result["summary"]
    assert result["proposal"] is None
    assert result["trace_id"].startswith("agent-loop-")
    assert [row["attempt"] for row in recorder.rows] == [1, 2, 3]
    assert all(row["trace_id"] == result["trace_id"] for row in recorder.rows)


def test_normal_plumber_gets_a_suggestion_within_two_steps():
    """Scenario 2: queue -> propose, the suggestion names a real employee."""

    tools = Tools(queue=QUEUE_ONE, staff=PLUMBER)
    recorder = Recorder()
    result = loop(tools=tools, planner=_stub(), recorder=recorder).run(
        question="厨房下水管渗水，安排人去看一下", business_date=WORK_DATE)

    assert result["status"] == "completed"
    assert result["outcome"] == "recommended"
    assert result["step_count"] == 2
    assert result["proposal"]["employee_id"] == 62
    assert result["proposal"]["applied"] is False
    assert result["proposal"]["requires_supervisor_confirmation"] is True
    assert [row[0] for row in tools.calls] == ["list_pending_work_orders",
                                                     "propose_assignment"]
    assert len(recorder.rows) == 2


def test_every_tool_failing_degrades_to_rule_dispatch_without_raising():
    """Scenario 3: all tools down -> rule dispatch, step limit, no exception."""

    tools = Tools(queue=QUEUE_ONE, staff=PLUMBER,
                  fail={"list_pending_work_orders", "get_work_order",
                        "list_eligible_staff", "propose_assignment"})
    recorder = Recorder()
    result = loop(tools=tools, planner=_stub(), recorder=recorder).run(
        question="楼道灯不亮", business_date=WORK_DATE)

    assert result["status"] == "degraded"
    assert result["error_code"] == SKIPPED
    assert result["fallback"]["mode"] == "RULE_DISPATCH"
    assert result["fallback"]["escalate_to_human"] is True
    assert result["fallback"]["work_order_id"] is None
    assert result["step_count"] == 1
    assert [step["tool"] for step in result["steps"]] == ["list_pending_work_orders"]
    assert all(step["error_code"] == SKIPPED for step in result["steps"])
    assert len(recorder.rows) == 1


# ------------------------------------------------------------ failure paths
def test_a_single_tool_failure_is_retried_and_the_turn_succeeds():
    tools = Tools(queue=QUEUE_ONE, staff=PLUMBER, fail_once={"list_pending_work_orders"})
    result = loop(tools=tools, planner=_stub()).run(
        question="厨房下水管渗水", business_date=WORK_DATE)

    assert result["status"] == "completed"
    assert result["outcome"] == "recommended"
    assert result["steps"][0]["attempts"] == 2
    assert result["steps"][0]["ok"] is True
    assert result["steps"][0]["error_code"] is None


def test_a_tool_that_fails_twice_is_skipped_and_the_loop_continues():
    tools = Tools(queue=QUEUE_ONE, staff=PLUMBER,
                  fail={"propose_assignment"})
    result = loop(tools=tools, planner=_stub()).run(
        question="厨房下水管渗水", business_date=WORK_DATE)

    assert result["status"] == "completed"
    assert result["outcome"] == "needs_human"
    skipped = [step for step in result["steps"] if not step["ok"]]
    assert len(skipped) == 1 and skipped[0]["tool"] == "propose_assignment"
    assert skipped[0]["error_code"] == SKIPPED
    assert result["steps"][-1]["tool"] == "list_eligible_staff"
    assert "escalate to a human supervisor" in result["summary"]


def test_an_empty_queue_finishes_without_calling_other_tools():
    tools = Tools(queue=QUEUE_EMPTY, staff=PLUMBER)
    result = loop(tools=tools, planner=_stub()).run(
        question="现在有单要派吗", business_date=WORK_DATE)

    assert result["status"] == "completed"
    assert result["outcome"] == "no_work"
    assert result["summary"] == "The dispatch queue is empty."
    assert [row[0] for row in tools.calls] == ["list_pending_work_orders"]


def test_planner_failure_degrades_instead_of_failing_the_request():
    class Broken:
        mode = "broken"

        def decide(self, state):
            raise ProviderTimeoutError("planner unreachable")

    result = loop(tools=Tools(queue=QUEUE_ONE, staff=PLUMBER), planner=Broken()).run(
        question="楼道灯不亮", business_date=WORK_DATE)

    assert result["status"] == "degraded"
    assert result["error_code"] == "AGENT.PLANNER_FAILED"
    assert result["fallback"]["mode"] == "RULE_DISPATCH"
    assert result["step_count"] == 0


def test_an_invalid_command_degrades_rather_than_executing_anything():
    class Wrong:
        mode = "wrong"

        def decide(self, state):
            return {"tool": "delete_work_orders", "args": {}, "decision": "QUEUE"}

    tools = Tools(queue=QUEUE_ONE, staff=PLUMBER)
    result = loop(tools=tools, planner=Wrong()).run(question="楼道灯不亮",
                                                    business_date=WORK_DATE)

    assert result["status"] == "degraded"
    assert result["error_code"] == "AGENT.INVALID_ACTION"
    assert tools.calls == []


def test_max_steps_is_bounded():
    with pytest.raises(ValueError):
        DispatchAgentLoop(tools=Tools(queue=[], staff=PLUMBER), planner=_stub(), max_steps=6)
    with pytest.raises(ValueError):
        DispatchAgentLoop(tools=Tools(queue=[], staff=PLUMBER), planner=_stub(), max_steps=0)


def test_an_invalid_question_is_rejected_before_any_tool_runs():
    tools = Tools(queue=QUEUE_ONE, staff=PLUMBER)
    with pytest.raises(AppError) as excinfo:
        loop(tools=tools, planner=_stub()).run(question="   ")
    assert excinfo.value.code == "AGENT.INVALID_INPUT"
    assert tools.calls == []


def test_the_audit_contract_keeps_step_index_tool_and_latency():
    """Each row carries step index in `attempt`, tool+decision in `biz_type`."""

    recorder = Recorder()
    result = loop(tools=Tools(queue=QUEUE_ONE, staff=PLUMBER), planner=_stub(),
                  recorder=recorder).run(question="厨房下水管渗水", business_date=WORK_DATE)

    assert STEP_OPERATION == "agent_step"
    assert STEP_PROMPT_VERSION == "dispatch-agent-loop-v1"
    assert recorder.rows[0]["biz_type"] == "AGENT_STEP:list_pending_work_orders:QUEUE"
    assert recorder.rows[1]["biz_type"] == "AGENT_STEP:propose_assignment:PROPOSE"
    assert all(row["latency_ms"] >= 0 for row in recorder.rows)
    assert {row["status"] for row in recorder.rows} == {"SUCCESS"}


def test_an_audit_failure_never_breaks_the_answer():
    class Exploding(Recorder):
        def record(self, **kwargs):
            raise RuntimeError("audit table is gone")

    result = loop(tools=Tools(queue=QUEUE_ONE, staff=PLUMBER), planner=_stub(),
                  recorder=Exploding()).run(question="厨房下水管渗水",
                                            business_date=WORK_DATE)
    assert result["status"] == "completed"
    assert result["proposal"]["employee_id"] == 62


def test_exhausting_max_steps_degrades_with_the_step_limit_code():
    """A planner that never stops is cut off at max_steps, not followed forever."""

    class Chatty:
        mode = "chatty"

        def decide(self, state):
            return {"tool": "list_pending_work_orders", "args": {"limit": 5},
                    "decision": "QUEUE"}

    recorder = Recorder()
    tools = Tools(queue=QUEUE_ONE, staff=PLUMBER)
    result = loop(tools=tools, planner=Chatty(), recorder=recorder).run(
        question="楼道的灯不亮", business_date=WORK_DATE)

    assert result["status"] == "degraded"
    assert result["error_code"] == STEP_LIMIT
    assert result["step_count"] == 5
    assert result["fallback"]["work_order_id"] == 41
    assert len(recorder.rows) == 5


def _stub():
    from app.provider.llm.dispatch_planner import DispatchPlanner
    return DispatchPlanner(None)
