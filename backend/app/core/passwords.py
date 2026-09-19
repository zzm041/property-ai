"""Argon2id password hashing and the current new-password policy.

Interim policy pending operator confirmation:
- at least 8 characters (not a fixed width of 8, not digits-only)
- case-sensitive; digits and symbols allowed
- no silent truncation or case folding
- login of an existing correct hash must not apply this policy

Not yet decided: longer minimum, mandatory character classes.
Do not record this interim rule as formal Production V1 acceptance.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.errors import AppError, AuthErrorCode

_HASHER = PasswordHasher()
NEW_PASSWORD_MIN_LENGTH = 8
NEW_PASSWORD_MAX_LENGTH = 128


def hash_password(password: str) -> str:
    if not password or not isinstance(password, str):
        raise ValueError("password is required")
    return _HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    if not password_hash or not password:
        return False
    try:
        return bool(_HASHER.verify(password_hash, password))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_strength(password: str) -> str:
    """Hint label from the actual input. Never a fixed '强' at length 8."""

    if not password:
        return "未输入"
    classes = 0
    if any(item.islower() for item in password):
        classes += 1
    if any(item.isupper() for item in password):
        classes += 1
    if any(item.isdigit() for item in password):
        classes += 1
    if any(not item.isalnum() for item in password):
        classes += 1
    length = len(password)
    if length < NEW_PASSWORD_MIN_LENGTH or password.isdigit() or classes < 2:
        return "偏弱"
    if length >= 12 and classes >= 3:
        return "较强"
    return "可用"


def require_new_password(password: str) -> None:
    if not isinstance(password, str):
        raise AppError(
            code=AuthErrorCode.WEAK_PASSWORD,
            message="New password does not meet the current policy",
            status_code=422,
        )
    if len(password) < NEW_PASSWORD_MIN_LENGTH or len(password) > NEW_PASSWORD_MAX_LENGTH:
        raise AppError(
            code=AuthErrorCode.WEAK_PASSWORD,
            message="New password does not meet the current policy",
            status_code=422,
        )
    if password.isdigit():
        raise AppError(
            code=AuthErrorCode.WEAK_PASSWORD,
            message="New password does not meet the current policy",
            status_code=422,
        )
    if password.lower() == password and password.isalpha():
        raise AppError(
            code=AuthErrorCode.WEAK_PASSWORD,
            message="New password does not meet the current policy",
            status_code=422,
        )
