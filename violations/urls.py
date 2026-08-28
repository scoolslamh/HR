from django.urls import path

from . import views

app_name = "violations"

urlpatterns = [
    path("my/", views.employee_portal, name="employee_portal"),
    path("my/clarifications/<uuid:clarification_id>/", views.employee_clarification, name="employee_clarification"),
    path("clarifications/manager/", views.manager_dashboard, name="manager_dashboard"),
    path("clarifications/manager/<uuid:clarification_id>/", views.manager_review, name="manager_review"),
    path("clarifications/executive/", views.executive_dashboard, name="executive_dashboard"),
    path("work-missions/", views.work_mission_list, name="work_mission_list"),
    path("clarifications/evidence/<uuid:evidence_id>/", views.evidence_download, name="evidence_download"),
]
