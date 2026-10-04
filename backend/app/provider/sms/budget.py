"""Durable, fail-closed attempt gate for the bounded 8093 SMS acceptance run."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any

from redis import Redis


LIVE_BUDGET_KEY = "sms:live-budget:AUTH-REAL-SMS-20261005"
MAX_ATTEMPTS = 4
MAX_COST_MILLI_CNY = 300
PER_ATTEMPT_MILLI_CNY = 75


class SmsBudgetUnavailable(RuntimeError):
    """No real SMS call is permitted; the message contains no private data."""


_RESERVE = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local ok, value = pcall(cjson.decode, raw)
if not ok or type(value) ~= 'table' then return 0 end
if value.version ~= 1 or value.phone_digest ~= ARGV[1] then return 0 end
if value.max_attempts ~= 4 or value.max_cost_milli_cny ~= 300 then return 0 end
if type(value.attempts) ~= 'number' or type(value.reserved_milli_cny) ~= 'number' then return 0 end
if value.attempts < 0 or value.reserved_milli_cny < 0 then return 0 end
if value.state ~= 'ACTIVE' or value.attempts >= value.max_attempts then return 0 end
if value.reserved_milli_cny + 75 > value.max_cost_milli_cny then return 0 end
value.attempts = value.attempts + 1
value.reserved_milli_cny = value.reserved_milli_cny + 75
value.state = 'IN_FLIGHT'
value.in_flight = ARGV[2]
redis.call('SET', KEYS[1], cjson.encode(value))
return 1
"""

_FINISH = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local ok, value = pcall(cjson.decode, raw)
if not ok or type(value) ~= 'table' then return 0 end
if value.version ~= 1 or value.state ~= 'IN_FLIGHT' then return 0 end
if value.in_flight ~= ARGV[1] then return 0 end
value.state = 'ACTIVE'
value.in_flight = ''
redis.call('SET', KEYS[1], cjson.encode(value))
return 1
"""


class RedisSmsBudget:
    def __init__(
        self,
        *,
        client: Redis,
        allowed_phone_e164: str,
        app_secret: str,
        key: str = LIVE_BUDGET_KEY,
    ) -> None:
        if not re.fullmatch(r"\+861[3-9]\d{9}", allowed_phone_e164):
            raise SmsBudgetUnavailable("Live SMS gate is not configured")
        if not app_secret or not key:
            raise SmsBudgetUnavailable("Live SMS gate is not configured")
        self._client = client
        self._key = key
        self._phone_digest = hmac.new(
            app_secret.encode("utf-8"), allowed_phone_e164.encode("ascii"), hashlib.sha256
        ).hexdigest()
        self._allowed_phone = allowed_phone_e164

    def arm_once(self) -> bool:
        record = {
            "version": 1,
            "phone_digest": self._phone_digest,
            "max_attempts": MAX_ATTEMPTS,
            "max_cost_milli_cny": MAX_COST_MILLI_CNY,
            "attempts": 0,
            "reserved_milli_cny": 0,
            "state": "ACTIVE",
            "in_flight": "",
        }
        try:
            return bool(self._client.set(self._key, json.dumps(record), nx=True))
        except Exception as exc:
            raise SmsBudgetUnavailable("Live SMS budget is unavailable") from exc

    def reserve(self, phone_e164: str, request_id: str) -> None:
        if phone_e164 != self._allowed_phone or not request_id:
            raise SmsBudgetUnavailable("Live SMS send is unavailable")
        try:
            accepted = self._client.eval(_RESERVE, 1, self._key, self._phone_digest, request_id)
        except Exception as exc:
            raise SmsBudgetUnavailable("Live SMS budget is unavailable") from exc
        if accepted != 1:
            raise SmsBudgetUnavailable("Live SMS send is unavailable")

    def finish(self, request_id: str) -> None:
        if not request_id:
            raise SmsBudgetUnavailable("Live SMS budget is unavailable")
        try:
            accepted = self._client.eval(_FINISH, 1, self._key, request_id)
        except Exception as exc:
            raise SmsBudgetUnavailable("Live SMS budget is unavailable") from exc
        if accepted != 1:
            raise SmsBudgetUnavailable("Live SMS budget is unavailable")

    def snapshot(self) -> dict[str, int | str]:
        try:
            raw = self._client.get(self._key)
            value: Any = json.loads(raw) if raw else None
            if (
                not isinstance(value, dict)
                or value.get("version") != 1
                or value.get("phone_digest") != self._phone_digest
                or value.get("max_attempts") != MAX_ATTEMPTS
                or value.get("max_cost_milli_cny") != MAX_COST_MILLI_CNY
                or type(value.get("attempts")) is not int
                or type(value.get("reserved_milli_cny")) is not int
                or value.get("state") not in {"ACTIVE", "IN_FLIGHT", "PAUSED"}
            ):
                raise ValueError("invalid budget record")
        except Exception as exc:
            raise SmsBudgetUnavailable("Live SMS budget is unavailable") from exc
        return {
            "attempts": value["attempts"],
            "reserved_milli_cny": value["reserved_milli_cny"],
            "state": value["state"],
        }
