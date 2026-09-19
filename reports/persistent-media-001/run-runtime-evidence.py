"""Prepare and verify persistent media through official AUTH001 business APIs.

The local credential fixture is read only to establish an official password
session. No credential, phone, token, resident name, or file bytes are emitted.
"""

from __future__ import annotations

import argparse
import base64
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.request
from uuid import uuid4


REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8083/api/v1"
ACCOUNTS_PATH = REPO / "reports" / "mp-current-full-regression" / "accounts.local.json"
PUBLIC_PATH = REPO / "reports" / "mp-current-full-regression" / "accounts-public.json"
STATE_PATH = OUT / "runtime-state.json"
IMAGE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _json_request(
    path: str,
    payload: dict[str, object] | None,
    *,
    request_id: str,
    token: str | None = None,
    idempotency_key: str | None = None,
) -> tuple[int, dict[str, object]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json", "X-Request-Id": request_id}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=body,
        headers=headers,
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error = json.loads(exc.read().decode("utf-8"))
        code = error.get("error", {}).get("code", "UNKNOWN")
        raise RuntimeError(f"{path} returned HTTP {exc.code}: {code}") from exc


def _login() -> str:
    accounts = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    _, response = _json_request(
        "/auth/password/login",
        {
            "phone": accounts["phones"]["resident"],
            "password": accounts["password"],
        },
        request_id=f"persistent-media-login-{uuid4().hex[:12]}",
    )
    return str(response["data"]["access_token"])


def _upload(
    file_id: int,
    token: str,
    run_id: str,
    *,
    content: bytes = IMAGE,
) -> tuple[int, str | None]:
    boundary = f"----persistent-media-{uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="persistent-media-001.png"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        f"{BASE}/files/{file_id}/content",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
            "X-Request-Id": f"{run_id}-upload",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read()
            return response.status, None
    except urllib.error.HTTPError as exc:
        error = json.loads(exc.read().decode("utf-8"))
        code = error.get("error", {}).get("code", "UNKNOWN")
        return exc.code, str(code)


def _download(file_id: int, token: str, run_id: str) -> tuple[int, bytes, str]:
    request = urllib.request.Request(
        f"{BASE}/files/{file_id}/content",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Request-Id": f"{run_id}-download",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read(), response.headers.get_content_type()
    except urllib.error.HTTPError as exc:
        error = json.loads(exc.read().decode("utf-8"))
        code = error.get("error", {}).get("code", "UNKNOWN")
        raise RuntimeError(f"download returned HTTP {exc.code}: {code}") from exc


def _head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
    ).strip()


def prepare() -> int:
    public = json.loads(PUBLIC_PATH.read_text(encoding="utf-8"))
    token = _login()
    run_id = f"persistent-media-{uuid4().hex[:12]}"
    registered_status, registered = _json_request(
        "/files",
        {
            "mime_type": "image/png",
            "size_bytes": len(IMAGE),
            "original_name": "persistent-media-001.png",
            "property_id": public["org"]["property_id"],
        },
        request_id=f"{run_id}-register",
        token=token,
    )
    file_id = int(registered["data"]["id"])
    upload_status, upload_error = _upload(file_id, token, run_id)
    if upload_error:
        raise RuntimeError(f"upload returned HTTP {upload_status}: {upload_error}")
    create_status, created = _json_request(
        "/work-orders",
        {
            "house_id": public["org"]["house_id"],
            "text": "持久附件跨进程读取验证（合成测试数据）",
            "file_ids": [file_id],
        },
        request_id=f"{run_id}-create",
        token=token,
        idempotency_key=f"{run_id}-one-order",
    )
    work_order_id = int(created["data"]["id"])
    detail_status, detail = _json_request(
        f"/work-orders/{work_order_id}",
        None,
        request_id=f"{run_id}-detail",
        token=token,
    )
    download_status, content, content_type = _download(file_id, token, run_id)
    media_ids = [int(item["file_id"]) for item in detail["data"].get("media", [])]
    digest = sha256(IMAGE).hexdigest()
    state = {
        "task_id": "PERSISTENT-MEDIA-001",
        "head": _head(),
        "file_id": file_id,
        "work_order_id": work_order_id,
        "expected_sha256": digest,
        "expected_size": len(IMAGE),
        "expected_mime": "image/png",
    }
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    evidence = {
        **state,
        "phase": "prepare_before_api_restart",
        "environment": "8083 -> property_ai_auth001_test @ 20260919_0009",
        "session": "official password login",
        "register_status": registered_status,
        "upload_status": upload_status,
        "create_status": create_status,
        "detail_status": detail_status,
        "download_status": download_status,
        "content_type": content_type,
        "download_sha256_matches": sha256(content).hexdigest() == digest,
        "download_size_matches": len(content) == len(IMAGE),
        "detail_links_file": file_id in media_ids,
        "not_claimed": ["real VLM", "production storage", "Production V1"],
        "redaction": "No credential, phone, token, resident name, or file bytes are stored.",
    }
    (OUT / "prepare-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence, ensure_ascii=False))
    return 0 if all(
        (
            registered_status == 200,
            upload_status == 200,
            create_status == 200,
            detail_status == 200,
            download_status == 200,
            evidence["download_sha256_matches"],
            evidence["download_size_matches"],
            evidence["detail_links_file"],
        )
    ) else 2


def verify() -> int:
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    token = _login()
    run_id = f"persistent-media-verify-{uuid4().hex[:12]}"
    download_status, content, content_type = _download(state["file_id"], token, run_id)
    detail_status, detail = _json_request(
        f"/work-orders/{state['work_order_id']}",
        None,
        request_id=f"{run_id}-detail",
        token=token,
    )
    media_ids = [int(item["file_id"]) for item in detail["data"].get("media", [])]
    evidence = {
        **state,
        "phase": "fresh_official_session_after_api_restart",
        "head_after_restart": _head(),
        "code_changed_after_prepare": state["head"] != _head(),
        "session": "new official password login",
        "download_status": download_status,
        "content_type": content_type,
        "download_sha256_matches": sha256(content).hexdigest()
        == state["expected_sha256"],
        "download_size_matches": len(content) == state["expected_size"],
        "detail_status": detail_status,
        "detail_links_same_file": state["file_id"] in media_ids,
        "work_order_status": detail["data"]["work_order"]["status"],
        "not_claimed": ["real VLM", "production storage", "Production V1"],
        "redaction": "No credential, phone, token, resident name, or file bytes are stored.",
    }
    (OUT / "runtime-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence, ensure_ascii=False))
    return 0 if all(
        (
            download_status == 200,
            content_type == state["expected_mime"],
            evidence["download_sha256_matches"],
            evidence["download_size_matches"],
            detail_status == 200,
            evidence["detail_links_same_file"],
        )
    ) else 2


def verify_immutable() -> int:
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    token = _login()
    run_id = f"persistent-media-immutable-{uuid4().hex[:12]}"
    retry_status, retry_error = _upload(state["file_id"], token, run_id)
    replacement = b"\x89PNG\r\n\x1a\n" + (b"replacement" * 6)
    replacement = replacement[: state["expected_size"]].ljust(
        state["expected_size"], b"x"
    )
    replace_status, replace_error = _upload(
        state["file_id"],
        token,
        f"{run_id}-replace",
        content=replacement,
    )
    download_status, content, content_type = _download(
        state["file_id"], token, run_id
    )
    evidence = {
        "task_id": "PERSISTENT-MEDIA-001-IMMUTABLE",
        "head": _head(),
        "file_id": state["file_id"],
        "same_content_retry_status": retry_status,
        "same_content_retry_error": retry_error,
        "different_content_status": replace_status,
        "different_content_error": replace_error,
        "readback_status": download_status,
        "readback_content_type": content_type,
        "original_sha256_preserved": sha256(content).hexdigest()
        == state["expected_sha256"],
        "not_claimed": ["real VLM", "production storage", "Production V1"],
        "redaction": "No credential, phone, token, resident name, replacement bytes, or original bytes are stored.",
    }
    (OUT / "immutability-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence, ensure_ascii=False))
    return 0 if all(
        (
            retry_status == 200,
            retry_error is None,
            replace_status == 409,
            replace_error == "FILE.CONFLICT",
            download_status == 200,
            content_type == state["expected_mime"],
            evidence["original_sha256_preserved"],
        )
    ) else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "verify", "verify-immutable"))
    args = parser.parse_args()
    if args.phase == "prepare":
        return prepare()
    if args.phase == "verify":
        return verify()
    return verify_immutable()


if __name__ == "__main__":
    raise SystemExit(main())
