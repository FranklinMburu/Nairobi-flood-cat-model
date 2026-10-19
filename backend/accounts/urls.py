from django.urls import path

from . import views

urlpatterns = [
    path("csrf/", views.csrf),
    path("demo-mode/", views.demo_mode),
    path("demo-login/", views.demo_login),
    path("register/", views.register),
    path("login/", views.login_view),
    path("send-otp/", views.send_otp_view),
    path("resend-otp/", views.resend_otp_view),
    path("verify-otp/", views.verify_otp_view),
    path("logout/", views.logout_view),
    path("me/", views.me),
]
