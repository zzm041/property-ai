"""Resident output allowlists; property authorization stays in the Service."""
from app.domain.work_order import WorkOrderEvent


def resident_media_visible(*, phase: str, receipt_id: int | None, receipt_ids: set[int]) -> bool:
    return phase == "REPORT" or (phase in {"BEFORE", "AFTER"} and receipt_id in receipt_ids)


def resident_detail(data: dict) -> dict:
    """Build a new projection; never expose mixed historical notes."""
    def pick(item, fields):
        return {key: item[key] for key in fields.split() if key in item}

    reporter = data["work_order"]["reporter_user_id"]
    receipt_ids = {item["id"] for item in data["receipts"]}
    public_events = {event.value for event in WorkOrderEvent} | {"AI_TASK_FAILED", "AI_TASK_ATTEMPT"}
    logs, notes = [], []
    for item in data["logs"]:
        if item["event_code"] not in public_events:
            continue
        projected = pick(item, "id actor_type from_status to_status process_reason event_code created_at")
        if (item["actor_type"] == "RESIDENT" and item["actor_user_id"] == reporter
                and item["event_code"] in {"APPEND", "REOPEN", "CANCEL"}):
            projected["reason"] = item.get("reason")
            if item["event_code"] == "APPEND" and item.get("reason"):
                notes.append(item["reason"])
        logs.append(projected)
    result = {
        "work_order": pick(data["work_order"], "id tenant_id property_id house_id reporter_user_id status process_reason category severity priority version current_assignment_id sla_due_at assigned_at accepted_at started_at resolved_at closed_at created_at updated_at sla_overdue"),
        "text": data.get("text"), "asr_text": data.get("asr_text"),
        "extra_notes": "\n".join(notes) or None,
        "media": [pick(item, "id file_id kind taken_phase process_id receipt_id created_at")
                  for item in data["media"] if resident_media_visible(
                      phase=item["taken_phase"], receipt_id=item.get("receipt_id"), receipt_ids=receipt_ids)],
        "assignments": [
            pick(item, "id employee_id status assigned_at accept_at ended_at")
            | ({"reason": "AUTO_DISPATCH"} if item.get("reason") == "AUTO_DISPATCH"
               and item.get("assigned_by_user_id") is None else {})
            for item in data["assignments"]
        ],
        "processes": [pick(item, "id cycle_no assignee_id started_at ended_at") for item in data["processes"]],
        "receipts": [pick(item, "id process_id cycle_no assignee_id problem_type method materials fee_amount fee_flag resolved unresolved_reason follow_up finished_at") for item in data["receipts"]],
        "feedbacks": [pick(item, "id process_id confirmed rating comment unresolved_flag created_at")
                      for item in data["feedbacks"] if item["actor_user_id"] == reporter],
        "logs": logs,
        "display": pick(data["display"], "property_timezone assignee_names timestamp_labels analysis_status analysis_message"),
    }
    visible_times = set()

    def collect_times(value):
        if isinstance(value, list):
            for item in value:
                collect_times(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key.endswith("_at") and isinstance(item, str):
                    visible_times.add(item)
                elif isinstance(item, (dict, list)):
                    collect_times(item)

    collect_times({key: value for key, value in result.items() if key != "display"})
    result["display"]["timestamp_labels"] = {
        key: value for key, value in result["display"].get("timestamp_labels", {}).items()
        if key in visible_times
    }
    return result
