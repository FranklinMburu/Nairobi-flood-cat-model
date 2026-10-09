"""One-time sign-in challenges. The Django session is created only after the code is accepted."""

import secrets
import uuid
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from .audit import (
    OTP_EXPIRED,
    OTP_FAILED,
    OTP_REQUESTED,
    OTP_RESENT,
    OTP_SENT,
    OTP_VERIFIED,
    PASSWORD_ACCEPTED,
    record,
)
from .delivery import deliver
from .models import OtpChallenge, SecuritySettings

CODE_TTL = timedelta(minutes=5)
CHALLENGE_TTL = timedelta(minutes=30)
RESEND_COOLDOWN = timedelta(seconds=60)
MAX_ATTEMPTS = 5


class OtpError(Exception):
    def __init__(self, message, status=400, extra=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.extra = extra or {}


def generate_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def configured_methods(config):
    methods = []
    if config.email_otp_enabled:
        methods.append("email")
    if config.sms_otp_enabled:
        methods.append("sms")
    return methods


def usable_methods(config, user):
    methods = configured_methods(config)
    if "sms" in methods and not (user.phone or "").strip():
        methods = [method for method in methods if method != "sms"]
    return methods


def mask_email(email):
    local, separator, domain = email.partition("@")
    if not separator:
        return "***"
    visible = local[0] if local else "*"
    return f"{visible}***@{domain}"


def mask_phone(phone):
    digits = "".join(character for character in (phone or "") if character.isdigit())
    last = digits[-3:] if digits else ""
    if (phone or "").strip().startswith("+") and len(digits) > 3:
        return f"+{digits[:3]} *** *** {last}"
    return f"*** *** {last}"


def challenge_payload(challenge, methods):
    body = {
        "requires_2fa": True,
        "challenge_id": str(challenge.id),
        "methods": methods,
    }
    if "email" in methods:
        body["masked_email"] = mask_email(challenge.user.email)
    if "sms" in methods:
        body["masked_phone"] = mask_phone(challenge.user.phone)
    if challenge.method and challenge.code_hash:
        body["method"] = challenge.method
        body["retry_after"] = int(RESEND_COOLDOWN.total_seconds())
    return body


def begin_challenge(user):
    """Return a pending payload, or None when second-factor verification is off.

    Does not create a session.
    """
    config = SecuritySettings.load()
    configured = configured_methods(config)
    if not configured:
        return None
    if configured == ["sms"] and not (user.phone or "").strip():
        raise OtpError(
            "SMS verification is required, but this account has no phone number. Contact your administrator.",
            status=400,
        )

    methods = usable_methods(config, user)
    OtpChallenge.objects.filter(user=user, consumed_at__isnull=True, invalidated=False).update(
        invalidated=True,
        code_hash="",
    )
    challenge = OtpChallenge.objects.create(user=user)
    record(PASSWORD_ACCEPTED, subject_email=user.email, actor=user)
    record(OTP_REQUESTED, subject_email=user.email, actor=user, detail=",".join(methods))
    if len(methods) == 1:
        send_code(challenge, methods[0])
        challenge.refresh_from_db()
    return challenge_payload(challenge, methods)


def send_code(challenge, method, *, resend=False):
    method = (method or "").strip().lower()
    with transaction.atomic():
        locked = OtpChallenge.objects.select_for_update().select_related("user").get(pk=challenge.pk)
        _ensure_open(locked)
        methods = usable_methods(SecuritySettings.load(), locked.user)
        if method not in methods:
            raise OtpError("That verification method is not available.")
        already_sent = bool(locked.sent_at and locked.code_hash)
        if already_sent or resend:
            _enforce_cooldown(locked)
        code = generate_code()
        locked.code_hash = make_password(code)
        locked.method = method
        locked.attempts = 0
        locked.code_expires_at = timezone.now() + CODE_TTL
        locked.sent_at = timezone.now()
        locked.save(
            update_fields=["code_hash", "method", "attempts", "code_expires_at", "sent_at"]
        )
    try:
        deliver(method, locked.user, code)
    except Exception:
        locked.invalidated = True
        locked.code_hash = ""
        locked.save(update_fields=["invalidated", "code_hash"])
        raise OtpError("The verification code could not be sent. Try again later.", status=503) from None
    record(
        OTP_RESENT if resend or already_sent else OTP_SENT,
        subject_email=locked.user.email,
        actor=locked.user,
        detail=method,
    )
    return locked


def verify_code(challenge_id, code, method):
    parsed = _parse_challenge_id(challenge_id)
    method = (method or "").strip().lower()
    if method not in {"email", "sms"}:
        raise OtpError("Invalid verification code.")
    failure = None
    user = None
    with transaction.atomic():
        try:
            challenge = OtpChallenge.objects.select_for_update().select_related("user").get(pk=parsed)
        except OtpChallenge.DoesNotExist:
            failure = OtpError("Invalid verification code.")
        else:
            failure, user = _check_code(challenge, code, method)
    if failure is not None:
        raise failure
    record(OTP_VERIFIED, subject_email=user.email, actor=user, detail=method)
    return user


def _check_code(challenge, code, method):
    """Return (error, user). Writes inside the open transaction must not raise."""
    if challenge.invalidated or challenge.consumed_at or not challenge.code_hash:
        return OtpError("Invalid verification code."), None
    if challenge.created_at < timezone.now() - CHALLENGE_TTL:
        return OtpError("This verification session is no longer valid. Sign in again."), None
    if challenge.method != method:
        return OtpError("Invalid verification code."), None
    if not challenge.code_expires_at or challenge.code_expires_at <= timezone.now():
        record(OTP_EXPIRED, subject_email=challenge.user.email, actor=challenge.user, detail=method)
        return OtpError("This verification code has expired. Request a new code."), None
    if challenge.attempts >= MAX_ATTEMPTS:
        return OtpError("Too many attempts. Request a new verification code."), None
    if not _code_matches(challenge, code):
        challenge.attempts += 1
        challenge.save(update_fields=["attempts"])
        record(OTP_FAILED, subject_email=challenge.user.email, actor=challenge.user, detail=method)
        if challenge.attempts >= MAX_ATTEMPTS:
            return OtpError("Too many attempts. Request a new verification code."), None
        return OtpError("Invalid verification code."), None
    if not challenge.user.is_active:
        return OtpError("Your account is inactive. Contact your administrator.", status=403), None
    challenge.consumed_at = timezone.now()
    challenge.code_hash = ""
    challenge.save(update_fields=["consumed_at", "code_hash"])
    return None, challenge.user


def load_challenge(challenge_id):
    parsed = _parse_challenge_id(challenge_id)
    try:
        challenge = OtpChallenge.objects.select_related("user").get(pk=parsed)
    except OtpChallenge.DoesNotExist:
        raise OtpError("This verification session is no longer valid. Sign in again.") from None
    _ensure_open(challenge)
    return challenge


def _ensure_open(challenge):
    if challenge.invalidated or challenge.consumed_at:
        raise OtpError("This verification session is no longer valid. Sign in again.")
    if challenge.created_at < timezone.now() - CHALLENGE_TTL:
        raise OtpError("This verification session is no longer valid. Sign in again.")


def _enforce_cooldown(challenge):
    if not challenge.sent_at:
        return
    elapsed = timezone.now() - challenge.sent_at
    if elapsed >= RESEND_COOLDOWN:
        return
    remaining = int((RESEND_COOLDOWN - elapsed).total_seconds()) or 1
    raise OtpError(
        "Wait before requesting another code.",
        status=429,
        extra={"retry_after": remaining},
    )


def _code_matches(challenge, code):
    if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
        return False
    return check_password(code, challenge.code_hash)


def _parse_challenge_id(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise OtpError("Invalid verification code.") from None
