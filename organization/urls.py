from django.urls import path

from . import directory_views, views

app_name = "organization"

urlpatterns = [
    path("employees/", directory_views.employee_list, name="employee_list"),
    path(
        "employees/department-assignments/",
        directory_views.employee_bulk_department_assignment,
        name="employee_bulk_department_assignment",
    ),
    path("employees/<uuid:employee_id>/", directory_views.employee_detail, name="employee_detail"),
    path("employees/<uuid:employee_id>/edit/", directory_views.employee_edit, name="employee_edit"),
    path("organization/departments/", directory_views.department_list, name="department_list"),
    path("organization/departments/add/", directory_views.department_create, name="department_create"),
    path("organization/departments/<uuid:instance_id>/edit/", directory_views.department_edit, name="department_edit"),
    path("organization/departments/<uuid:instance_id>/disable/", directory_views.department_disable, name="department_disable"),
    path("organization/locations/", directory_views.location_list, name="location_list"),
    path("organization/locations/add/", directory_views.location_create, name="location_create"),
    path("organization/locations/<uuid:instance_id>/edit/", directory_views.location_edit, name="location_edit"),
    path("organization/locations/<uuid:instance_id>/disable/", directory_views.location_disable, name="location_disable"),
    path(
        "employees/imports/",
        views.employee_import_list,
        name="employee_import_list",
    ),
    path(
        "employees/imports/upload/",
        views.employee_import_upload,
        name="employee_import_upload",
    ),
    path(
        "employees/imports/add-employee/",
        views.employee_manual_create,
        name="employee_manual_create",
    ),
    path(
        "employees/imports/manage-employees/",
        views.employee_bulk_manage,
        name="employee_bulk_manage",
    ),
    path(
        "employees/imports/template/",
        views.employee_import_template,
        name="employee_import_template",
    ),
    path(
        "employees/imports/<uuid:batch_id>/",
        views.employee_import_detail,
        name="employee_import_detail",
    ),
    path(
        "employees/imports/<uuid:batch_id>/preview/",
        views.employee_import_preview,
        name="employee_import_preview",
    ),
    path(
        "employees/imports/<uuid:batch_id>/errors/",
        views.employee_import_errors,
        name="employee_import_errors",
    ),
    path(
        "employees/imports/<uuid:batch_id>/approve/",
        views.employee_import_approve,
        name="employee_import_approve",
    ),
    path(
        "employees/imports/<uuid:batch_id>/delete/",
        views.employee_import_delete,
        name="employee_import_delete",
    ),
]
