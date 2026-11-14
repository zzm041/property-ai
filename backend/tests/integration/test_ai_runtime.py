"""AI analysis, dispatch recommendation, incident, announcement, fallback tests."""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, IamErrorCode
from app.domain.governance import AuditAction
from app.main import create_app
from app.model.ai import AiCallLog, DispatchRecommendation, Incident, WorkOrderAnalysis
from app.model.identity import AuditLog
from app.model.work_order import WorkOrder, WorkOrderContent
from app.provider.asr import DevelopmentASRProvider
from app.provider.llm import DevelopmentLLMProvider
from app.provider.vision import DevelopmentVLMProvider
from app.repository.identity import IdentityRepository
from app.service.analysis import AnalysisService
from app.service.identity.auth import AuthService
from app.service.employee import EmployeeService
from app.service.work_order import WorkOrderService
from tests.integration.conftest import commit_open_transaction
from tests.integration.work_order_test_data import create_work_order_graph

TEST_SECRET = "t7-ai-secret-that-is-not-production"


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "test",
        "dev_auth_enabled": True,
        "app_secret_key": TEST_SECRET,
        "incident_threshold": 2,
        "ai_enqueue_on_create": False,
        "file_scanner_provider": "development",
        "file_scanner_demo_acknowledged": True,
    }
    values.update(overrides)
    return Settings(**values)


def _client(settings: Settings | None = None) -> TestClient:
    return TestClient(create_app(settings or _settings()))


def _token(client: TestClient, user_id: int) -> str:
    response = client.post(
        "/api/v1/auth/dev/token",
        json={"user_id": user_id},
        headers={"X-Request-Id": f"t7-login-{user_id}"},
    )
    assert response.status_code == 200
    return response.json()["data"]["access_token"]


