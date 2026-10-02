"""Analysis attempt fencing in the existing append-only work-order journal.

No task platform or mutable JSON business record: each claim is an audit event
under the existing WorkOrder mutex. Retry messages carry only its identifiers.
"""
from hashlib import sha256

from app.model.work_order import WorkOrderLog


_CHECKPOINT_PROVIDERS = frozenset({"ASR", "VLM"})


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
        elif previous and not (
            context.get("claim_handoff") is True
            and payload == previous
            and previous.get("attachment_completion") is True
            and previous.get("task_id") != context.get("task_id")
        ):
            return False
    if previous:
        # prepare COMMIT may have succeeded without an acknowledgement. A new
        # connection resolves this by the journal; never replay a raw statement.
        if payload and payload["task_id"] == context["task_id"]:
            expected = int(payload["work_order_version"])
            allowed = {expected}
            if payload["from_status"] == "PENDING_ANALYSIS" and record.status == "PENDING_ASSIGNMENT":
                allowed.add(expected + 1)
            if payload.get("attachment_completion") is True and record.status == "PENDING_ASSIGNMENT":
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
    if getattr(snapshot, "attachment_completion", False):
        payload["attachment_completion"] = True
        payload["source_analysis_id"] = snapshot.source_analysis_id
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


def provider_checkpoint_payload(snapshot, context: dict, provider_type: str,
                                model_version: str, value: str) -> dict[str, object]:
    """Build an immutable partial-result checkpoint bound to one durable task input."""
    if provider_type not in _CHECKPOINT_PROVIDERS:
        raise ValueError("Unsupported provider checkpoint")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Provider checkpoint requires non-empty text")
    return {
        "schema_version": 1,
        "task_id": context["task_id"],
        "attempt": context["attempt"],
        "analysis_id": snapshot.analysis_id,
        "analysis_version": snapshot.analysis_version,
        "input_hash": snapshot.input_hash,
        "provider_type": provider_type,
        "model_version": model_version,
        "value": value,
        "value_sha256": sha256(value.encode("utf-8")).hexdigest(),
    }


def read_provider_checkpoint(payload: object, snapshot, context: dict,
                             provider_type: str, model_version: str) -> str | None:
    """Return only an intact checkpoint for this exact task, input and provider."""
    if not isinstance(payload, dict) or provider_type not in _CHECKPOINT_PROVIDERS:
        return None
    expected = {
        "schema_version": 1,
        "task_id": context.get("task_id"),
        "analysis_id": snapshot.analysis_id,
        "analysis_version": snapshot.analysis_version,
        "input_hash": snapshot.input_hash,
        "provider_type": provider_type,
        "model_version": model_version,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        return None
    attempt = payload.get("attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt > context.get("attempt", 0):
        return None
    value = payload.get("value")
    digest = payload.get("value_sha256")
    if not isinstance(value, str) or not value.strip() or not isinstance(digest, str):
        return None
    if sha256(value.encode("utf-8")).hexdigest() != digest:
        return None
    return value


def media_checkpoint_payload(
    snapshot,
    context: dict,
    provider_type: str,
    model_version: str,
    protocol_version: str,
    media,
    value: str,
    *,
    source: str = "provider",
) -> dict[str, object]:
    """Bind one successful provider result to immutable file content.

    Unlike the legacy aggregate checkpoint, this record may be reused by a
    later, explicitly authorized analysis version when the exact file,
    provider model and protocol still match.
    """
    if provider_type not in _CHECKPOINT_PROVIDERS:
        raise ValueError("Unsupported provider checkpoint")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Provider checkpoint requires non-empty text")
    content_hash = getattr(media, "content_hash", None)
    if not isinstance(content_hash, str) or len(content_hash) != 64:
        raise ValueError("Media checkpoint requires a finalized content hash")
    return {
        "schema_version": 2,
        "task_id": context["task_id"],
        "attempt": context["attempt"],
        "analysis_id": snapshot.analysis_id,
        "analysis_version": snapshot.analysis_version,
        "input_hash": snapshot.input_hash,
        "provider_type": provider_type,
        "model_version": model_version,
        "protocol_version": protocol_version,
        "media_id": getattr(media, "media_id", None),
        "file_id": getattr(media, "file_id", None),
        "media_kind": media.kind,
        "mime_type": media.mime_type,
        "storage_key": media.storage_key,
        "content_hash": content_hash,
        "source": source,
        "value": value,
        "value_sha256": sha256(value.encode("utf-8")).hexdigest(),
    }


def read_media_checkpoint(
    payload: object,
    provider_type: str,
    model_version: str,
    protocol_version: str,
    media,
) -> str | None:
    """Return an intact result only for the same immutable media contract."""
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        return None
    expected = {
        "provider_type": provider_type,
        "model_version": model_version,
        "protocol_version": protocol_version,
        "media_id": getattr(media, "media_id", None),
        "file_id": getattr(media, "file_id", None),
        "media_kind": media.kind,
        "mime_type": media.mime_type,
        "storage_key": media.storage_key,
        "content_hash": getattr(media, "content_hash", None),
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        return None
    value = payload.get("value")
    digest = payload.get("value_sha256")
    if not isinstance(value, str) or not value.strip() or not isinstance(digest, str):
        return None
    if sha256(value.encode("utf-8")).hexdigest() != digest:
        return None
    return value
