import json
import re
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.test import Client, TestCase
from django.utils import timezone

from accounts.audit import (
    LOGIN_FAILED,
    LOGIN_SUCCEEDED,
    OTP_EXPIRED,
    OTP_FAILED,
    OTP_REQUESTED,
    OTP_RESENT,
    OTP_SENT,
    OTP_VERIFIED,
    SECURITY_SETTINGS_CHANGED,
    USER_CREATED,
    USER_DEACTIVATED,
    USER_REACTIVATED,
)
from accounts.models import AuditEvent, OtpChallenge, SecuritySettings, User
from accounts.otp import generate_code

PASSWORD = "Valid-password-9"
EMAIL = "user@example.com"
NAME = "Francis Masila"


def post_json(client, path, payload, **extra):
    return client.post(path, data=json.dumps(payload), content_type="application/json", **extra)


def password_only():
    row = SecuritySettings.load()
    row.email_otp_enabled = False
    row.sms_otp_enabled = False
    row.save()


def code_from_text(text):
    match = re.search(r"\b(\d{6})\b", text)
    if match is None:
        raise AssertionError("No verification code was delivered.")
    return match.group(1)


class RegistrationTests(TestCase):
    def test_public_registration_is_disabled(self):
        response = post_json(
            self.client,
            "/api/auth/register/",
            {"name": NAME, "email": EMAIL, "password": PASSWORD},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "Public registration is disabled."})
        self.assertFalse(User.objects.filter(email=EMAIL).exists())


class SessionTests(TestCase):
    def setUp(self):
        password_only()
        User.objects.create_user(email=EMAIL, name=NAME, password=PASSWORD)

    def test_successful_login(self):
        response = post_json(
            self.client,
            "/api/auth/login/",
            {"email": "User@Example.com", "password": PASSWORD},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["email"], EMAIL)
        self.assertEqual(response.json()["name"], NAME)
        self.assertNotIn("password", response.json())
        self.assertIn("_auth_user_id", self.client.session)

    def test_invalid_login(self):
        response = post_json(
            self.client,
            "/api/auth/login/",
            {"email": EMAIL, "password": "wrong-password"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Invalid email or password."})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_me_while_authenticated(self):
        post_json(self.client, "/api/auth/login/", {"email": EMAIL, "password": PASSWORD})

        response = self.client.get("/api/auth/me/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["email"], EMAIL)
        self.assertEqual(body["name"], NAME)
        self.assertEqual(set(body), {"id", "name", "email", "is_staff"})
        self.assertFalse(body["is_staff"])

    def test_me_while_unauthenticated(self):
        response = self.client.get("/api/auth/me/")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Authentication required."})

    def test_successful_logout(self):
        post_json(self.client, "/api/auth/login/", {"email": EMAIL, "password": PASSWORD})

        response = post_json(self.client, "/api/auth/logout/", {})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"detail": "Logged out."})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_me_after_logout(self):
        post_json(self.client, "/api/auth/login/", {"email": EMAIL, "password": PASSWORD})
        post_json(self.client, "/api/auth/logout/", {})

        response = self.client.get("/api/auth/me/")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Authentication required."})


class CsrfTests(TestCase):
    def test_login_requires_csrf_token_and_accepts_the_issued_token(self):
        password_only()
        User.objects.create_user(email=EMAIL, name=NAME, password=PASSWORD)
        client = Client(enforce_csrf_checks=True)

        blocked = post_json(client, "/api/auth/login/", {"email": EMAIL, "password": PASSWORD})
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json(), {"error": "CSRF verification failed."})

        issued = client.get("/api/auth/csrf/")
        self.assertEqual(issued.status_code, 200)
        token = issued.json()["csrfToken"]

        allowed = post_json(
            client,
            "/api/auth/login/",
            {"email": EMAIL, "password": PASSWORD},
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["email"], EMAIL)


def patch_json(client, path, payload):
    return client.patch(path, data=json.dumps(payload), content_type="application/json")


