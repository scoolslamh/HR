from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render, resolve_url
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .access import user_can_manage_references, user_can_view_employee_directory
from .forms import (
    DepartmentForm,
    EmployeeDirectoryFilterForm,
    EmployeeEditForm,
    JobTitleForm,
    LocationForm,
)
from .models import Department, Employee, JobTitle, Location, UserDepartmentScope
from .selectors import (
    current_assignment_for,
    current_primary_location_for,
    department_ids_in_user_scope,
    employee_directory_queryset,
)
from .services import disable_reference, save_reference, update_employee


def _require_access(check):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request: HttpRequest, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(
                    request.get_full_path(), resolve_url("core:login")
                )
            if not check(request.user):
                raise PermissionDenied("لا تملك صلاحية الوصول إلى هذه الصفحة.")
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


employee_directory_required = _require_access(user_can_view_employee_directory)
reference_management_required = _require_access(user_can_manage_references)


def _employee_breadcrumb(label: str):
    return (
        {"label": "الرئيسية", "url_name": "core:dashboard"},
        {"label": "الموظفون", "url_name": "organization:employee_list"},
        {"label": label},
    )


def _current_date_filters(prefix: str = "") -> Q:
    today = timezone.localdate()
    return Q(**{f"{prefix}valid_from__lte": today}) & (
        Q(**{f"{prefix}valid_to__isnull": True}) | Q(**{f"{prefix}valid_to__gt": today})
    )


@employee_directory_required
@require_GET
def employee_list(request: HttpRequest) -> HttpResponse:
    queryset = employee_directory_queryset(request.user).order_by("full_name_ar")
    filter_form = EmployeeDirectoryFilterForm(request.GET or None, user=request.user)
    if filter_form.is_valid():
        search = filter_form.cleaned_data.get("search")
        department = filter_form.cleaned_data.get("department")
        location = filter_form.cleaned_data.get("location")
        status = filter_form.cleaned_data.get("status")
        if search:
            queryset = queryset.filter(
                Q(full_name_ar__icontains=search) | Q(employee_number__icontains=search)
            )
        if department:
            queryset = queryset.filter(
                _current_date_filters("employment_assignments__"),
                employment_assignments__is_primary=True,
                employment_assignments__department=department,
            )
        if location:
            queryset = queryset.filter(
                _current_date_filters("primary_location_assignments__"),
                primary_location_assignments__location=location,
            )
        if status:
            queryset = queryset.filter(employment_status=status)
    page_obj = Paginator(queryset.distinct(), 20).get_page(request.GET.get("page"))
    for employee in page_obj.object_list:
        employee.ui_assignment = current_assignment_for(employee)
        employee.ui_location = current_primary_location_for(employee)
        identity = getattr(employee, "identity", None)
        employee.ui_national_id = (
            f"******{identity.national_id_last4}" if identity else "غير متاح"
        )
    query_params = request.GET.copy()
    query_params.pop("page", None)
    return render(
        request,
        "organization/employees/list.html",
        {
            "page_title": "الموظفون",
            "page_description": "عرض الموظفين ضمن النطاق التنظيمي المسموح.",
            "breadcrumb_items": _employee_breadcrumb("القائمة")[:-1],
            "page_obj": page_obj,
            "filter_form": filter_form,
            "query_string": query_params.urlencode(),
        },
    )


def _scoped_employee_or_404(user, employee_id) -> Employee:
    return get_object_or_404(employee_directory_queryset(user), id=employee_id)


@employee_directory_required
@require_GET
def employee_detail(request: HttpRequest, employee_id) -> HttpResponse:
    employee = _scoped_employee_or_404(request.user, employee_id)
    current_assignment = current_assignment_for(employee)
    current_location = current_primary_location_for(employee)
    identity = getattr(employee, "identity", None)
    return render(
        request,
        "organization/employees/detail.html",
        {
            "page_title": "تفاصيل الموظف",
            "page_description": "البيانات الأساسية والتاريخ التنظيمي .",
            "breadcrumb_items": _employee_breadcrumb(employee.full_name_ar),
            "employee": employee,
            "national_id_masked": (
                f"******{identity.national_id_last4}" if identity else "غير متاح"
            ),
            "current_assignment": current_assignment,
            "current_location": current_location,
            "assignment_history": employee.employment_assignments.select_related(
                "department", "manager_employee", "job_title"
            ).order_by("-valid_from"),
            "location_history": employee.primary_location_assignments.select_related(
                "location"
            ).order_by("-valid_from"),
            "can_edit": _can_manage_employee(request.user, current_assignment),
        },
    )


