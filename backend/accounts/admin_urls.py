from django.urls import path

from . import admin_api

urlpatterns = [
    path("stats/", admin_api.stats),
    path("users/", admin_api.users),
    path("users/<int:user_id>/", admin_api.user_detail),
    path("security/", admin_api.security_settings),
    path("audit-log/", admin_api.audit_log),
]
