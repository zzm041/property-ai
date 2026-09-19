from hashlib import sha256
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.core.errors import AppError, FileErrorCode
from app.domain.iam import Principal
from app.domain.work_order import MediaTakenPhase
from app.model.work_order import FileObject, WorkOrder
from app.provider.errors import ProviderInvalidOutputError
from app.service.work_order import (
    WorkOrderService,
    _content_matches_mime,
    _filename_matches_mime,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
JPEG = b"\xff\xd8\xff\xe0"


def _actor(user_id: int = 7) -> Principal:
    return Principal(
        user_id=user_id,
        status="ACTIVE",
        display_name="synthetic user",
        grants=(),
    )


def _file(*, uploader_user_id: int = 7, content: bytes = JPEG) -> FileObject:
    record = FileObject()
    record.id = 11
    record.tenant_id = 2
    record.property_id = 3
    record.uploader_user_id = uploader_user_id
    record.mime_type = "image/jpeg"
    record.size_bytes = len(content)
    record.storage_key = "dev-object:2:3:opaque"
    record.content_hash = sha256(content).hexdigest()
    record.scanner_status = "skipped_dev"
    return record


def _service(record: FileObject, *, content: bytes = JPEG) -> WorkOrderService:
    service = WorkOrderService.__new__(WorkOrderService)
    service._settings = Settings(_env_file=None, app_env="test")
    service._repository = MagicMock()
    service._repository.get_file.return_value = record
    service._repository.list_work_orders_for_file.return_value = ()
    service._storage = MagicMock()
    service._storage.get_object.return_value = content
    service._session = MagicMock()
    service._reload_actor = MagicMock(side_effect=lambda actor: actor)
    service._actor_can_see_file_tenant = MagicMock(return_value=True)
    return service


@pytest.mark.parametrize(
    ("mime_type", "content"),
    (
        ("image/jpeg", JPEG),
        ("image/png", b"\x89PNG\r\n\x1a\n"),
        ("image/webp", b"RIFF\x04\x00\x00\x00WEBP"),
        ("audio/mpeg", b"ID3\x04"),
        ("audio/mp4", b"\x00\x00\x00\x18ftypM4A "),
        ("audio/wav", b"RIFF\x04\x00\x00\x00WAVE"),
        ("audio/webm", b"\x1aE\xdf\xa3"),
    ),
)
def test_allowed_media_magic_is_recognized(mime_type: str, content: bytes) -> None:
    assert _content_matches_mime(mime_type, content)
    assert not _content_matches_mime(mime_type, b"not-media")


def test_filename_extension_must_match_declared_mime() -> None:
    assert _filename_matches_mime("image/jpeg", "repair.JPG")
    assert _filename_matches_mime("audio/mp4", "voice.m4a")
    assert not _filename_matches_mime("image/jpeg", "payload.exe")
    assert not _filename_matches_mime("image/png", "renamed.jpg")


def test_upload_validates_bytes_and_records_actual_hash() -> None:
    record = _file()
    record.content_hash = "registration-placeholder"
    service = _service(record)

    result = service._put_file_content(
        actor=_actor(),
        file_id=record.id,
        content=JPEG,
        request_id="persistent-media-put",
    )

    assert result is record
    assert record.content_hash == sha256(JPEG).hexdigest()
    service._storage.put_object.assert_called_once_with(
        record.storage_key,
        JPEG,
        record.mime_type,
    )
    service._session.flush.assert_called_once_with()


@pytest.mark.parametrize(
    ("declared_size", "content"),
    ((5, JPEG), (4, b"jpeg"), (0, b"")),
)
def test_upload_rejects_size_or_magic_mismatch(
    declared_size: int,
    content: bytes,
) -> None:
    record = _file(content=JPEG)
    record.size_bytes = declared_size
    service = _service(record)

    with pytest.raises(AppError) as caught:
        service._put_file_content(
            actor=_actor(),
            file_id=record.id,
            content=content,
            request_id="persistent-media-invalid",
        )

    assert caught.value.code == FileErrorCode.INVALID_INPUT
    assert caught.value.status_code == 422
    service._storage.put_object.assert_not_called()


def test_uploader_can_read_integrity_checked_content() -> None:
    record = _file()
    service = _service(record)

    returned, content = service.get_file_content(actor=_actor(), file_id=record.id)

    assert returned is record
    assert content == JPEG


def test_related_work_order_reader_can_read_but_unrelated_actor_gets_not_found() -> None:
    record = _file(uploader_user_id=7)
    reader = _actor(user_id=9)
    work_order = WorkOrder()
    work_order.id = 5
    allowed = _service(record)
    allowed._repository.list_work_orders_for_file.return_value = (work_order,)
    allowed._authorize_read = MagicMock()

    _, content = allowed.get_file_content(actor=reader, file_id=record.id)

    assert content == JPEG
    allowed._authorize_read.assert_called_once_with(reader, work_order)

    denied = _service(record)
    denied._repository.list_work_orders_for_file.return_value = ()
    with pytest.raises(AppError) as caught:
        denied.get_file_content(actor=reader, file_id=record.id)
    assert caught.value.code == FileErrorCode.NOT_FOUND
    assert caught.value.status_code == 404
    denied._storage.get_object.assert_not_called()


def test_download_rejects_corrupted_persistent_object() -> None:
    record = _file()
    service = _service(record, content=b"\xff\xd8\xff\xe1")

    with pytest.raises(AppError) as caught:
        service.get_file_content(actor=_actor(), file_id=record.id)

    assert caught.value.code == FileErrorCode.STORAGE_UNAVAILABLE
    assert caught.value.status_code == 503


@pytest.mark.parametrize("scanner_status", ("pending", "blocked"))
def test_unscanned_or_blocked_content_is_not_readable(scanner_status: str) -> None:
    record = _file()
    record.scanner_status = scanner_status
    service = _service(record)

    with pytest.raises(AppError) as caught:
        service.get_file_content(actor=_actor(), file_id=record.id)

    assert caught.value.code == FileErrorCode.INVALID_INPUT
    assert caught.value.status_code == 409
    service._storage.get_object.assert_not_called()


def test_registered_metadata_without_content_cannot_be_attached() -> None:
    record = _file()
    service = _service(record)
    service._storage.get_object.side_effect = ProviderInvalidOutputError(
        "Object was not found"
    )
    work_order = WorkOrder()
    work_order.id = 5
    work_order.tenant_id = record.tenant_id
    work_order.property_id = record.property_id

    with pytest.raises(AppError) as caught:
        service._attach_media(
            work_order,
            actor=_actor(),
            file_ids=(record.id,),
            taken_phase=MediaTakenPhase.BEFORE,
        )

    assert caught.value.code == FileErrorCode.NOT_FOUND
    assert caught.value.status_code == 404
    service._repository.add_media.assert_not_called()


def test_integrity_checked_content_can_be_attached() -> None:
    record = _file()
    service = _service(record)
    work_order = WorkOrder()
    work_order.id = 5
    work_order.tenant_id = record.tenant_id
    work_order.property_id = record.property_id

    service._attach_media(
        work_order,
        actor=_actor(),
        file_ids=(record.id,),
        taken_phase=MediaTakenPhase.BEFORE,
    )

    service._repository.add_media.assert_called_once()


def test_miniprogram_has_authenticated_media_download_and_preview() -> None:
    api_source = (REPO_ROOT / "miniprogram/services/api.js").read_text(
        encoding="utf-8"
    )
    page_source = (
        REPO_ROOT / "miniprogram/pages/work-orders/detail.js"
    ).read_text(encoding="utf-8")
    template = (
        REPO_ROOT / "miniprogram/pages/work-orders/detail.wxml"
    ).read_text(encoding="utf-8")

    assert "wx.downloadFile" in api_source
    assert "Authorization" in api_source
    assert "previewMedia" in page_source
    assert "playMedia" in page_source
    assert "查看图片" in template
    assert "播放语音" in template
