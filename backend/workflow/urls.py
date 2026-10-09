from django.urls import path

from . import views

urlpatterns = [
    path("workflow/capabilities/", views.capabilities),
    path("workflow/example/", views.example),
    path("workflow/submissions/", views.submissions),
    path("workflow/submissions/<str:extraction_id>/", views.submission_detail),
    path("workflow/submissions/<str:extraction_id>/decisions/", views.decision),
    path("workflow/submissions/<str:extraction_id>/run/", views.run),
    path("runs/", views.reference_runs),
    path("runs/<str:run_id>/", views.reference_run_detail),
    path("ingest/<str:action>/", views.ingest),
]
