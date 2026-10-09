"""Session authentication API. Loss calculations stay in loss_engine."""

import json

from django.contrib.auth import login, logout
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .audit import LOGIN_FAILED, LOGIN_SUCCEEDED, PASSWORD_ACCEPTED, record
from .models import SecuritySettings, User
from .otp import OtpError, begin_challenge, challenge_payload, load_challenge, send_code, usable_methods, verify_code


def csrf_failure(request, reason=""):
    return JsonResponse({"error": "CSRF verification failed."}, status=403)


def user_payload(user):
    return {"id": user.pk, "name": user.name, "email": user.email, "is_staff": user.is_staff}


def read_json(request):
    if not request.body:
        return {}
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def text_field(data, key):
    value = data.get(key, "")
    if not isinstance(value, str):
        return ""
    return value.strip()


@require_GET
@ensure_csrf_cookie
def csrf(request):
    return JsonResponse({"csrfToken": get_token(request)})


@require_POST
def register(_request):
    return JsonResponse({"error": "Public registration is disabled."}, status=403)


@require_POST
def login_view(request):
    data = read_json(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    email = text_field(data, "email").lower()
    password = data.get("password", "")
    user, error = check_credentials(email, password)
    if error is not None:
        return error

    try:
        pending = begin_challenge(user)
    except OtpError as exc:
        return otp_error(exc)
    if pending is None:
        record(PASSWORD_ACCEPTED, subject_email=user.email, actor=user)
        return start_session(request, user)
    return JsonResponse(pending)


@require_POST
def send_otp_view(request):
    data = read_json(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    try:
        challenge = load_challenge(text_field(data, "challenge_id"))
        updated = send_code(challenge, text_field(data, "method"))
    except OtpError as exc:
        return otp_error(exc)
    methods = usable_methods(SecuritySettings.load(), updated.user)
    return JsonResponse(challenge_payload(updated, methods))


@require_POST
def resend_otp_view(request):
    data = read_json(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    try:
        challenge = load_challenge(text_field(data, "challenge_id"))
        if not challenge.method:
            raise OtpError("Choose a verification method first.")
        updated = send_code(challenge, challenge.method, resend=True)
    except OtpError as exc:
        return otp_error(exc)
    methods = usable_methods(SecuritySettings.load(), updated.user)
    return JsonResponse(challenge_payload(updated, methods))


@require_POST
def verify_otp_view(request):
    data = read_json(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    code = data.get("code", "")
    try:
        user = verify_code(text_field(data, "challenge_id"), code if isinstance(code, str) else "", text_field(data, "method"))
    except OtpError as exc:
        return otp_error(exc)
    return start_session(request, user)


def check_credentials(email, password):
    if not email or not isinstance(password, str) or not password:
        return None, JsonResponse({"error": "Invalid email or password."}, status=401)
    user = User.objects.filter(email=email).first()
    password_ok = user is not None and user.check_password(password)
    if user is not None and password_ok and not user.is_active:
        record(LOGIN_FAILED, subject_email=email, actor=user)
        return None, JsonResponse(
            {"error": "Your account is inactive. Contact your administrator."},
            status=401,
        )
    if not password_ok:
        record(LOGIN_FAILED, subject_email=email, actor=user if user is not None else None)
        return None, JsonResponse({"error": "Invalid email or password."}, status=401)
    return user, None


def start_session(request, user):
    login(request, user)
    record(LOGIN_SUCCEEDED, subject_email=user.email, actor=user)
    return JsonResponse(user_payload(user))


def otp_error(exc):
    return JsonResponse({"error": exc.message, **exc.extra}, status=exc.status)


@require_POST
def logout_view(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)
    logout(request)
    return JsonResponse({"detail": "Logged out."})


@require_GET
def me(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)
    return JsonResponse(user_payload(request.user))
