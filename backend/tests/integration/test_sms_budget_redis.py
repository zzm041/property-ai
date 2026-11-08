"""Bounded live-SMS budget against a disposable key in the isolated Redis."""

import importlib
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from redis import Redis


def _budget_module():
    return importlib.import_module("app.provider.sms.budget")


@pytest.fixture
def budget_client():
    url = os.getenv("PROPERTY_AI_SMS_BUDGET_TEST_REDIS_URL")
    if not url:
        pytest.skip("isolated Redis test URL is not set")
    client = Redis.from_url(url, decode_responses=True, socket_timeout=2)
    key = f"sms:live-budget:test:{uuid4().hex}"
    try:
        yield client, key
    finally:
        client.delete(key)
        client.close()


def test_budget_is_atomic_persistent_and_never_rearmed(budget_client):
    module = _budget_module()
    client, key = budget_client
    phone = "+8613800000000"
    make = lambda: module.RedisSmsBudget(
        client=client,
        allowed_phone_e164=phone,
        app_secret="test-only-budget-secret",
        key=key,
    )
    budget = make()
    assert budget.arm_once() is True
    assert budget.arm_once() is False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: _try_reserve(make(), phone, f"parallel-{i}"), range(8)))
    assert results.count("reserved") == 1
    assert results.count("blocked") == 7
    assert make().snapshot() == {"attempts": 1, "reserved_milli_cny": 75, "state": "IN_FLIGHT"}

    winner = next(f"parallel-{i}" for i, result in enumerate(results) if result == "reserved")
    with pytest.raises(module.SmsBudgetUnavailable):
        make().finish("wrong-request")
    make().finish(winner)
    for number in range(2, 5):
        fresh = make()  # New process/client object sees the same durable key.
        fresh.reserve(phone, f"definite-failure-{number}")
        fresh.finish(f"definite-failure-{number}")
    assert make().snapshot() == {"attempts": 4, "reserved_milli_cny": 300, "state": "ACTIVE"}
    with pytest.raises(module.SmsBudgetUnavailable):
        make().reserve(phone, "fifth")
    assert make().arm_once() is False
    assert make().snapshot()["attempts"] == 4


def test_budget_fails_closed_for_wrong_phone_unknown_result_and_lost_state(budget_client):
    module = _budget_module()
    client, key = budget_client
    budget = module.RedisSmsBudget(
        client=client,
        allowed_phone_e164="+8613800000000",
        app_secret="test-only-budget-secret",
        key=key,
    )
    with pytest.raises(module.SmsBudgetUnavailable):
        budget.reserve("+8613800000000", "missing")
    assert budget.arm_once() is True
    with pytest.raises(module.SmsBudgetUnavailable):
        budget.reserve("+8613900000000", "wrong-phone")
    budget.reserve("+8613800000000", "unknown")
    with pytest.raises(module.SmsBudgetUnavailable):
        budget.reserve("+8613800000000", "after-unknown")
    assert budget.snapshot() == {"attempts": 1, "reserved_milli_cny": 75, "state": "IN_FLIGHT"}
    client.delete(key)
    with pytest.raises(module.SmsBudgetUnavailable):
        budget.reserve("+8613800000000", "after-loss")
    client.set(key, "not-json")
    with pytest.raises(module.SmsBudgetUnavailable):
        budget.reserve("+8613800000000", "malformed")


def _try_reserve(budget, phone: str, request_id: str) -> str:
    module = _budget_module()
    try:
        budget.reserve(phone, request_id)
    except module.SmsBudgetUnavailable:
        return "blocked"
    return "reserved"
