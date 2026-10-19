"""Prepare a fresh Vercel instance: an empty SQLite database in /tmp.

Runs once per instance, from config/wsgi.py, before the first request. It
creates the tables and the Presentation Demo user. Every instance creates the
same user with the same id and the same unusable password value, because
Django ties a signed-in session to both: a visitor whose next request reaches
another instance then stays signed in.

Nothing here is used locally.
"""

from django.core.management import call_command

# Starts with "!", so Django treats it as an unusable password: nobody can
# sign in with it. It is fixed (not random) only so that every instance
# computes the same session hash for the demo user.
DEMO_PASSWORD_VALUE = "!presentation-demo-no-password"


def prepare() -> None:
    from django.conf import settings

    from accounts.models import User
    from accounts.views import DEMO_EMAIL

    call_command("migrate", interactive=False, verbosity=0)
    settings.WORKFLOW_STORE_DIR.mkdir(parents=True, exist_ok=True)

    user, _ = User.objects.get_or_create(
        email=DEMO_EMAIL,
        defaults={"name": "Presentation Demo", "is_staff": False},
    )
    if user.password != DEMO_PASSWORD_VALUE:
        user.password = DEMO_PASSWORD_VALUE
        user.save(update_fields=["password"])
