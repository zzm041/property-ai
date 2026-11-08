"""Development token request accepts seed_key or user_id, not both."""

import pytest
from pydantic import ValidationError

from app.schema.auth import DevelopmentTokenRequest


def test_user_id_only_is_valid() -> None:
    body = DevelopmentTokenRequest(user_id=4)
    assert body.user_id == 4
    assert body.seed_key is None


def test_seed_key_only_is_valid() -> None:
    body = DevelopmentTokenRequest(seed_key="__DEV_RESIDENT__")
    assert body.seed_key == "__DEV_RESIDENT__"
    assert body.user_id is None


def test_both_identifiers_are_rejected() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        DevelopmentTokenRequest(user_id=4, seed_key="__DEV_RESIDENT__")


def test_missing_identifier_is_rejected() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        DevelopmentTokenRequest()
