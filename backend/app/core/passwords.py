"""Argon2id password hashing and the current new-password policy.

Current new/reset password policy (operator decision 2026-09-19):
- at least 8 Unicode code points (minimum length, not a fixed width of 8)
- uppercase is not required; lowercase+digit (e.g. goodpass1) is accepted
- reject ASCII-all-digit and ASCII-all-lowercase-letter passwords
- hash and verify the original input; never lower/casefold before hashing
- different casing is a different password; historical hashes stay exact-match
- login of an existing correct hash must not apply this new-password policy
- length and reject classes are shared with miniprogram/utils/password.js

Do not add extra character-class rules without a new operator decision.
Do not record this rule as formal Production V1 acceptance.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.errors import AppError, AuthErrorCode

_HASHER = PasswordHasher()
NEW_PASSWORD_MIN_LENGTH = 8
NEW_PASSWORD_MAX_LENGTH = 128


def code_point_length(password: str) -> int:
    """Count Unicode scalar values, not UTF-16 units."""

    return len(password)


def is_ascii_digits_only(password: str) -> bool:
    return bool(password) and all("0" <= item <= "9" for item in password)


def is_ascii_lowercase_letters_only(password: str) -> bool:
    return bool(password) and all("a" <= item <= "z" for item in password)


def hash_password(password: str) -> str:
    """Hash the original input. Do not lower or casefold first."""

    if not password or not isinstance(password, str):
        raise ValueError("password is required")
    return _HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Compare the original input to the stored hash. Casing differences fail."""

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
    if any("a" <= item <= "z" for item in password):
        classes += 1
    if any("A" <= item <= "Z" for item in password):
        classes += 1
    if any("0" <= item <= "9" for item in password):
        classes += 1
    if any(not item.isalnum() for item in password):
        classes += 1
    length = code_point_length(password)
    if (
        length < NEW_PASSWORD_MIN_LENGTH
        or is_ascii_digits_only(password)
        or classes < 2
    ):
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
    length = code_point_length(password)
    if length < NEW_PASSWORD_MIN_LENGTH or length > NEW_PASSWORD_MAX_LENGTH:
        raise AppError(
            code=AuthErrorCode.WEAK_PASSWORD,
            message="New password does not meet the current policy",
            status_code=422,
        )
    if is_ascii_digits_only(password) or is_ascii_lowercase_letters_only(password):
        raise AppError(
            code=AuthErrorCode.WEAK_PASSWORD,
            message="New password does not meet the current policy",
            status_code=422,
        )
