"""Staff-only account administration. Loss calculations stay in loss_engine."""

import secrets

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_http_methods

from .audit import SECURITY_SETTINGS_CHANGED, USER_CREATED, USER_DEACTIVATED, USER_REACTIVATED, record
from .models import AuditEvent, SecuritySettings, User
from .permissions import admin_required
from .views import read_json, text_field

ROLES = {"user": False, "administrator": True}
PHONE_MIN = 7
PHONE_MAX = 20


def role_name(user):
    return "administrator" if user.is_staff else "user"


def iso(value):
    return value.isoformat() if value else None


def account_payload(user):
    return {
        "id": user.pk,
        "name": user.name,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "phone": user.phone,
        "role": role_name(user),
        "is_active": user.is_active,
        "is_staff": user.is_staff,
        "last_login": iso(user.last_login),
        "created_at": iso(user.created_at),
    }


def clean_phone(value):
    phone = " ".join(value.split())
    digits = [character for character in phone if character.isdigit()]
    allowed = set("0123456789+ -()")
    if not phone or any(character not in allowed for character in phone):
        return None
    if phone.count("+") > 1 or ("+" in phone and not phone.startswith("+")):
        return None
    if not PHONE_MIN <= len(digits) <= PHONE_MAX:
        return None
    return phone


def temporary_password(user):
    """Issued once to the creating administrator. Not stored or logged."""
    for _ in range(5):
        password = "Aa1-" + secrets.token_urlsafe(12)
        try:
            validate_password(password, user=user)
        except ValidationError:
            continue
        return password
    return None


@admin_required
@require_GET
def stats(_request):
    return JsonResponse(
        {
            "total_users": User.objects.count(),
            "active_users": User.objects.filter(is_active=True).count(),
            "inactive_users": User.objects.filter(is_active=False).count(),
            "administrators": User.objects.filter(is_staff=True).count(),
        }
    )


@admin_required
@require_http_methods(["GET", "POST"])
def users(request):
    if request.method == "GET":
        rows = User.objects.order_by("-created_at", "-id")
        return JsonResponse({"count": rows.count(), "users": [account_payload(user) for user in rows]})
    return create_user(request)


def create_user(request):
    data = read_json(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    first_name = text_field(data, "first_name")
    last_name = text_field(data, "last_name")
    email = text_field(data, "email").lower()
    phone = clean_phone(text_field(data, "phone"))
    role = text_field(data, "role").lower()

    if not first_name:
        return JsonResponse({"error": "First name is required."}, status=400)
    if not last_name:
        return JsonResponse({"error": "Last name is required."}, status=400)
    if len(first_name) > 60 or len(last_name) > 60:
        return JsonResponse({"error": "Name is too long."}, status=400)
    if not email:
        return JsonResponse({"error": "Email is required."}, status=400)
    try:
        validate_email(email)
    except ValidationError:
        return JsonResponse({"error": "Enter a valid email address."}, status=400)
    if phone is None:
        return JsonResponse({"error": "Enter a valid phone number."}, status=400)
    if role not in ROLES:
        return JsonResponse({"error": "Role must be User or Administrator."}, status=400)
    if User.objects.filter(email=email).exists():
        return JsonResponse({"error": "An account with this email already exists."}, status=400)

    name = f"{first_name} {last_name}"
    candidate = User(email=email, name=name, first_name=first_name, last_name=last_name)
    password = temporary_password(candidate)
    if password is None:
        return JsonResponse({"error": "A temporary password could not be issued."}, status=500)

    try:
        with transaction.atomic():
            user = User.objects.create_user(
                email=email,
                name=name,
                password=password,
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                is_staff=ROLES[role],
            )
            record(USER_CREATED, subject_email=user.email, actor=request.user)
    except IntegrityError:
        return JsonResponse({"error": "An account with this email already exists."}, status=400)

    return JsonResponse({"user": account_payload(user), "temporary_password": password}, status=201)


@admin_required
@require_http_methods(["PATCH"])
def user_detail(request, user_id):
    data = read_json(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    if "is_active" not in data or not isinstance(data["is_active"], bool):
        return JsonResponse({"error": "is_active must be true or false."}, status=400)

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found."}, status=404)

    active = data["is_active"]
    if user.pk == request.user.pk and not active:
        return JsonResponse({"error": "You cannot deactivate your own account."}, status=400)
    if user.is_active == active:
        return JsonResponse(account_payload(user))

    user.is_active = active
    user.save(update_fields=["is_active"])
    record(
        USER_REACTIVATED if active else USER_DEACTIVATED,
        subject_email=user.email,
        actor=request.user,
    )
    return JsonResponse(account_payload(user))


def security_payload(row):
    return {"email_otp_enabled": row.email_otp_enabled, "sms_otp_enabled": row.sms_otp_enabled}


@admin_required
@require_http_methods(["GET", "PATCH"])
def security_settings(request):
    row = SecuritySettings.load()
    if request.method == "GET":
        return JsonResponse(security_payload(row))

    data = read_json(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    updates = {}
    for key in ("email_otp_enabled", "sms_otp_enabled"):
        if key not in data:
            continue
        if not isinstance(data[key], bool):
            return JsonResponse({"error": f"{key} must be true or false."}, status=400)
        updates[key] = data[key]
    if not updates:
        return JsonResponse({"error": "No security settings were provided."}, status=400)

    changed = any(getattr(row, key) != value for key, value in updates.items())
    for key, value in updates.items():
        setattr(row, key, value)
    if changed:
        row.save(update_fields=list(updates))
        record(
            SECURITY_SETTINGS_CHANGED,
            subject_email=request.user.email,
            actor=request.user,
            detail=f"email_otp={'on' if row.email_otp_enabled else 'off'};sms_otp={'on' if row.sms_otp_enabled else 'off'}",
        )
    return JsonResponse(security_payload(row))


@admin_required
@require_GET
def audit_log(_request):
    events = AuditEvent.objects.select_related("actor").order_by("-created_at", "-id")[:200]
    return JsonResponse(
        {
            "events": [
                {
                    "id": event.pk,
                    "action": event.action,
                    "actor_name": event.actor.name if event.actor_id else None,
                    "actor_email": event.actor.email if event.actor_id else None,
                    "subject_email": event.subject_email,
                    "detail": event.detail,
                    "created_at": iso(event.created_at),
                }
                for event in events
            ]
        }
    )
