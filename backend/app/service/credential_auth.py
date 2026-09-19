"""Official SMS/password credential authentication. Service-owned transactions."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
import secrets

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import run_owned_transaction
from app.core.errors import AppError, AuthErrorCode, ErrorCode
from app.core.passwords import hash_password, require_new_password, verify_password
from app.core.rate_limit import RateLimiter, RedisRateLimiter
from app.core.security import issue_access_token
from app.core.time import utc_now
from app.core.token_hash import generate_invite_token, generate_otp_code, generate_refresh_token, keyed_hash
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.iam import ScopeType
from app.domain.phone import mask_phone, normalize_phone
from app.model.identity import (
    AuthSession,
    PasswordResetTicket,
    SmsChallenge,
    User,
    UserPassword,
    UserPhone,
    VisualChallenge,
)
from app.provider.factory import build_sms_provider
from app.provider.sms import SmsProvider
from app.repository.credential import CredentialRepository
from app.repository.identity import IdentityRepository
from app.schema.auth import (
    SessionTokenData,
    SmsSendData,
    VisualChallengeData,
)
from app.service.audit import AuditService


@dataclass(frozen=True, slots=True)
class IssuedSession:
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    auth_method: str
    user_id: int
    session_id: int

    def as_schema(self) -> SessionTokenData:
        return SessionTokenData(
            access_token=self.access_token,
            refresh_token=self.refresh_token,
            token_type="bearer",
            expires_in=self.expires_in,
            auth_method=self.auth_method,  # type: ignore[arg-type]
            user_id=self.user_id,
        )


class CredentialAuthService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        repository: CredentialRepository | None = None,
        identity: IdentityRepository | None = None,
        audit: AuditService | None = None,
        sms_provider: SmsProvider | None = None,
        rate_limiter: RateLimiter | None = None,
        otp_factory: Callable[[], str] = generate_otp_code,
        clock=utc_now,
    ) -> None:
        self._session = session
        self._settings = settings
        self._repository = repository or CredentialRepository(session)
        self._identity = identity or IdentityRepository(session)
        self._audit = audit or AuditService(session)
        self._sms = sms_provider or build_sms_provider(settings)
        self._limiter = rate_limiter or RedisRateLimiter(settings.redis_url)
        self._otp_factory = otp_factory
        self._clock = clock

    def issue_visual_challenge(
        self, *, phone: str, purpose: str, request_id: str
    ) -> VisualChallengeData:
        del request_id
        phone_e164 = self._require_phone(phone)
        if purpose not in {"login", "reset"}:
            raise AppError(
                code=AuthErrorCode.INVALID_PHONE,
                message="Unsupported SMS purpose",
                status_code=422,
            )
        if not self._limiter.hit(
            f"visual:{phone_e164}:{purpose}",
            limit=self._settings.sms_send_limit_per_hour,
            window_seconds=3600,
        ):
            raise AppError(
                code=AuthErrorCode.SMS_RATE_LIMITED,
                message="Challenge issuing is rate limited",
                status_code=429,
            )
        left = secrets.randbelow(9) + 1
        right = secrets.randbelow(9) + 1
        prompt = f"{left} + {right} = ?"
        answer = str(left + right)
        token = generate_invite_token()
        now = self._clock()
        secret = self._settings.app_secret_key.get_secret_value()

        def _persist() -> None:
            self._repository.add_visual_challenge(
                VisualChallenge(
                    token_hash=keyed_hash(token, secret=secret),
                    phone_e164=phone_e164,
                    purpose=purpose,
                    prompt=prompt,
                    answer_hash=keyed_hash(answer, secret=secret),
                    expires_at=now + timedelta(seconds=120),
                    attempts=0,
                    max_attempts=5,
                )
            )

        try:
            run_owned_transaction(self._session, _persist)
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="Challenge could not be stored",
                status_code=500,
            ) from exc
        return VisualChallengeData(
            challenge_id=token, prompt=prompt, expires_in=120
        )

    def send_sms(
        self,
        *,
        phone: str,
        purpose: str,
        request_id: str,
        challenge_id: str,
        challenge_answer: str,
    ) -> SmsSendData:
        phone_e164 = self._require_phone(phone)
        if purpose not in {"login", "reset"}:
            raise AppError(
                code=AuthErrorCode.INVALID_PHONE,
                message="Unsupported SMS purpose",
                status_code=422,
            )
        if not challenge_id or not challenge_answer:
            raise AppError(
                code=AuthErrorCode.CHALLENGE_REQUIRED,
                message="A visual challenge is required",
                status_code=401,
            )
        send_key = f"sms:send:{phone_e164}:{purpose}"
        if not self._limiter.hit(
            send_key,
            limit=self._settings.sms_send_limit_per_hour,
            window_seconds=3600,
        ):
            raise AppError(
                code=AuthErrorCode.SMS_RATE_LIMITED,
                message="SMS sending is rate limited",
                status_code=429,
            )
        code = self._otp_factory()
        now = self._clock()
        expires = now + timedelta(seconds=self._settings.sms_otp_ttl_seconds)
        secret = self._settings.app_secret_key.get_secret_value()
        challenge_status = {"value": "ok"}

        def _persist() -> None:
            challenge_status["value"] = self._consume_visual_challenge(
                challenge_id=challenge_id,
                answer=challenge_answer,
                phone_e164=phone_e164,
                purpose=purpose,
            )
            if challenge_status["value"] != "ok":
                return
            existing = self._repository.get_open_challenge(phone_e164, purpose)
            if existing is not None:
                existing.consumed_at = now
                self._session.flush()
            challenge = SmsChallenge(
                phone_e164=phone_e164,
                purpose=purpose,
                code_hash=keyed_hash(code, secret=secret),
                expires_at=expires,
                attempts=0,
                max_attempts=self._settings.sms_otp_max_attempts,
            )
            self._repository.add_challenge(challenge)

        try:
            run_owned_transaction(self._session, _persist)
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="SMS challenge could not be stored",
                status_code=500,
            ) from exc
        self._raise_challenge(challenge_status["value"])
        try:
            sent = self._sms.send(phone_e164=phone_e164, purpose=purpose, code=code)
        except Exception as exc:
            raise AppError(
                code=AuthErrorCode.SMS_PROVIDER_DOWN,
                message="SMS provider is unavailable",
                status_code=503,
            ) from exc
        return SmsSendData(
            delivery_channel=sent.delivery_channel,
            provider_mode=sent.provider_mode,
            masked_phone=mask_phone(phone_e164),
            expires_in=self._settings.sms_otp_ttl_seconds,
        )

    def login_sms(self, *, phone: str, code: str, request_id: str) -> IssuedSession:
        phone_e164 = self._require_phone(phone)
        self._hit_login_limit(phone_e164)
        user_id_holder: dict[str, int] = {}
        refresh_holder: dict[str, str] = {}

        otp_status = {"value": "ok"}

        def _persist() -> None:
            otp_status["value"] = self._apply_otp(phone_e164, purpose="login", code=code)
            if otp_status["value"] != "ok":
                return
            phone_row = self._repository.get_active_phone(phone_e164)
            if phone_row is None:
                user = self._repository.add_user(
                    User(status="ACTIVE", display_name=mask_phone(phone_e164))
                )
                self._repository.add_phone(
                    UserPhone(
                        user_id=user.id,
                        phone_e164=phone_e164,
                        status="ACTIVE",
                        verified_at=self._clock(),
                    )
                )
                user_id = user.id
            else:
                user = self._identity.get_user(phone_row.user_id)
                if user is None or user.status != "ACTIVE":
                    raise AppError(
                        code=AuthErrorCode.USER_DISABLED,
                        message="User is not active",
                        status_code=401,
                    )
                user_id = user.id
            issued = self._issue_session(user_id=user_id, auth_method="sms")
            self._audit.append(
                action=AuditAction.AUTH_SMS_TOKEN_ISSUED,
                resource_type=AuditResourceType.USER,
                actor_user_id=user_id,
                resource_id=user_id,
                request_id=request_id,
                scope_type=ScopeType.SYSTEM,
                details={"auth_method": "sms", "token_type": "access"},
            )
            user_id_holder["user_id"] = issued.user_id
            refresh_holder["refresh"] = issued.refresh_token
            refresh_holder["access"] = issued.access_token
            user_id_holder["session_id"] = issued.session_id

        try:
            run_owned_transaction(self._session, _persist)
        except AppError:
            raise
        except IntegrityError as exc:
            raise AppError(
                code=AuthErrorCode.INVALID_PHONE,
                message="Phone number is already registered",
                status_code=409,
            ) from exc
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="SMS login could not be completed",
                status_code=500,
            ) from exc
        self._raise_otp(otp_status["value"])
        return IssuedSession(
            access_token=refresh_holder["access"],
            refresh_token=refresh_holder["refresh"],
            token_type="bearer",
            expires_in=self._settings.jwt_access_token_ttl_seconds,
            auth_method="sms",
            user_id=user_id_holder["user_id"],
            session_id=user_id_holder["session_id"],
        )

    def login_password(
        self, *, phone: str, password: str, request_id: str
    ) -> IssuedSession:
        phone_e164 = self._require_phone(phone)
        self._hit_login_limit(phone_e164)
        issued_box: list[IssuedSession] = []

        def _persist() -> None:
            phone_row = self._repository.get_active_phone(phone_e164)
            credential = None
            if phone_row is not None:
                credential = self._repository.get_password(phone_row.user_id)
            if (
                phone_row is None
                or credential is None
                or not verify_password(credential.password_hash, password)
            ):
                raise AppError(
                    code=AuthErrorCode.INVALID_PASSWORD,
                    message="Phone or password is incorrect",
                    status_code=401,
                )
            user = self._identity.get_user(phone_row.user_id)
            if user is None or user.status != "ACTIVE":
                raise AppError(
                    code=AuthErrorCode.USER_DISABLED,
                    message="User is not active",
                    status_code=401,
                )
            issued = self._issue_session(user_id=user.id, auth_method="password")
            self._audit.append(
                action=AuditAction.AUTH_PASSWORD_TOKEN_ISSUED,
                resource_type=AuditResourceType.USER,
                actor_user_id=user.id,
                resource_id=user.id,
                request_id=request_id,
                scope_type=ScopeType.SYSTEM,
                details={"auth_method": "password", "token_type": "access"},
            )
            issued_box.append(issued)

        try:
            run_owned_transaction(self._session, _persist)
        except AppError:
            raise
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="Password login could not be completed",
                status_code=500,
            ) from exc
        return issued_box[0]

    def change_password(
        self,
        *,
        user_id: int,
        old_password: str,
        new_password: str,
        request_id: str,
    ) -> IssuedSession:
        issued_box: list[IssuedSession] = []

        def _persist() -> None:
            credential = self._repository.get_password(user_id)
            if credential is None or not verify_password(
                credential.password_hash, old_password
            ):
                raise AppError(
                    code=AuthErrorCode.INVALID_PASSWORD,
                    message="Current password is incorrect",
                    status_code=401,
                )
            require_new_password(new_password)
            credential.password_hash = hash_password(new_password)
            credential.password_algo = "argon2id"
            self._revoke_sessions(user_id, reason="PASSWORD_CHANGE", request_id=request_id)
            issued = self._issue_session(user_id=user_id, auth_method="password")
            self._audit.append(
                action=AuditAction.AUTH_PASSWORD_CHANGED,
                resource_type=AuditResourceType.USER,
                actor_user_id=user_id,
                resource_id=user_id,
                request_id=request_id,
                scope_type=ScopeType.SYSTEM,
                details={"auth_method": "password"},
            )
            issued_box.append(issued)

        run_owned_transaction(self._session, _persist)
        return issued_box[0]

    def verify_password_reset(
        self, *, phone: str, code: str, request_id: str
    ) -> str:
        phone_e164 = self._require_phone(phone)
        otp_status = {"value": "ok"}
        token_box: list[str] = []
        secret = self._settings.app_secret_key.get_secret_value()

        def _persist() -> None:
            otp_status["value"] = self._apply_otp(phone_e164, purpose="reset", code=code)
            if otp_status["value"] != "ok":
                return
            phone_row = self._repository.get_active_phone(phone_e164)
            if phone_row is None:
                otp_status["value"] = "invalid"
                return
            raw = generate_invite_token()
            now = self._clock()
            self._repository.add_reset_ticket(
                PasswordResetTicket(
                    token_hash=keyed_hash(raw, secret=secret),
                    user_id=phone_row.user_id,
                    phone_e164=phone_e164,
                    expires_at=now + timedelta(seconds=600),
                )
            )
            token_box.append(raw)

        run_owned_transaction(self._session, _persist)
        self._raise_otp(otp_status["value"])
        del request_id
        return token_box[0]

    def confirm_password_reset(
        self,
        *,
        reset_token: str,
        new_password: str,
        confirm_password: str,
        request_id: str,
    ) -> None:
        if new_password != confirm_password:
            raise AppError(
                code=AuthErrorCode.PASSWORD_MISMATCH,
                message="New password confirmation does not match",
                status_code=422,
            )
        require_new_password(new_password)
        secret = self._settings.app_secret_key.get_secret_value()
        token_hash = keyed_hash(reset_token, secret=secret)
        status = {"value": "ok"}

        def _persist() -> None:
            ticket = self._repository.get_reset_ticket_for_update(token_hash)
            now = self._clock()
            if (
                ticket is None
                or ticket.consumed_at is not None
                or ticket.expires_at <= now
            ):
                status["value"] = "invalid"
                return
            ticket.consumed_at = now
            hashed = hash_password(new_password)
            credential = self._repository.get_password(ticket.user_id)
            if credential is None:
                self._repository.add_password(
                    UserPassword(
                        user_id=ticket.user_id,
                        password_hash=hashed,
                        password_algo="argon2id",
                    )
                )
            else:
                credential.password_hash = hashed
                credential.password_algo = "argon2id"
            self._revoke_sessions(
                ticket.user_id, reason="PASSWORD_RESET", request_id=request_id
            )
            self._audit.append(
                action=AuditAction.AUTH_PASSWORD_RESET,
                resource_type=AuditResourceType.USER,
                actor_user_id=ticket.user_id,
                resource_id=ticket.user_id,
                request_id=request_id,
                scope_type=ScopeType.SYSTEM,
                details={"auth_method": "password"},
            )

        run_owned_transaction(self._session, _persist)
        if status["value"] != "ok":
            raise AppError(
                code=AuthErrorCode.RESET_TOKEN_INVALID,
                message="Reset credential is invalid or expired",
                status_code=401,
            )

    def refresh(self, *, refresh_token: str, request_id: str) -> IssuedSession:
        issued_box: list[IssuedSession] = []
        secret = self._settings.app_secret_key.get_secret_value()
        token_hash = keyed_hash(refresh_token, secret=secret)

        def _persist() -> None:
            record = self._repository.get_session_by_refresh_hash(token_hash)
            now = self._clock()
            if (
                record is None
                or record.revoked_at is not None
                or record.expires_at <= now
            ):
                raise AppError(
                    code=AuthErrorCode.INVALID_TOKEN,
                    message="Refresh token is invalid",
                    status_code=401,
                )
            record.revoked_at = now
            issued = self._issue_session(
                user_id=record.user_id, auth_method=record.auth_method
            )
            record.replaced_by_id = issued.session_id
            issued_box.append(issued)

        try:
            run_owned_transaction(self._session, _persist)
        except AppError:
            raise
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="Token refresh failed",
                status_code=500,
            ) from exc
        return issued_box[0]

    def logout(self, *, session_id: int | None, refresh_token: str | None, request_id: str) -> None:
        secret = self._settings.app_secret_key.get_secret_value()

        def _persist() -> None:
            record = None
            if refresh_token:
                record = self._repository.get_session_by_refresh_hash(
                    keyed_hash(refresh_token, secret=secret)
                )
            elif session_id:
                record = self._repository.get_session_for_update(session_id)
            if record is None or record.revoked_at is not None:
                return
            record.revoked_at = self._clock()
            self._audit.append(
                action=AuditAction.AUTH_SESSION_REVOKED,
                resource_type=AuditResourceType.AUTH_SESSION,
                actor_user_id=record.user_id,
                resource_id=record.id,
                request_id=request_id,
                scope_type=ScopeType.SYSTEM,
                details={"auth_method": record.auth_method, "reason_code": "LOGOUT"},
            )

        run_owned_transaction(self._session, _persist)

    def update_profile(self, *, user_id: int, display_name: str, request_id: str) -> User:
        name = display_name.strip()
        if not name:
            raise AppError(
                code=AuthErrorCode.INVALID_USER,
                message="display_name is required",
                status_code=422,
            )
        holder: list[User] = []

        def _persist() -> None:
            user = self._identity.get_user(user_id)
            if user is None or user.status != "ACTIVE":
                raise AppError(
                    code=AuthErrorCode.USER_DISABLED,
                    message="User is not active",
                    status_code=401,
                )
            user.display_name = name
            self._audit.append(
                action=AuditAction.AUTH_PROFILE_UPDATED,
                resource_type=AuditResourceType.USER,
                actor_user_id=user.id,
                resource_id=user.id,
                request_id=request_id,
                scope_type=ScopeType.SYSTEM,
                details={},
            )
            holder.append(user)

        run_owned_transaction(self._session, _persist)
        return holder[0]

    def require_active_session(self, session_id: str, user_id: int) -> None:
        try:
            numeric = int(session_id)
        except (TypeError, ValueError) as exc:
            raise AppError(
                code=AuthErrorCode.INVALID_TOKEN,
                message="Access token is invalid",
                status_code=401,
            ) from exc
        record = self._repository.get_session(numeric)
        now = self._clock()
        if (
            record is None
            or record.user_id != user_id
            or record.revoked_at is not None
            or record.expires_at <= now
        ):
            raise AppError(
                code=AuthErrorCode.SESSION_REVOKED,
                message="Session is no longer valid",
                status_code=401,
            )

    def _issue_session(self, *, user_id: int, auth_method: str) -> IssuedSession:
        refresh = generate_refresh_token()
        now = self._clock()
        record = self._repository.add_session(
            AuthSession(
                user_id=user_id,
                auth_method=auth_method,
                refresh_token_hash=keyed_hash(
                    refresh, secret=self._settings.app_secret_key.get_secret_value()
                ),
                expires_at=now + timedelta(seconds=self._settings.jwt_refresh_token_ttl_seconds),
            )
        )
        access = issue_access_token(
            user_id,
            self._settings,
            auth_method=auth_method,
            session_id=str(record.id),
            now=now,
        )
        return IssuedSession(
            access_token=access,
            refresh_token=refresh,
            token_type="bearer",
            expires_in=self._settings.jwt_access_token_ttl_seconds,
            auth_method=auth_method,
            user_id=user_id,
            session_id=record.id,
        )

    def _revoke_sessions(self, user_id: int, *, reason: str, request_id: str) -> None:
        now = self._clock()
        for record in self._repository.list_active_sessions(user_id):
            record.revoked_at = now
            self._audit.append(
                action=AuditAction.AUTH_SESSION_REVOKED,
                resource_type=AuditResourceType.AUTH_SESSION,
                actor_user_id=user_id,
                resource_id=record.id,
                request_id=request_id,
                scope_type=ScopeType.SYSTEM,
                details={"auth_method": record.auth_method, "reason_code": reason},
            )

    def _apply_otp(self, phone_e164: str, *, purpose: str, code: str) -> str:
        challenge = self._repository.get_open_challenge(phone_e164, purpose)
        now = self._clock()
        if challenge is None:
            return "invalid"
        if challenge.expires_at <= now:
            challenge.consumed_at = now
            return "expired"
        if challenge.attempts >= challenge.max_attempts:
            challenge.consumed_at = now
            return "exhausted"
        expected = keyed_hash(code, secret=self._settings.app_secret_key.get_secret_value())
        if expected != challenge.code_hash:
            challenge.attempts += 1
            if challenge.attempts >= challenge.max_attempts:
                challenge.consumed_at = now
            return "invalid"
        challenge.consumed_at = now
        return "ok"

    def _consume_visual_challenge(
        self,
        *,
        challenge_id: str,
        answer: str,
        phone_e164: str,
        purpose: str,
    ) -> str:
        secret = self._settings.app_secret_key.get_secret_value()
        record = self._repository.get_visual_challenge_for_update(
            keyed_hash(challenge_id, secret=secret)
        )
        now = self._clock()
        if record is None:
            return "invalid"
        if (
            record.phone_e164 != phone_e164
            or record.purpose != purpose
            or record.consumed_at is not None
        ):
            return "invalid"
        if record.expires_at <= now:
            record.consumed_at = now
            return "expired"
        if record.attempts >= record.max_attempts:
            record.consumed_at = now
            return "exhausted"
        expected = keyed_hash(answer.strip(), secret=secret)
        if expected != record.answer_hash:
            record.attempts += 1
            if record.attempts >= record.max_attempts:
                record.consumed_at = now
            return "invalid"
        record.consumed_at = now
        return "ok"

    @staticmethod
    def _raise_challenge(status: str) -> None:
        if status == "ok":
            return
        raise AppError(
            code=AuthErrorCode.CHALLENGE_INVALID,
            message="Visual challenge is invalid or expired",
            status_code=401,
        )

    @staticmethod
    def _raise_otp(status: str) -> None:
        if status == "ok":
            return
        raise AppError(
            code=AuthErrorCode.INVALID_CODE,
            message="Verification code is invalid or expired",
            status_code=401,
        )

    def _hit_login_limit(self, phone_e164: str) -> None:
        if not self._limiter.hit(
            f"sms:login:{phone_e164}",
            limit=self._settings.sms_login_limit_per_hour,
            window_seconds=3600,
        ):
            raise AppError(
                code=AuthErrorCode.SMS_RATE_LIMITED,
                message="Login is rate limited",
                status_code=429,
            )

    @staticmethod
    def _require_phone(phone: str) -> str:
        normalized = normalize_phone(phone)
        if normalized is None:
            raise AppError(
                code=AuthErrorCode.INVALID_PHONE,
                message="Phone number is invalid",
                status_code=422,
            )
        return normalized
