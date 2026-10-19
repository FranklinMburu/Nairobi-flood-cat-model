import os

from django.shortcuts import redirect
from django.urls import include, path

# This process serves only /api/. The interface is the Vite app; send a
# browser that opens the API's root address there instead of a 404.
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://127.0.0.1:5173")


def root(_request):
    return redirect(FRONTEND_URL)


urlpatterns = [
    path("", root),
    path("api/auth/", include("accounts.urls")),
    path("api/admin/", include("accounts.admin_urls")),
    path("api/", include("workflow.urls")),
    path("api/", include("portfolio.urls")),
]
