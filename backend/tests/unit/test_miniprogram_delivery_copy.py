"""Ordinary Mini Program surfaces should use business language."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_ordinary_pages_do_not_expose_development_or_internal_copy() -> None:
    pages = ROOT / "miniprogram" / "pages"
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in pages.rglob("*")
        if path.suffix in {".js", ".wxml"} and "dev-login" not in path.parts
    )

    for forbidden in (
        "Fake Provider",
        "本地开发模型",
        "后端会使用代码兜底值",
        "employee_id，不是 user_id",
        "异常代码兜底（需修复）",
        "不可变版本结构尚未获准迁移",
        "当前测试短信通道",
        "小区 ID",
        "房屋编号：{{item.house_id}}",
        "小区 {{item.property_id}}",
    ):
        assert forbidden not in text

    for relative in ("login/login.wxml", "home/home.wxml"):
        assert "envNote" not in (pages / relative).read_text(encoding="utf-8")


def test_service_configuration_failure_is_user_safe() -> None:
    api_source = (ROOT / "miniprogram" / "services" / "api.js").read_text(
        encoding="utf-8"
    )

    assert "未配置 API 地址" not in api_source
    assert "当前服务未配置，请联系管理员" in api_source


def test_shared_styles_cover_touch_forms_feedback_and_safe_area() -> None:
    styles = (ROOT / "miniprogram" / "app.wxss").read_text(encoding="utf-8")

    for selector_or_rule in (
        "padding-bottom: env(safe-area-inset-bottom)",
        "button.btn",
        "input",
        "textarea",
        ".section-title",
        ".notice",
        "@media (max-width: 360px)",
    ):
        assert selector_or_rule in styles


def test_ordinary_pages_do_not_render_internal_numeric_identifiers_as_labels() -> None:
    pages = ROOT / "miniprogram" / "pages"
    text = "\n".join(path.read_text(encoding="utf-8") for path in pages.rglob("*.wxml"))

    for forbidden in (
        "处理人员 {{item.employee_id}}",
        "楼栋 ID：",
        "主处理员工：{{selected.primary_employee_id",
        "{{created.initial_admin_user_id}}",
        "（{{created.tenant_id}}）",
        "（{{created.property_id}}）",
    ):
        assert forbidden not in text


def test_identity_conflict_copy_explains_review_requirement() -> None:
    source = (ROOT / "miniprogram" / "utils" / "error.js").read_text(
        encoding="utf-8"
    )

    assert "两名不同且当前有权的审核人" in source


def test_queue_decorates_notification_target_before_inserting_it() -> None:
    source = (ROOT / "miniprogram" / "pages" / "supervisor" / "queue.js").read_text(
        encoding="utf-8"
    )

    assert "decorateWorkOrder(require(\"../../utils/ui\").workOrderOf(targetDetail), labels)" in source
