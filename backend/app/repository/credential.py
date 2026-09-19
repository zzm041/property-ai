"""Credential persistence for phones, passwords, OTP challenges, and sessions."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.model.identity import (
    AuthSession,
    PasswordResetTicket,
    SmsChallenge,
    User,
    UserPassword,
    UserPhone,
    VisualChallenge,
)


class CredentialRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_user(self, user: User) -> User:
        self._session.add(user)
        self._session.flush()
        return user

    def get_active_phone(self, phone_e164: str) -> UserPhone | None:
        return self._session.scalar(
            select(UserPhone).where(
                UserPhone.phone_e164 == phone_e164,
                UserPhone.status == "ACTIVE",
            )
        )

    def get_active_phone_for_user(self, user_id: int) -> UserPhone | None:
        return self._session.scalar(
            select(UserPhone).where(
                UserPhone.user_id == user_id,
                UserPhone.status == "ACTIVE",
            )
        )

    def get_active_phone_for_update(self, phone_e164: str) -> UserPhone | None:
        return self._session.scalar(
            select(UserPhone)
            .where(
                UserPhone.phone_e164 == phone_e164,
                UserPhone.status == "ACTIVE",
            )
            .with_for_update()
        )

    def get_active_phone_for_user_for_update(self, user_id: int) -> UserPhone | None:
        return self._session.scalar(
            select(UserPhone)
            .where(
                UserPhone.user_id == user_id,
                UserPhone.status == "ACTIVE",
            )
            .with_for_update()
        )

    def add_phone(self, record: UserPhone) -> UserPhone:
        self._session.add(record)
        self._session.flush()
        return record

    def get_password(self, user_id: int) -> UserPassword | None:
        return self._session.scalar(
            select(UserPassword).where(UserPassword.user_id == user_id)
        )

    def add_password(self, record: UserPassword) -> UserPassword:
        self._session.add(record)
        self._session.flush()
        return record

    def get_open_challenge(self, phone_e164: str, purpose: str) -> SmsChallenge | None:
        return self._session.scalar(
            select(SmsChallenge)
            .where(
                SmsChallenge.phone_e164 == phone_e164,
                SmsChallenge.purpose == purpose,
                SmsChallenge.consumed_at.is_(None),
            )
            .with_for_update()
        )

    def add_challenge(self, record: SmsChallenge) -> SmsChallenge:
        self._session.add(record)
        self._session.flush()
        return record

    def add_visual_challenge(self, record: VisualChallenge) -> VisualChallenge:
        self._session.add(record)
        self._session.flush()
        return record

    def get_visual_challenge_for_update(self, token_hash: str) -> VisualChallenge | None:
        return self._session.scalar(
            select(VisualChallenge)
            .where(VisualChallenge.token_hash == token_hash)
            .with_for_update()
        )

    def add_reset_ticket(self, record: PasswordResetTicket) -> PasswordResetTicket:
        self._session.add(record)
        self._session.flush()
        return record

    def get_reset_ticket_for_update(self, token_hash: str) -> PasswordResetTicket | None:
        return self._session.scalar(
            select(PasswordResetTicket)
            .where(PasswordResetTicket.token_hash == token_hash)
            .with_for_update()
        )

    def add_session(self, record: AuthSession) -> AuthSession:
        self._session.add(record)
        self._session.flush()
        return record

    def get_session(self, session_id: int) -> AuthSession | None:
        return self._session.get(AuthSession, session_id)

    def get_session_by_refresh_hash(self, token_hash: str) -> AuthSession | None:
        return self._session.scalar(
            select(AuthSession)
            .where(AuthSession.refresh_token_hash == token_hash)
            .with_for_update()
        )

    def list_active_sessions(self, user_id: int) -> tuple[AuthSession, ...]:
        rows = self._session.scalars(
            select(AuthSession).where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
            )
        ).all()
        return tuple(rows)

    def get_session_for_update(self, session_id: int) -> AuthSession | None:
        return self._session.scalar(
            select(AuthSession)
            .where(AuthSession.id == session_id)
            .with_for_update()
        )
