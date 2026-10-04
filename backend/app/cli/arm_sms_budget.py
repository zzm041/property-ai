"""One-time, explicit arming of the bounded staging SMS acceptance budget."""

from __future__ import annotations

import argparse

from redis import Redis

from app.core.config import Settings, get_settings
from app.provider.sms.budget import RedisSmsBudget


APPROVAL_TOKEN = "AUTH-REAL-SMS-20261005"


def main(*, settings: Settings, client: Redis, approval: str) -> int:
    if approval != APPROVAL_TOKEN:
        raise RuntimeError("Explicit SMS budget approval is required")
    if settings.app_env != "staging":
        raise RuntimeError("SMS budget may only be armed in staging")
    if client.config_get("appendonly").get("appendonly") != "yes":
        raise RuntimeError("Redis appendonly must be enabled")
    if client.config_get("appendfsync").get("appendfsync") != "always":
        raise RuntimeError("Redis appendfsync must be always")
    budget = RedisSmsBudget(
        client=client,
        allowed_phone_e164=settings.sms_live_allowed_phone_e164.get_secret_value(),
        app_secret=settings.app_secret_key.get_secret_value(),
    )
    if not budget.arm_once():
        raise RuntimeError("SMS budget already exists; refusing to reset it")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Arm the one-time bounded staging SMS budget")
    parser.add_argument("--approval", required=True)
    args = parser.parse_args()
    runtime_settings = get_settings()
    redis_client = Redis.from_url(runtime_settings.redis_url, decode_responses=True)
    raise SystemExit(main(settings=runtime_settings, client=redis_client, approval=args.approval))
