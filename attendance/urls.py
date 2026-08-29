from django.urls import path

from . import views

app_name = "attendance"

urlpatterns = [
    path("attendance/records/", views.attendance_record_list, name="record_list"),
    path("attendance/reports/", views.report_overview, name="report_overview"),
    path("attendance/reports/builder/", views.report_builder, name="report_builder"),
    path("attendance/reports/category/<slug:category>/", views.attendance_category_report, name="category_report"),
    path("attendance/reports/outside-location/", views.outside_location_report, name="outside_location_report"),
    path("attendance/calculate/", views.run_attendance_calculation, name="run_calculation"),
    path("attendance/imports/", views.attendance_import_list, name="import_list"),
    path("attendance/imports/upload/", views.attendance_import_upload, name="import_upload"),
    path("attendance/imports/<uuid:batch_id>/", views.attendance_import_detail, name="import_detail"),
    path("attendance/imports/<uuid:batch_id>/preview/", views.attendance_import_preview, name="import_preview"),
    path("attendance/imports/<uuid:batch_id>/errors/", views.attendance_import_errors, name="import_errors"),
    path("attendance/imports/<uuid:batch_id>/approve/", views.attendance_import_approve, name="import_approve"),
    path("attendance/imports/<uuid:batch_id>/resolve-unmatched/", views.attendance_import_resolve_unmatched, name="import_resolve_unmatched"),
    path("attendance/imports/<uuid:batch_id>/delete/", views.attendance_import_delete, name="import_delete"),
    path("attendance/imports/<uuid:batch_id>/update/", views.attendance_import_update, name="import_update"),
    path("attendance/imports/<uuid:batch_id>/archive/", views.attendance_import_archive, name="import_archive"),
    path("attendance/imports/<uuid:batch_id>/restore/", views.attendance_import_restore, name="import_restore"),
]
