"""Analysis attempt fencing in the existing append-only work-order journal.

No task platform or mutable JSON business record: each claim is an audit event
under the existing WorkOrder mutex. Retry messages carry only its identifiers.
"""
from app.model.work_order import WorkOrderLog


def accepts_attempt(repository, record, context: dict | None) -> bool:
    if not context:
        return True
    previous = context.get("claim")
    latest = repository.latest_analysis_attempt(record.id)
    payload = latest.payload_json if latest is not None else None
    if payload:
        if payload["task_id"] == context["task_id"]:
            if int(payload["attempt"]) > context["attempt"]:
                return False
        elif previous:
            return False
    if previous:
        # prepare COMMIT may have succeeded without an acknowledgement. A new
        # connection resolves this by the journal; never replay a raw statement.
        if payload and payload["task_id"] == context["task_id"]:
            expected = int(payload["work_order_version"])
            allowed = {expected}
            if payload["from_status"] == "PENDING_ANALYSIS" and record.status == "PENDING_ASSIGNMENT":
                allowed.add(expected + 1)
            return record.version in allowed
        return record.version == previous["work_order_version"]
    expected = context.get("expected_version")
    return expected is None or record.version == expected


def claim_attempt(repository, record, snapshot, context: dict | None, request_id: str):
    if not context:
        return snapshot
    payload = {
        "task_id": context["task_id"], "attempt": context["attempt"],
        "execution_token": context["execution_token"],
        "analysis_id": snapshot.analysis_id, "analysis_version": snapshot.analysis_version,
        "input_hash": snapshot.input_hash, "work_order_version": record.version,
        "from_status": record.status,
    }
    repository.add_log(WorkOrderLog(
        tenant_id=record.tenant_id, property_id=record.property_id,
        work_order_id=record.id, actor_user_id=None, actor_type="SYSTEM",
        from_status=record.status, to_status=record.status,
        process_reason=record.process_reason, event_code="AI_TASK_ATTEMPT",
        request_id=request_id, payload_json=payload,
    ))
    context["claim"] = payload
    return snapshot


def owns_attempt(repository, record, analysis, context: dict | None) -> bool:
    if not context:
        return True
    claim = context.get("claim")
    if not claim:
        return False
    latest = repository.latest_analysis_attempt(record.id)
    if latest is None or latest.payload_json != claim:
        return False
    return (analysis is not None and analysis.id == claim["analysis_id"]
            and analysis.analysis_version == claim["analysis_version"]
            and analysis.input_hash == claim["input_hash"])