class AdminApiTests(TestCase):
    def setUp(self):
        password_only()
        self.admin = User.objects.create_user(
            email="admin@example.com",
            name="Dira Admin",
            password=PASSWORD,
            is_staff=True,
        )
        self.member = User.objects.create_user(email=EMAIL, name=NAME, password=PASSWORD)

    def test_admin_endpoints_require_authentication(self):
        response = self.client.get("/api/admin/users/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Authentication required."})

    def test_normal_user_is_forbidden(self):
        post_json(self.client, "/api/auth/login/", {"email": EMAIL, "password": PASSWORD})
        response = self.client.get("/api/admin/stats/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "Administrator access required."})

    def test_staff_can_list_and_read_real_stats(self):
        post_json(self.client, "/api/auth/login/", {"email": "admin@example.com", "password": PASSWORD})

        stats = self.client.get("/api/admin/stats/")
        listing = self.client.get("/api/admin/users/")

        self.assertEqual(stats.status_code, 200)
        self.assertEqual(
            stats.json(),
            {"total_users": 2, "active_users": 2, "inactive_users": 0, "administrators": 1},
        )
        self.assertEqual(listing.status_code, 200)
        emails = {row["email"] for row in listing.json()["users"]}
        self.assertEqual(emails, {"admin@example.com", EMAIL})
        self.assertNotIn("password", listing.json()["users"][0])

    def test_admin_creates_user_and_rejects_duplicate_email(self):
        post_json(self.client, "/api/auth/login/", {"email": "admin@example.com", "password": PASSWORD})
        payload = {
            "first_name": "Amina",
            "last_name": "Otieno",
            "email": "Amina@Example.com",
            "phone": "+254 712 345678",
            "role": "user",
        }
        created = post_json(self.client, "/api/admin/users/", payload)
        duplicate = post_json(self.client, "/api/admin/users/", payload)

        self.assertEqual(created.status_code, 201)
        body = created.json()
        self.assertEqual(body["user"]["email"], "amina@example.com")
        self.assertEqual(body["user"]["role"], "user")
        self.assertNotIn("password", body["user"])
        temporary = body["temporary_password"]
        user = User.objects.get(email="amina@example.com")
        self.assertNotEqual(user.password, temporary)
        self.assertTrue(user.check_password(temporary))
        self.assertTrue(user.password.startswith("pbkdf2_sha256$"))
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(duplicate.json(), {"error": "An account with this email already exists."})
        self.assertTrue(AuditEvent.objects.filter(action=USER_CREATED, subject_email="amina@example.com").exists())

    def test_admin_can_deactivate_and_reactivate_user(self):
        post_json(self.client, "/api/auth/login/", {"email": "admin@example.com", "password": PASSWORD})

        disabled = patch_json(self.client, f"/api/admin/users/{self.member.pk}/", {"is_active": False})
        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(disabled.json()["is_active"])

        self.client.logout()
        blocked = post_json(self.client, "/api/auth/login/", {"email": EMAIL, "password": PASSWORD})
        self.assertEqual(blocked.status_code, 401)
        self.assertEqual(blocked.json(), {"error": "Your account is inactive. Contact your administrator."})
        self.assertNotIn("_auth_user_id", self.client.session)

        post_json(self.client, "/api/auth/login/", {"email": "admin@example.com", "password": PASSWORD})
        restored = patch_json(self.client, f"/api/admin/users/{self.member.pk}/", {"is_active": True})
        self.assertEqual(restored.status_code, 200)
        self.assertTrue(restored.json()["is_active"])
        self.assertTrue(AuditEvent.objects.filter(action=USER_DEACTIVATED, subject_email=EMAIL).exists())
        self.assertTrue(AuditEvent.objects.filter(action=USER_REACTIVATED, subject_email=EMAIL).exists())

        stats = self.client.get("/api/admin/stats/")
        self.assertEqual(stats.json()["active_users"], 2)
        self.assertEqual(stats.json()["inactive_users"], 0)

    def test_login_events_are_recorded(self):
        post_json(self.client, "/api/auth/login/", {"email": EMAIL, "password": "wrong-password"})
        post_json(self.client, "/api/auth/login/", {"email": EMAIL, "password": PASSWORD})

        self.assertTrue(AuditEvent.objects.filter(action=LOGIN_FAILED, subject_email=EMAIL).exists())
        self.assertTrue(AuditEvent.objects.filter(action=LOGIN_SUCCEEDED, subject_email=EMAIL).exists())
        stored = AuditEvent.objects.filter(subject_email=EMAIL)
        self.assertFalse(
            any(
                PASSWORD in event.subject_email or PASSWORD in event.action or PASSWORD in event.detail
                for event in stored
            )
        )


PHONE = "+254712345678"


def configure_otp(*, email, sms):
    row = SecuritySettings.load()
    row.email_otp_enabled = email
    row.sms_otp_enabled = sms
    row.save()
    return row


def assert_no_secrets(test, *secrets):
    for event in AuditEvent.objects.all():
        blob = f"{event.action} {event.subject_email} {event.detail}"
        for secret in secrets:
            test.assertNotIn(secret, blob)


class SecondFactorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email=EMAIL,
            name=NAME,
            password=PASSWORD,
            phone=PHONE,
        )

    def login(self):
        return post_json(self.client, "/api/auth/login/", {"email": EMAIL, "password": PASSWORD})

    def test_default_configuration_is_email_only_and_unique(self):
        SecuritySettings.objects.all().delete()

        first = SecuritySettings.load()
        SecuritySettings.load()
        SecuritySettings.objects.create(email_otp_enabled=False, sms_otp_enabled=True)

        self.assertTrue(first.email_otp_enabled)
        self.assertFalse(first.sms_otp_enabled)
        self.assertEqual(SecuritySettings.objects.count(), 1)
        self.assertFalse(SecuritySettings.objects.get().email_otp_enabled)
        self.assertTrue(SecuritySettings.objects.get().sms_otp_enabled)

    def test_password_only_when_both_methods_are_disabled(self):
        configure_otp(email=False, sms=False)

        response = self.login()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["email"], EMAIL)
        self.assertNotIn("requires_2fa", response.json())
        self.assertEqual(OtpChallenge.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertTrue(AuditEvent.objects.filter(action=LOGIN_SUCCEEDED, subject_email=EMAIL).exists())
        self.assertFalse(AuditEvent.objects.filter(action=OTP_SENT).exists())

    def test_email_otp_flow(self):
        configure_otp(email=True, sms=False)
        started = self.login()

        self.assertEqual(started.status_code, 200)
        body = started.json()
        self.assertTrue(body["requires_2fa"])
        self.assertEqual(body["methods"], ["email"])
        self.assertEqual(body["method"], "email")
        self.assertNotEqual(body["masked_email"], EMAIL)
        self.assertNotIn("code", body)
        self.assertEqual(uuid.UUID(body["challenge_id"]).version, 4)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [EMAIL])
        self.assertIn("Dira", mail.outbox[0].body)
        self.assertIn("5 minutes", mail.outbox[0].body)
        self.assertNotIn(PASSWORD, mail.outbox[0].body)
        code = code_from_text(mail.outbox[0].body)
        self.assertEqual(len(code), 6)

        wrong = post_json(
            self.client,
            "/api/auth/verify-otp/",
            {"challenge_id": body["challenge_id"], "code": "000000", "method": "email"},
        )
        self.assertEqual(wrong.status_code, 400)
        self.assertEqual(wrong.json(), {"error": "Invalid verification code."})
        self.assertNotIn("_auth_user_id", self.client.session)

        verified = post_json(
            self.client,
            "/api/auth/verify-otp/",
            {"challenge_id": body["challenge_id"], "code": code, "method": "email"},
        )
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(verified.json()["email"], EMAIL)
        self.assertFalse(verified.json()["is_staff"])
        self.assertIn("_auth_user_id", self.client.session)
        self.assertTrue(AuditEvent.objects.filter(action=OTP_VERIFIED, detail="email").exists())
        self.assertTrue(AuditEvent.objects.filter(action=LOGIN_SUCCEEDED).exists())
        self.assertEqual(AuditEvent.objects.filter(action=LOGIN_SUCCEEDED).count(), 1)
        assert_no_secrets(self, code, PASSWORD)

        self.client.logout()
        reused = post_json(
            self.client,
            "/api/auth/verify-otp/",
            {"challenge_id": body["challenge_id"], "code": code, "method": "email"},
        )
        self.assertEqual(reused.status_code, 400)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_leading_zero_code_is_accepted(self):
        configure_otp(email=True, sms=False)
        with patch("accounts.otp.generate_code", return_value="012345"):
            started = self.login()
        code = code_from_text(mail.outbox[0].body)
        self.assertEqual(code, "012345")
        verified = post_json(
            self.client,
            "/api/auth/verify-otp/",
            {"challenge_id": started.json()["challenge_id"], "code": "012345", "method": "email"},
        )
        self.assertEqual(verified.status_code, 200)

    def test_expired_code_is_rejected(self):
        configure_otp(email=True, sms=False)
        clock = {"now": timezone.now()}
        with patch("django.utils.timezone.now", side_effect=lambda: clock["now"]):
            started = self.login()
            code = code_from_text(mail.outbox[0].body)
            clock["now"] += timedelta(minutes=6)
            expired = post_json(
                self.client,
                "/api/auth/verify-otp/",
                {"challenge_id": started.json()["challenge_id"], "code": code, "method": "email"},
            )
        self.assertEqual(expired.status_code, 400)
        self.assertEqual(expired.json(), {"error": "This verification code has expired. Request a new code."})
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertTrue(AuditEvent.objects.filter(action=OTP_EXPIRED).exists())
        assert_no_secrets(self, code)

    def test_five_failures_lock_the_challenge(self):
        configure_otp(email=True, sms=False)
        started = self.login()
        challenge_id = started.json()["challenge_id"]
        code = code_from_text(mail.outbox[0].body)
        for _ in range(4):
            wrong = post_json(
                self.client,
                "/api/auth/verify-otp/",
                {"challenge_id": challenge_id, "code": "111111", "method": "email"},
            )
            self.assertEqual(wrong.json()["error"], "Invalid verification code.")
        locked = post_json(
            self.client,
            "/api/auth/verify-otp/",
            {"challenge_id": challenge_id, "code": "111111", "method": "email"},
        )
        self.assertEqual(locked.json()["error"], "Too many attempts. Request a new verification code.")
        blocked = post_json(
            self.client,
            "/api/auth/verify-otp/",
            {"challenge_id": challenge_id, "code": code, "method": "email"},
        )
        self.assertEqual(blocked.json()["error"], "Too many attempts. Request a new verification code.")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertGreaterEqual(AuditEvent.objects.filter(action=OTP_FAILED).count(), 5)
        assert_no_secrets(self, code, "111111")

    def test_resend_invalidates_the_previous_code_and_enforces_cooldown(self):
        configure_otp(email=True, sms=False)
        clock = {"now": timezone.now()}
        with patch("django.utils.timezone.now", side_effect=lambda: clock["now"]):
            started = self.login()
            challenge_id = started.json()["challenge_id"]
            original = code_from_text(mail.outbox[0].body)
            too_soon = post_json(self.client, "/api/auth/resend-otp/", {"challenge_id": challenge_id})
            self.assertEqual(too_soon.status_code, 429)
            self.assertLessEqual(too_soon.json()["retry_after"], 60)
            self.assertNotIn("code", too_soon.json())
            clock["now"] += timedelta(seconds=61)
            resent = post_json(self.client, "/api/auth/resend-otp/", {"challenge_id": challenge_id})
            self.assertEqual(resent.status_code, 200)
            self.assertNotIn("_auth_user_id", self.client.session)
            replacement = code_from_text(mail.outbox[1].body)
            self.assertNotEqual(original, replacement)
            stale = post_json(
                self.client,
                "/api/auth/verify-otp/",
                {"challenge_id": challenge_id, "code": original, "method": "email"},
            )
            self.assertEqual(stale.status_code, 400)
            current = post_json(
                self.client,
                "/api/auth/verify-otp/",
                {"challenge_id": challenge_id, "code": replacement, "method": "email"},
            )
        self.assertEqual(current.status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(action=OTP_RESENT, detail="email").exists())
        self.assertTrue(AuditEvent.objects.filter(action=OTP_REQUESTED).exists())
        assert_no_secrets(self, original, replacement)

    def test_sms_only_uses_the_mocked_gateway(self):
        configure_otp(email=False, sms=True)
        captured = {}

        def capture(phone, message):
            captured["phone"] = phone
            captured["message"] = message

        with patch("accounts.delivery.send_sms", side_effect=capture) as send_sms:
            started = self.login()
            self.assertEqual(send_sms.call_count, 1)
        body = started.json()
        self.assertEqual(body["methods"], ["sms"])
        self.assertEqual(body["method"], "sms")
        self.assertNotIn(PHONE, json.dumps(body))
        self.assertTrue(body["masked_phone"].endswith("678"))
        self.assertEqual(len(mail.outbox), 0)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(captured["phone"], "+254712345678")
        code = code_from_text(captured["message"])
        verified = post_json(
            self.client,
            "/api/auth/verify-otp/",
            {"challenge_id": body["challenge_id"], "code": code, "method": "sms"},
        )
        self.assertEqual(verified.status_code, 200)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertTrue(AuditEvent.objects.filter(action=OTP_SENT, detail="sms").exists())
        assert_no_secrets(self, code, PASSWORD)

    def test_both_methods_send_only_the_selected_channel(self):
        configure_otp(email=True, sms=True)
        with patch("accounts.delivery.send_sms") as send_sms:
            started = self.login()
            self.assertEqual(send_sms.call_count, 0)
            body = started.json()
            self.assertEqual(body["methods"], ["email", "sms"])
            self.assertNotIn("method", body)
            self.assertEqual(len(mail.outbox), 0)
            self.assertNotIn("_auth_user_id", self.client.session)

            chosen = post_json(
                self.client,
                "/api/auth/send-otp/",
                {"challenge_id": body["challenge_id"], "method": "email"},
            )
            self.assertEqual(chosen.status_code, 200)
            self.assertEqual(chosen.json()["method"], "email")
            self.assertEqual(send_sms.call_count, 0)
            self.assertEqual(len(mail.outbox), 1)
            code = code_from_text(mail.outbox[0].body)
            verified = post_json(
                self.client,
                "/api/auth/verify-otp/",
                {"challenge_id": body["challenge_id"], "code": code, "method": "email"},
            )
        self.assertEqual(verified.status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(action=OTP_SENT, detail="email").exists())
        assert_no_secrets(self, code)

    def test_sms_can_be_selected_when_both_methods_are_enabled(self):
        configure_otp(email=True, sms=True)
        captured = {}

        def capture(phone, message):
            captured["message"] = message

        with patch("accounts.delivery.send_sms", side_effect=capture):
            started = self.login()
            challenge_id = started.json()["challenge_id"]
            chosen = post_json(self.client, "/api/auth/send-otp/", {"challenge_id": challenge_id, "method": "sms"})
        self.assertEqual(chosen.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)
        code = code_from_text(captured["message"])
        verified = post_json(
            self.client,
            "/api/auth/verify-otp/",
            {"challenge_id": challenge_id, "code": code, "method": "sms"},
        )
        self.assertEqual(verified.status_code, 200)
        assert_no_secrets(self, code)

    def test_new_sign_in_invalidates_the_previous_code(self):
        configure_otp(email=True, sms=False)
        first = self.login()
        original = code_from_text(mail.outbox[0].body)
        second = self.login()
        replacement = code_from_text(mail.outbox[1].body)
        stale = post_json(
            self.client,
            "/api/auth/verify-otp/",
            {"challenge_id": first.json()["challenge_id"], "code": original, "method": "email"},
        )
        self.assertEqual(stale.status_code, 400)
        current = post_json(
            self.client,
            "/api/auth/verify-otp/",
            {"challenge_id": second.json()["challenge_id"], "code": replacement, "method": "email"},
        )
        self.assertEqual(current.status_code, 200)

    def test_generated_codes_keep_six_digits(self):
        codes = [generate_code() for _ in range(30)]
        self.assertTrue(all(len(code) == 6 and code.isdigit() for code in codes))


