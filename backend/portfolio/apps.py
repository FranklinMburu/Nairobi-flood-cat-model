import os

from django.apps import AppConfig


class PortfolioConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "portfolio"

    def ready(self):
        # The reloader parent does not serve requests. Warm the engine in the serving process.
        if os.environ.get("RUN_MAIN") != "true":
            return
        from .engine import get_payload

        get_payload()
