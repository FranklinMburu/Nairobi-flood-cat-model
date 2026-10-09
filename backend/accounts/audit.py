from .models import AuditEvent

USER_CREATED = "user_created"
USER_DEACTIVATED = "user_deactivated"
USER_REACTIVATED = "user_reactivated"
LOGIN_SUCCEEDED = "login_succeeded"
LOGIN_FAILED = "login_failed"
PASSWORD_ACCEPTED = "password_accepted"
OTP_REQUESTED = "otp_requested"
OTP_SENT = "otp_sent"
OTP_RESENT = "otp_resent"
OTP_VERIFIED = "otp_verified"
OTP_FAILED = "otp_failed"
OTP_EXPIRED = "otp_expired"
SECURITY_SETTINGS_CHANGED = "security_settings_changed"


def record(action, subject_email="", actor=None, detail=""):
    AuditEvent.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        action=action,
        subject_email=(subject_email or "")[:254],
        detail=(detail or "")[:64],
    )
