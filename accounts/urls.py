from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("employee-login/", views.employee_national_id_login, name="employee_login"),
    path("accounts/users/", views.user_list, name="user_list"),
    path("accounts/users/add/", views.user_form, name="user_create"),
    path("accounts/users/<uuid:user_id>/edit/", views.user_form, name="user_edit"),
    path("accounts/users/<uuid:user_id>/delete/", views.user_delete, name="user_delete"),
    path("accounts/roles/", views.role_list, name="role_list"),
    path("accounts/roles/add/", views.role_form, name="role_create"),
    path("accounts/roles/<uuid:role_id>/edit/", views.role_form, name="role_edit"),
    path("accounts/roles/<uuid:role_id>/delete/", views.role_delete, name="role_delete"),
]