class SecuritySettingsApiTests(TestCase):
    def setUp(self):
        password_only()
        self.admin = User.objects.create_user(
            email="admin@example.com",
            name="Dira Admin",
            password=PASSWORD,
            is_staff=True,
        )
        User.objects.create_user(email=EMAIL, name=NAME, password=PASSWORD, phone=PHONE)

    def test_only_staff_can_read_and_change_settings(self):
        anonymous = self.client.get("/api/admin/security/")
        self.assertEqual(anonymous.status_code, 401)

        post_json(self.client, "/api/auth/login/", {"email": EMAIL, "password": PASSWORD})
        forbidden = self.client.get("/api/admin/security/")
        self.assertEqual(forbidden.status_code, 403)
        self.client.logout()

        post_json(self.client, "/api/auth/login/", {"email": "admin@example.com", "password": PASSWORD})
        current = self.client.get("/api/admin/security/")
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json(), {"email_otp_enabled": False, "sms_otp_enabled": False})

        saved = patch_json(
            self.client,
            "/api/admin/security/",
            {"email_otp_enabled": True, "sms_otp_enabled": False},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json(), {"email_otp_enabled": True, "sms_otp_enabled": False})
        stored = SecuritySettings.objects.get(pk=1)
        self.assertTrue(stored.email_otp_enabled)
        self.assertFalse(stored.sms_otp_enabled)
        again = self.client.get("/api/admin/security/")
        self.assertEqual(again.json()["email_otp_enabled"], True)

        event = AuditEvent.objects.get(action=SECURITY_SETTINGS_CHANGED)
        self.assertEqual(event.actor_id, self.admin.pk)
        self.assertIn("email_otp=on", event.detail)
        self.assertNotIn(PASSWORD, event.detail)
        self.client.logout()

        signed_in = AuditEvent.objects.filter(action=LOGIN_SUCCEEDED, subject_email=EMAIL).count()
        pending = post_json(self.client, "/api/auth/login/", {"email": EMAIL, "password": PASSWORD})
        self.assertTrue(pending.json()["requires_2fa"])
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(
            AuditEvent.objects.filter(action=LOGIN_SUCCEEDED, subject_email=EMAIL).count(),
            signed_in,
        )
