from django.urls import path

from . import views

urlpatterns = [
    path("csrf/", views.csrf),
    path("register/", views.register),
    path("login/", views.login_view),
    path("send-otp/", views.send_otp_view),
    path("resend-otp/", views.resend_otp_view),
    path("verify-otp/", views.verify_otp_view),
    path("logout/", views.logout_view),
    path("me/", views.me),
]