def _can_manage_employee(user, current_assignment) -> bool:
    if user.is_superuser:
        return True
    if current_assignment is None:
        return False
    allowed = department_ids_in_user_scope(
        user,
        access_levels=(
            UserDepartmentScope.AccessLevel.MANAGE,
            UserDepartmentScope.AccessLevel.APPROVE,
        ),
    )
    return current_assignment.department_id in allowed


@employee_directory_required
@require_http_methods(["GET", "POST"])
def employee_edit(request: HttpRequest, employee_id) -> HttpResponse:
    employee = _scoped_employee_or_404(request.user, employee_id)
    current_assignment = current_assignment_for(employee)
    if not _can_manage_employee(request.user, current_assignment):
        raise PermissionDenied("لا تملك صلاحية تعديل هذا الموظف.")
    form = EmployeeEditForm(
        request.POST or None,
        instance=employee,
        user=request.user,
    )
    department_signing_locations = {
        str(department.id): (
            str(department.signing_location_id)
            if department.signing_location_id
            else None
        )
        for department in form.fields["department"].queryset.only(
            "id", "signing_location_id"
        )
    }
    department_heads = {
        str(department.id): (
            str(department.department_head_id)
            if department.department_head_id
            else None
        )
        for department in form.fields["department"].queryset.only(
            "id", "department_head_id"
        )
    }
    if request.method == "POST" and form.is_valid():
        try:
            updated = update_employee(
                employee,
                actor=request.user,
                full_name_ar=form.cleaned_data["full_name_ar"],
                employee_number=form.cleaned_data["employee_number"],
                normalized_mobile=form.cleaned_data["mobile"],
                employment_status=form.cleaned_data["employment_status"],
                department=form.cleaned_data["department"],
                location=form.cleaned_data["location"],
                location_effective_date=form.cleaned_data["location_effective_date"],
                manager=form.cleaned_data["manager_employee"],
            )
        except PermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        messages.success(request, "تم تحديث بيانات الموظف وحفظ السجل التاريخي.")
        return redirect("organization:employee_detail", employee_id=updated.id)
    return render(
        request,
        "organization/employees/form.html",
        {
            "page_title": "تعديل الموظف",
            "page_description": "تعديل البيانات المسموحة دون تغيير السجل المدني.",
            "breadcrumb_items": _employee_breadcrumb("تعديل الموظف"),
            "employee": employee,
            "form": form,
            "department_signing_locations": department_signing_locations,
            "department_heads": department_heads,
        },
    )


def _reference_breadcrumb(section: str, url_name: str, label: str):
    return (
        {"label": "الرئيسية", "url_name": "core:dashboard"},
        {"label": section, "url_name": url_name},
        {"label": label},
    )


def _reference_list(request, *, model, template, title, description):
    queryset = model.objects.all()
    if model is Department:
        queryset = queryset.select_related(
            "parent", "signing_location", "department_head"
        )
    page_obj = Paginator(queryset.order_by("code"), 25).get_page(
        request.GET.get("page")
    )
    return render(
        request,
        template,
        {
            "page_title": title,
            "page_description": description,
            "breadcrumb_items": (
                {"label": "الرئيسية", "url_name": "core:dashboard"},
                {"label": title},
            ),
            "page_obj": page_obj,
        },
    )


