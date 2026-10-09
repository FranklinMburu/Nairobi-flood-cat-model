import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, name, password=None, **extra):
        if not email:
            raise ValueError("Email is required.")
        if not name:
            raise ValueError("Name is required.")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, name=name.strip(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, name, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if extra.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self.create_user(email, name, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """Application user. Email is the login identifier. Passwords are hashed."""

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=150)
    first_name = models.CharField(max_length=60, blank=True)
    last_name = models.CharField(max_length=60, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    def __str__(self):
        return self.email


class AuditEvent(models.Model):
    """Staff-visible record of account actions. Never stores secrets."""

    actor = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    action = models.CharField(max_length=64)
    subject_email = models.CharField(max_length=254, blank=True)
    detail = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at", "-id"]


class SecuritySettings(models.Model):
    """One platform-wide second-factor configuration. The row id is always 1."""

    email_otp_enabled = models.BooleanField(default=True)
    sms_otp_enabled = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        self.pk = 1
        if type(self).objects.filter(pk=1).exists():
            self._state.adding = False
            kwargs.pop("force_insert", None)
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        row, _created = cls.objects.get_or_create(pk=1)
        return row


class OtpChallenge(models.Model):
    """Pending sign-in. The one-time code is stored only as a password hash."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="otp_challenges")
    method = models.CharField(max_length=8, blank=True)
    code_hash = models.CharField(max_length=256, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    code_expires_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    invalidated = models.BooleanField(default=False)
