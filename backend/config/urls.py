from django.urls import include, path

urlpatterns = [
    path("api/auth/", include("accounts.urls")),
    path("api/admin/", include("accounts.admin_urls")),
    path("api/", include("workflow.urls")),
    path("api/", include("portfolio.urls")),
]
