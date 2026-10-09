from django.urls import path

from . import views

urlpatterns = [
    path("portfolio/", views.portfolio),
    path("buildings/", views.buildings),
    path("buildings/<str:loc_id>/", views.building_detail),
    path("loss-curve/", views.loss_curve),
    path("hotspots/", views.hotspots),
]