def _reference_form(
    request,
    *,
    model,
    form_class,
    instance_id,
    template,
    title,
    list_url,
):
    instance = get_object_or_404(model, id=instance_id) if instance_id else None
    form = form_class(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        saved = save_reference(
            form.save(commit=False),
            actor=request.user,
            created=instance is None,
        )
        messages.success(request, "تم حفظ البيانات بنجاح.")
        return redirect(list_url)
    return render(
        request,
        template,
        {
            "page_title": title,
            "page_description": "أدخل البيانات المطلوبة ثم احفظ التغييرات.",
            "breadcrumb_items": _reference_breadcrumb(title, list_url, "النموذج"),
            "form": form,
            "object": instance,
        },
    )


def _disable(request, *, model, instance_id, list_url, label):
    instance = get_object_or_404(model, id=instance_id)
    disable_reference(instance, actor=request.user)
    messages.success(request, f"تم تعطيل {label} دون حذف السجل التاريخي.")
    return redirect(list_url)


@reference_management_required
@require_GET
def department_list(request):
    return _reference_list(
        request,
        model=Department,
        template="organization/departments/list.html",
        title="الأقسام",
        description="إدارة الهيكل التنظيمي وتعطيل الوحدات دون حذفها.",
    )


@reference_management_required
@require_http_methods(["GET", "POST"])
def department_create(request):
    return _reference_form(
        request,
        model=Department,
        form_class=DepartmentForm,
        instance_id=None,
        template="organization/references/form.html",
        title="إضافة قسم",
        list_url="organization:department_list",
    )


@reference_management_required
@require_http_methods(["GET", "POST"])
def department_edit(request, instance_id):
    return _reference_form(
        request,
        model=Department,
        form_class=DepartmentForm,
        instance_id=instance_id,
        template="organization/references/form.html",
        title="تعديل قسم",
        list_url="organization:department_list",
    )


@reference_management_required
@require_POST
def department_disable(request, instance_id):
    return _disable(
        request,
        model=Department,
        instance_id=instance_id,
        list_url="organization:department_list",
        label="القسم",
    )


@reference_management_required
@require_GET
def location_list(request):
    return _reference_list(
        request,
        model=Location,
        template="organization/locations/list.html",
        title="المواقع",
        description="إدارة مواقع العمل وتعطيلها دون حذفها.",
    )


@reference_management_required
@require_http_methods(["GET", "POST"])
def location_create(request):
    return _reference_form(
        request,
        model=Location,
        form_class=LocationForm,
        instance_id=None,
        template="organization/references/form.html",
        title="إضافة موقع",
        list_url="organization:location_list",
    )


@reference_management_required
@require_http_methods(["GET", "POST"])
def location_edit(request, instance_id):
    return _reference_form(
        request,
        model=Location,
        form_class=LocationForm,
        instance_id=instance_id,
        template="organization/references/form.html",
        title="تعديل موقع",
        list_url="organization:location_list",
    )


@reference_management_required
@require_POST
def location_disable(request, instance_id):
    return _disable(
        request,
        model=Location,
        instance_id=instance_id,
        list_url="organization:location_list",
        label="الموقع",
    )


@reference_management_required
@require_GET
def job_title_list(request):
    return _reference_list(
        request,
        model=JobTitle,
        template="organization/job_titles/list.html",
        title="المسميات الوظيفية",
        description="إدارة المسميات الوظيفية وتعطيلها دون حذفها.",
    )


@reference_management_required
@require_http_methods(["GET", "POST"])
def job_title_create(request):
    return _reference_form(
        request,
        model=JobTitle,
        form_class=JobTitleForm,
        instance_id=None,
        template="organization/references/form.html",
        title="إضافة مسمى وظيفي",
        list_url="organization:job_title_list",
    )


@reference_management_required
@require_http_methods(["GET", "POST"])
def job_title_edit(request, instance_id):
    return _reference_form(
        request,
        model=JobTitle,
        form_class=JobTitleForm,
        instance_id=instance_id,
        template="organization/references/form.html",
        title="تعديل مسمى وظيفي",
        list_url="organization:job_title_list",
    )


@reference_management_required
@require_POST
def job_title_disable(request, instance_id):
    return _disable(
        request,
        model=JobTitle,
        instance_id=instance_id,
        list_url="organization:job_title_list",
        label="المسمى الوظيفي",
    )
