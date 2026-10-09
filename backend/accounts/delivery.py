"""Deliver a one-time code by email or SMS. Secrets stay in the environment."""

import json

from africastalking.SMS import SMSService
from django.conf import settings
from django.core.mail import send_mail


class DeliveryError(Exception):
    """The code was not delivered. The message must not contain the code or a secret."""


def deliver(method, user, code):
    if method == "email":
        deliver_email(user, code)
        return
    if method == "sms":
        deliver_sms(user, code)
        return
    raise DeliveryError("That verification method is not available.")


def deliver_email(user, code):
    send_mail(
        subject="Your Dira verification code",
        message=(
            "Dira\n\n"
            f"Your verification code is {code}.\n\n"
            "This code expires in 5 minutes.\n\n"
            "If you did not try to sign in, you can ignore this message. "
            "Do not share this code with anyone."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )


def deliver_sms(user, code):
    destination = normalize_number(user.phone)
    if not destination:
        raise DeliveryError("This account has no phone number.")
    message = (
        f"Dira verification code: {code}. "
        "It expires in 5 minutes. Do not share this code."
    )
    send_sms(destination, message)


def normalize_number(number):
    """Kenyan local numbers become E.164. 00254 is checked before a leading 0."""
    raw = (number or "").strip()
    for character in (" ", "-", "(", ")"):
        raw = raw.replace(character, "")
    digits = "".join(character for character in raw if character.isdigit())
    if not digits:
        return ""
    if digits.startswith("00254"):
        return "+254" + digits[5:]
    if digits.startswith("254"):
        return "+" + digits
    if digits.startswith("0"):
        return "+254" + digits[1:]
    if raw.startswith("+"):
        return "+" + digits
    return "+" + digits


def send_sms(phone, message):
    if settings.EMAIL_BACKEND.endswith("locmem.EmailBackend"):
        raise DeliveryError("Live SMS is disabled during tests.")
    username = settings.AT_USERNAME
    api_key = settings.AT_API_KEY
    if not username or not api_key:
        raise DeliveryError("SMS delivery is not configured.")
    destination = normalize_number(phone)
    if not destination:
        raise DeliveryError("This account has no phone number.")

    try:
        response = SMSService(username, api_key).send(
            message,
            [destination],
            sender_id=settings.AT_SENDER_ID or None,
        )
    except Exception:
        raise DeliveryError("SMS could not be sent.") from None
    if not _sms_accepted(response):
        raise DeliveryError("SMS could not be sent.")


def _sms_accepted(response):
    if isinstance(response, (bytes, str)):
        try:
            response = json.loads(response)
        except (TypeError, ValueError, UnicodeError):
            return False
    try:
        recipients = response["SMSMessageData"]["Recipients"]
    except (KeyError, TypeError):
        return False
    if not recipients:
        return False
    return all(item.get("statusCode") in {100, 101, 102} for item in recipients)
