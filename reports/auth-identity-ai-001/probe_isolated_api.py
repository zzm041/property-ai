"""Live probe of the isolated auth API. Does not print OTP, tokens, or answers."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8083/api/v1"


def _request(path: str, payload: dict, request_id: str) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Request-Id": request_id,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as response:
            body = json.loads(response.read().decode("utf-8"))
            return response.status, body
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8"))
        return exc.code, body


def main() -> None:
    with urllib.request.urlopen(f"{BASE}/healthz") as response:
        health = json.loads(response.read().decode("utf-8"))
        print(f"healthz={response.status} status={health.get('status') or health.get('data')}")
    status, challenge = _request(
        "/auth/challenge",
        {"phone": "13900001111", "purpose": "login"},
        "live-ch",
    )
    data = challenge.get("data") or {}
    print(
        "challenge",
        status,
        "keys",
        sorted(data.keys()),
        "has_prompt",
        bool(data.get("prompt")),
        "has_code",
        "code" in data,
    )
    status, denied = _request(
        "/auth/sms/send",
        {
            "phone": "13900001111",
            "purpose": "login",
            "challenge_id": data.get("challenge_id") or "x" * 16,
            "challenge_answer": "99",
        },
        "live-bad",
    )
    error = (denied.get("error") or {}).get("code")
    print("wrong_challenge_send", status, error)


if __name__ == "__main__":
    main()