def _auth(token: str, request_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Request-Id": request_id}


def _principal(session: Session, user_id: int):
    principal = AuthService(session, IdentityRepository(session)).load_principal(user_id)
    commit_open_transaction(session)
    return principal


def _check_in(session: Session, user_id: int, property_id: int, request_id: str) -> None:
    EmployeeService(session).check_in(
        actor=_principal(session, user_id),
        property_id=property_id,
        request_id=request_id,
    )


def _create_work_order(
    session: Session,
    *,
    user_id: int,
    house_id: int,
    contact_phone: str = "13800000000",
    text: str = "Kitchen outlet is sparking",
    file_ids: tuple[int, ...] = (),
) -> WorkOrder:
    return WorkOrderService(session, settings=_settings()).create_work_order(
        actor=_principal(session, user_id),
        house_id=house_id,
        contact_phone=contact_phone,
        request_id=f"t7-create-{uuid4().hex[:12]}",
        idempotency_key=f"t7-{uuid4()}",
        text=text,
        file_ids=file_ids,
    )


def _analyze(session: Session, work_order_id: int, settings: Settings | None = None):
    return AnalysisService(session, settings=settings or _settings()).analyze_work_order(
        work_order_id=work_order_id,
        request_id=f"t7-analyze-{work_order_id}",
    )


def test_successful_analysis_advances_to_pending_assignment(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    _check_in(
        identity_session,
        graph.employees.staff_a_user_id,
        graph.employees.property_1_id,
        "t7-checkin-a",
    )
    record = _create_work_order(
        identity_session,
        user_id=graph.employees.resident_id,
        house_id=graph.house_1_id,
        contact_phone="13800000000",
    )
    assert record.status == "PENDING_ANALYSIS"
    analysis = _analyze(identity_session, record.id)
    identity_session.expire_all()
    record = identity_session.get(WorkOrder, record.id)
    assert analysis is not None
    assert analysis.status == "COMPLETED"
    assert analysis.category == "ELECTRICAL"
    assert analysis.required_skill == "ELECTRICAL"
    assert record.status == "PENDING_ASSIGNMENT"
    assert record.category == "ELECTRICAL"
    eligible = identity_session.scalars(
        select(DispatchRecommendation).where(
            DispatchRecommendation.work_order_id == record.id,
            DispatchRecommendation.eligible == 1,
        )
    ).all()
    assert [item.employee_id for item in eligible] == [graph.employees.employee_a_id]


def test_create_does_not_fail_when_llm_times_out(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    record = _create_work_order(
        identity_session,
        user_id=graph.employees.resident_id,
        house_id=graph.house_1_id,
        contact_phone="13800000000",
    )
    settings = _settings(llm_provider_behavior="timeout")
    analysis = AnalysisService(
        identity_session,
        settings=settings,
        llm=DevelopmentLLMProvider("timeout"),
    ).analyze_work_order(work_order_id=record.id, request_id="t7-timeout")
    identity_session.expire_all()
    record = identity_session.get(WorkOrder, record.id)
    assert record.status == "PENDING_ANALYSIS"
    assert analysis is not None
    assert analysis.status == "FAILED"
    assert analysis.error_code == "AI.TIMEOUT"


def test_invalid_llm_output_does_not_mutate_work_order(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    record = _create_work_order(
        identity_session,
        user_id=graph.employees.resident_id,
        house_id=graph.house_1_id,
        contact_phone="13800000000",
    )
    analysis = AnalysisService(
        identity_session,
        settings=_settings(),
        llm=DevelopmentLLMProvider("invalid"),
    ).analyze_work_order(work_order_id=record.id, request_id="t7-invalid")
    identity_session.expire_all()
    record = identity_session.get(WorkOrder, record.id)
    assert record.status == "PENDING_ANALYSIS"
    assert analysis.status == "FAILED"
    assert analysis.error_code == "AI.INVALID_OUTPUT"


def test_low_confidence_keeps_manual_queue(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    record = _create_work_order(
        identity_session,
        user_id=graph.employees.resident_id,
        house_id=graph.house_1_id,
        contact_phone="13800000000",
    )
    analysis = AnalysisService(
        identity_session,
        settings=_settings(),
        llm=DevelopmentLLMProvider("low_confidence"),
    ).analyze_work_order(work_order_id=record.id, request_id="t7-low")
    identity_session.expire_all()
    record = identity_session.get(WorkOrder, record.id)
    assert analysis.status == "LOW_CONFIDENCE"
    assert record.status == "PENDING_ANALYSIS"


def test_duplicate_analysis_is_idempotent(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    record = _create_work_order(
        identity_session,
        user_id=graph.employees.resident_id,
        house_id=graph.house_1_id,
        contact_phone="13800000000",
    )
    first = _analyze(identity_session, record.id)
    second = _analyze(identity_session, record.id)
    rows = identity_session.scalars(
        select(WorkOrderAnalysis).where(WorkOrderAnalysis.work_order_id == record.id)
    ).all()
    assert len(rows) == 1
    assert first.id == second.id
    assert first.status == "COMPLETED"


def test_stale_result_does_not_override_human_cancel(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    record = _create_work_order(
        identity_session,
        user_id=graph.employees.resident_id,
        house_id=graph.house_1_id,
        contact_phone="13800000000",
    )
    WorkOrderService(identity_session).cancel(
        actor=_principal(identity_session, graph.employees.resident_id),
        work_order_id=record.id,
        request_id="t7-cancel",
    )
    analysis = _analyze(identity_session, record.id)
    identity_session.expire_all()
    record = identity_session.get(WorkOrder, record.id)
    assert record.status == "CANCELLED"
    # 已取消的工单不再产出分析：analyze_work_order 返回 None（产品语义），
    # 只要证明迟到的结果没有把记录改回任何状态即可。
    assert analysis is None


def test_asr_failure_still_creates_and_analyzes_text(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    _check_in(
        identity_session,
        graph.employees.staff_a_user_id,
        graph.employees.property_1_id,
        "t7-checkin-s_text",
    )
    actor = _principal(identity_session, graph.employees.resident_id)
    service = WorkOrderService(identity_session, settings=_settings())
    file_object = service.register_file(
        actor=actor,
        mime_type="audio/mpeg",
        size_bytes=12,
        request_id="t7-audio",
        claimed_property_id=graph.employees.property_1_id,
    )
    service.put_file_content(
        actor=actor,
        file_id=file_object.id,
        content=b"ID3audioDATA",
        request_id="t7-audio-content",
    )
    record = service.create_work_order(
        actor=actor,
        house_id=graph.house_1_id,
        contact_phone="13800000000",
        request_id="t7-create-audio",
        idempotency_key=f"t7-{uuid4()}",
        text="Sparking outlet",
        file_ids=(file_object.id,),
    )
    analysis = AnalysisService(
        identity_session,
        settings=_settings(),
        asr=DevelopmentASRProvider("failure"),
    ).analyze_work_order(work_order_id=record.id, request_id="t7-asr-fail")
    content = identity_session.scalar(
        select(WorkOrderContent).where(WorkOrderContent.work_order_id == record.id)
    )
    identity_session.expire_all()
    record = identity_session.get(WorkOrder, record.id)
    assert content.asr_text is None
    assert analysis.status == "COMPLETED"
    assert record.status == "PENDING_ASSIGNMENT"


def test_vlm_failure_does_not_block_llm_analysis(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    _check_in(
        identity_session,
        graph.employees.staff_a_user_id,
        graph.employees.property_1_id,
        "t7-checkin-alysis",
    )
    actor = _principal(identity_session, graph.employees.resident_id)
    service = WorkOrderService(identity_session, settings=_settings())
    file_object = service.register_file(
        actor=actor,
        mime_type="image/jpeg",
        size_bytes=12,
        request_id="t7-image",
        claimed_property_id=graph.employees.property_1_id,
    )
    service.put_file_content(
        actor=actor,
        file_id=file_object.id,
        content=b"\xff\xd8\xff\xe0image123",
        request_id="t7-image-content",
    )
    record = service.create_work_order(
        actor=actor,
        house_id=graph.house_1_id,
        contact_phone="13800000000",
        request_id="t7-create-image",
        idempotency_key=f"t7-{uuid4()}",
        text="Sparking outlet",
        file_ids=(file_object.id,),
    )
    analysis = AnalysisService(
        identity_session,
        settings=_settings(),
        vlm=DevelopmentVLMProvider("failure"),
    ).analyze_work_order(work_order_id=record.id, request_id="t7-vlm-fail")
    assert analysis.status == "COMPLETED"


def test_supervisor_can_assign_after_ai_failure(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    _check_in(
        identity_session,
        graph.employees.staff_a_user_id,
        graph.employees.property_1_id,
        "t7-fail-checkin",
    )
    record = _create_work_order(
        identity_session,
        user_id=graph.employees.resident_id,
        house_id=graph.house_1_id,
        contact_phone="13800000000",
    )
    AnalysisService(
        identity_session,
        settings=_settings(),
        llm=DevelopmentLLMProvider("timeout"),
    ).analyze_work_order(work_order_id=record.id, request_id="t7-fail-analyze")
    assigned = WorkOrderService(identity_session).assign(
        actor=_principal(identity_session, graph.employees.supervisor_p1_id),
        work_order_id=record.id,
        employee_id=graph.employees.employee_a_id,
        request_id="t7-fail-assign",
    )
    assert assigned.status == "ASSIGNED"


def test_human_override_records_recommendation_difference(
    identity_session: Session,
) -> None:
    graph = create_work_order_graph(identity_session)
    _go_on_duty(identity_session, graph.employees.employee_a_id)
    _go_on_duty(identity_session, graph.employees.employee_b_id)
    _copy_skills(identity_session, graph.employees.employee_a_id, graph.employees.employee_b_id)
    _copy_duties(identity_session, graph.employees.employee_a_id, graph.employees.employee_b_id)
    _check_in(
        identity_session,
        graph.employees.staff_a_user_id,
        graph.employees.property_1_id,
        "t7-ov-a",
    )
    _check_in(
        identity_session,
        graph.employees.staff_b_user_id,
        graph.employees.property_1_id,
        "t7-ov-b",
    )
    record = _create_work_order(
        identity_session,
        user_id=graph.employees.resident_id,
        house_id=graph.house_1_id,
        contact_phone="13800000000",
    )
    _analyze(identity_session, record.id)
    try:
        assigned = WorkOrderService(identity_session).assign(
            actor=_principal(identity_session, graph.employees.supervisor_p1_id),
            work_order_id=record.id,
            employee_id=graph.employees.employee_b_id,
            request_id="t7-override",
        )
    except AppError as exc:
        raise AssertionError(f"dispatch denied: {exc.details}") from exc
    assert assigned.status == "ASSIGNED"
    audit = identity_session.scalars(
        select(AuditLog).where(AuditLog.action == AuditAction.AI_RECOMMENDATION_REVIEWED.value)
    ).all()
    recs = identity_session.scalars(
        select(DispatchRecommendation).where(DispatchRecommendation.work_order_id == record.id)
    ).all()
    assert any(item.details_json.get("overridden") is True for item in audit), (
        f"recommendations={[(r.employee_id, r.rank_no, r.eligible) for r in recs]} "
        f"audit={[(a.action, a.details_json) for a in audit]}"
    )


def test_incident_aggregates_same_property_only(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    first = _create_work_order(
        identity_session,
        user_id=graph.employees.resident_id,
        house_id=graph.house_1_id,
        contact_phone="13800000000",
        text="Spark 1",
    )
    second = _create_work_order(
        identity_session,
        user_id=graph.employees.resident_id,
        house_id=graph.house_1_id,
        contact_phone="13800000000",
        text="Spark 2",
    )
    other = WorkOrderService(identity_session, settings=_settings()).create_work_order(
        actor=_principal(identity_session, graph.resident_b_id),
        house_id=graph.house_2_id,
        contact_phone="13800000000",
        request_id="t7-create-p2",
        idempotency_key=f"t7-{uuid4()}",
        text="Spark other property",
    )
    _analyze(identity_session, first.id)
    _analyze(identity_session, second.id)
    _analyze(identity_session, other.id)
    incidents = identity_session.scalars(
        select(Incident).where(Incident.property_id == graph.employees.property_1_id)
    ).all()
    assert len(incidents) == 1
    assert incidents[0].work_order_count >= 2
    identity_session.expire_all()
    other = identity_session.get(WorkOrder, other.id)
    assert other.incident_id is None


def test_mysql_rejects_cross_scope_incident_work_order(
    identity_session: Session,
    test_engine,
) -> None:
    graph = create_work_order_graph(identity_session)
    first = _create_work_order(
        identity_session,
        user_id=graph.employees.resident_id,
        house_id=graph.house_1_id,
        contact_phone="13800000000",
        text="Spark 1",
    )
    second = _create_work_order(
        identity_session,
        user_id=graph.employees.resident_id,
        house_id=graph.house_1_id,
        contact_phone="13800000000",
        text="Spark 2",
    )
    _analyze(identity_session, first.id)
    _analyze(identity_session, second.id)
    incident = identity_session.scalar(select(Incident))
    other = WorkOrderService(identity_session, settings=_settings()).create_work_order(
        actor=_principal(identity_session, graph.resident_b_id),
        house_id=graph.house_2_id,
        contact_phone="13800000000",
        request_id="t7-cross",
        idempotency_key=f"t7-{uuid4()}",
        text="Other property",
    )
    with test_engine.connect() as connection:
        try:
            connection.execute(
                text(
                    """
                    INSERT INTO incident_work_orders
                    (tenant_id, property_id, incident_id, work_order_id)
                    VALUES (:tenant_id, :property_id, :incident_id, :work_order_id)
                    """
                ),
                {
                    "tenant_id": incident.tenant_id,
                    "property_id": incident.property_id,
                    "incident_id": incident.id,
                    "work_order_id": other.id,
                },
            )
            connection.commit()
            raised = False
        except Exception:
            raised = True
            connection.rollback()
    assert raised is True


def test_announcement_draft_review_publish_and_resident_read(
    identity_session: Session,
) -> None:
    graph = create_work_order_graph(identity_session)
    client = _client()
    manager = _token(client, graph.employees.manager_p1_id)
    resident = _token(client, graph.employees.resident_id)
    system_admin = _token(client, graph.employees.system_admin_id)
    draft = client.post(
        "/api/v1/admin/announcements",
        json={
            "property_id": graph.employees.property_1_id,
            "ai_draft": True,
        },
        headers=_auth(manager, "t7-ann-draft"),
    )
    assert draft.status_code == 200
    announcement_id = draft.json()["data"]["id"]
    assert draft.json()["data"]["status"] == "DRAFT"
    publish_early = client.post(
        f"/api/v1/admin/announcements/{announcement_id}/publish",
        json={},
        headers=_auth(manager, "t7-ann-early"),
    )
    assert publish_early.status_code == 409
    submitted = client.post(
        f"/api/v1/admin/announcements/{announcement_id}/submit-review",
        json={},
        headers=_auth(manager, "t7-ann-submit"),
    )
    assert submitted.status_code == 200
    denied = client.post(
        f"/api/v1/admin/announcements/{announcement_id}/publish",
        json={},
        headers=_auth(system_admin, "t7-ann-sys"),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == IamErrorCode.FORBIDDEN
    published = client.post(
        f"/api/v1/admin/announcements/{announcement_id}/publish",
        json={},
        headers=_auth(manager, "t7-ann-publish"),
    )
    assert published.status_code == 200
    assert published.json()["data"]["status"] == "PUBLISHED"
    visible = client.get(
        "/api/v1/announcements",
        params={"property_id": graph.employees.property_1_id},
        headers=_auth(resident, "t7-ann-read"),
    )
    assert visible.status_code == 200
    assert visible.json()["data"]["items"][0]["id"] == announcement_id
    other_resident = _token(client, graph.resident_b_id)
    hidden = client.get(
        "/api/v1/announcements",
        params={"property_id": graph.employees.property_1_id},
        headers=_auth(other_resident, "t7-ann-cross"),
    )
    assert hidden.status_code == 403


def test_ai_call_log_has_no_secrets(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    record = _create_work_order(
        identity_session,
        user_id=graph.employees.resident_id,
        house_id=graph.house_1_id,
        contact_phone="13800000000",
    )
    _analyze(identity_session, record.id)
    logs = identity_session.scalars(select(AiCallLog)).all()
    assert logs
    for item in logs:
        payload = f"{item.model_version}{item.error_code}{item.input_hash}"
        assert "secret" not in payload.casefold()
        assert "authorization" not in payload.casefold()


def test_system_admin_cannot_read_analysis(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    record = _create_work_order(
        identity_session,
        user_id=graph.employees.resident_id,
        house_id=graph.house_1_id,
        contact_phone="13800000000",
    )
    _analyze(identity_session, record.id)
    client = _client()
    token = _token(client, graph.employees.system_admin_id)
    response = client.get(
        f"/api/v1/supervisor/work-orders/{record.id}/analysis",
        headers=_auth(token, "t7-sys-analysis"),
    )
    assert response.status_code == 403


def _go_on_duty(session: Session, employee_id: int) -> None:
    """PA-212: dispatch requires employee_daily_statuses.on_duty = ON_DUTY."""
    with session.get_bind().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO employee_daily_statuses "
                "(employee_id, tenant_id, property_id, business_date, on_duty, current_load, created_at, updated_at) "
                "SELECT e.id, e.tenant_id, (SELECT p.id FROM properties p WHERE p.tenant_id = e.tenant_id ORDER BY p.id LIMIT 1), "
                "CURRENT_DATE(), 'ON_DUTY', 0, NOW(6), NOW(6) FROM employees e WHERE e.id = :employee_id "
                "ON DUPLICATE KEY UPDATE on_duty='ON_DUTY', updated_at=NOW(6)"
            ),
            {"employee_id": employee_id},
        )


def _copy_skills(session: Session, source_employee_id: int, target_employee_id: int) -> None:
    """PA-212: 派单需要技能匹配，测试里把同一组合复制给目标员工。"""
    with session.get_bind().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO employee_skills (employee_id, skill_id, tenant_id, created_at, updated_at) "
                "SELECT :target, es.skill_id, es.tenant_id, NOW(6), NOW(6) FROM employee_skills es "
                "WHERE es.employee_id = :source "
                "AND NOT EXISTS (SELECT 1 FROM employee_skills x WHERE x.employee_id = :target AND x.skill_id = es.skill_id)"
            ),
            {"source": source_employee_id, "target": target_employee_id},
        )


def _copy_duties(session: Session, source_employee_id: int, target_employee_id: int) -> None:
    """PA-212: 派单还要求 duty（专业）匹配，测试里把同一组 duty 复制给目标员工。"""
    with session.get_bind().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO employee_duties (employee_id, tenant_id, duty_id, created_at, updated_at) "
                "SELECT :target, ed.tenant_id, ed.duty_id, NOW(6), NOW(6) FROM employee_duties ed "
                "WHERE ed.employee_id = :source "
                "AND NOT EXISTS (SELECT 1 FROM employee_duties x WHERE x.employee_id = :target AND x.duty_id = ed.duty_id)"
            ),
            {"source": source_employee_id, "target": target_employee_id},
        )

