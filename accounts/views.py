from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from audit.models import AuditLog
from organization.models import EmployeeIdentity
from organization.services.exceptions import SecurityConfigurationError
from organization.services.identity import national_id_digest

from .forms import NationalIdLoginForm, RoleAccessForm, UserAccessForm
from .models import Role, User
from .services import save_role_access, save_user_access


@require_http_methods(["GET", "POST"])
def employee_national_id_login(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        try:
            request.user.employee
        except ObjectDoesNotExist:
            pass
        else:
            return redirect("violations:employee_portal")
    form = NationalIdLoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        attempts = int(request.session.get("employee_login_attempts", 0))
        if attempts >= 5:
            form.add_error(None, "تم تجاوز عدد المحاولات المسموح. أغلق المتصفح وحاول لاحقًا.")
        else:
            try:
                digest = national_id_digest(form.cleaned_data["national_id"])
            except SecurityConfigurationError:
                form.add_error(None, "خدمة الدخول غير متاحة حاليًا. تواصل مع مسؤول النظام.")
            else:
                identity = EmployeeIdentity.objects.select_related("employee__user").filter(
                    national_id_hash=digest,
                    employee__employment_status="active",
                    employee__archived_at__isnull=True,
                ).first()
                if not identity:
                    request.session["employee_login_attempts"] = attempts + 1
                    AuditLog.objects.create(
                        action="employee.national_id_login",
                        module="accounts",
                        object_type="Employee",
                        outcome=AuditLog.Outcome.FAILURE,
                        reason="تعذر التحقق من بيانات الموظف.",
                    )
                    form.add_error(None, "تعذر التحقق من بيانات الموظف.")
                else:
                    employee = identity.employee
                    with transaction.atomic():
                        user = employee.user
                        if user is None:
                            user = User(
                                username=f"employee-{employee.id}",
                                first_name=(employee.preferred_name_ar or employee.full_name_ar)[:150],
                                is_active=True,
                            )
                            user.set_unusable_password()
                            user.save()
                            employee.user = user
                            employee.updated_by = user
                            employee.save(update_fields=("user", "updated_by", "updated_at"))
                        if not user.is_active:
                            form.add_error(None, "الحساب المرتبط بالموظف غير نشط.")
                        else:
                            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                            request.session["employee_portal_mode"] = True
                            request.session.pop("employee_login_attempts", None)
                            AuditLog.objects.create(
                                actor_user=user,
                                actor_username_snapshot=user.username,
                                action="employee.national_id_login",
                                module="accounts",
                                object_type="Employee",
                                object_id=employee.id,
                                object_repr_masked=employee.full_name_ar,
                                outcome=AuditLog.Outcome.SUCCESS,
                            )
                            return redirect("violations:employee_portal")
    return render(request, "accounts/employee_login.html", {"form": form, "page_title": "دخول الموظف"})


def system_admin_required(view_func):
    @wraps(view_func)
    def wrapped(request: HttpRequest, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not request.user.is_active or not request.user.is_superuser:
            return render(request, "core/errors/403.html", status=403)
        return view_func(request, *args, **kwargs)

    return wrapped


def _breadcrumbs(label):
    return (
        {"label": "الرئيسية", "url_name": "core:dashboard"},
        {"label": "المستخدمون", "url_name": "accounts:user_list"},
        {"label": label},
    )


@system_admin_required
@require_GET
def user_list(request: HttpRequest) -> HttpResponse:
    users = User.objects.select_related("employee").prefetch_related(
        "role_assignments__role", "department_scopes__department"
    )
    search = (request.GET.get("search") or "").strip()
    if search:
        users = users.filter(username__icontains=search)
    page_obj = Paginator(users, 25).get_page(request.GET.get("page"))
    for user in page_obj.object_list:
        user.ui_roles = [
            item.role.name_ar
            for item in user.role_assignments.all()
            if item.is_active and item.valid_to is None
        ]
        user.ui_departments = [
            item.department.name_ar
            for item in user.department_scopes.all()
            if item.valid_to is None
        ]
    return render(
        request,
        "accounts/users/list.html",
        {
            "page_title": "المستخدمون",
            "page_description": "إدارة الحسابات والأدوار ونطاقات الأقسام.",
            "breadcrumb_items": _breadcrumbs("القائمة"),
            "page_obj": page_obj,
            "search": search,
        },
    )


@system_admin_required
@require_http_methods(["GET", "POST"])
def user_form(request: HttpRequest, user_id=None) -> HttpResponse:
    instance = (
        get_object_or_404(User, pk=user_id, is_superuser=False)
        if user_id
        else User()
    )
    created = user_id is None
    form = UserAccessForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        user = form.save(commit=False)
        save_user_access(
            user=user,
            actor=request.user,
            employee=form.cleaned_data["employee"],
            roles=form.cleaned_data["roles"],
            departments=form.cleaned_data["departments"],
            access_level=form.cleaned_data["access_level"],
            include_descendants=form.cleaned_data["include_descendants"],
            password=form.cleaned_data["password1"],
            created=created,
        )
        messages.success(request, "تم حفظ المستخدم وصلاحياته ونطاقه بنجاح.")
        return redirect("accounts:user_list")
    return render(
        request,
        "accounts/users/form.html",
        {
            "page_title": "إضافة مستخدم" if created else "تعديل المستخدم",
            "page_description": "اربط الحساب بالموظف وحدد الدور والأقسام المسموحة.",
            "breadcrumb_items": _breadcrumbs("إضافة" if created else "تعديل"),
            "form": form,
            "managed_user": instance,
        },
    )


@system_admin_required
@require_GET
def role_list(request: HttpRequest) -> HttpResponse:
    roles = Role.objects.prefetch_related("permission_assignments__permission")
    page_obj = Paginator(roles, 25).get_page(request.GET.get("page"))
    for role in page_obj.object_list:
        role.ui_permissions = [
            item.permission.name_ar
            for item in role.permission_assignments.all()
            if item.revoked_at is None
        ]
    return render(
        request,
        "accounts/roles/list.html",
        {
            "page_title": "الأدوار والصلاحيات",
            "page_description": "إنشاء الأدوار والتحكم بالصلاحيات الممنوحة لكل دور.",
            "breadcrumb_items": _breadcrumbs("الأدوار"),
            "page_obj": page_obj,
        },
    )


@system_admin_required
@require_http_methods(["GET", "POST"])
def role_form(request: HttpRequest, role_id=None) -> HttpResponse:
    instance = get_object_or_404(Role, pk=role_id) if role_id else Role()
    created = role_id is None
    form = RoleAccessForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        role = form.save(commit=False)
        save_role_access(
            role=role,
            actor=request.user,
            permissions=form.cleaned_data["permissions"],
            created=created,
        )
        messages.success(request, "تم حفظ الدور والصلاحيات بنجاح.")
        return redirect("accounts:role_list")
    return render(
        request,
        "accounts/roles/form.html",
        {
            "page_title": "إضافة دور" if created else "تعديل الدور",
            "page_description": "حدد صلاحيات الدور ثم اربطه بالمستخدمين المطلوبين.",
            "breadcrumb_items": _breadcrumbs("إضافة دور" if created else "تعديل دور"),
            "form": form,
            "role": instance,
            "permission_groups": form.permission_groups,
        },
    )
