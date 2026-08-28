from functools import wraps
from io import BytesIO
from typing import Any, Callable

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render, resolve_url
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.models import UserRole

from .forms import (
    BulkEmployeeManagementForm,
    EmployeeImportApprovalForm,
    EmployeeImportUploadForm,
    ManualEmployeeCreateForm,
)
from .models import Employee, EmployeeImportBatch, EmployeeImportError, EmployeeImportRow
from .selectors import current_assignment_for, current_primary_location_for
from .services import (
    EmployeeImportServiceError,
    approve_employee_import,
    build_employee_import_template,
    bulk_set_employee_archive_status,
    can_delete_employee_import,
    delete_employee_import,
    create_manual_employee,
    preview_employee_import,
)
from .services.identity import redact_potential_national_ids

EMPLOYEE_IMPORT_PERMISSION = "employees.import"

STATUS_COLORS = {
    EmployeeImportBatch.Status.UPLOADED: "azure",
    EmployeeImportBatch.Status.PREVIEW_READY: "green",
    EmployeeImportBatch.Status.HAS_ERRORS: "red",
    EmployeeImportBatch.Status.APPROVED: "purple",
    EmployeeImportBatch.Status.FAILED: "red",
}

ROW_STATUS_COLORS = {
    EmployeeImportRow.ValidationStatus.VALID: "green",
    EmployeeImportRow.ValidationStatus.WARNING: "yellow",
    EmployeeImportRow.ValidationStatus.ERROR: "red",
}

ROW_ACTION_COLORS = {
    EmployeeImportRow.ImportAction.CREATE: "blue",
    EmployeeImportRow.ImportAction.UPDATE: "purple",
    EmployeeImportRow.ImportAction.SKIP: "secondary",
}


def user_can_import_employees(user, *, at=None) -> bool:
    """Evaluate the active project permission without relying on UI visibility."""

    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True

    at = at or timezone.now()
    return (
        UserRole.objects.filter(
            user=user,
            is_active=True,
            valid_from__lte=at,
            role__is_active=True,
            role__permission_assignments__permission__code=EMPLOYEE_IMPORT_PERMISSION,
            role__permission_assignments__permission__is_active=True,
            role__permission_assignments__granted_at__lte=at,
            role__permission_assignments__revoked_at__isnull=True,
        )
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gt=at))
        .exists()
    )


def employee_import_permission_required(view_func: Callable) -> Callable:
    """Require authentication and the employee-import business permission."""

    @wraps(view_func)
    def wrapped(request: HttpRequest, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(
                request.get_full_path(),
                resolve_url("core:login"),
            )
        if not user_can_import_employees(request.user):
            raise PermissionDenied("لا تملك صلاحية استيراد بيانات الموظفين.")
        return view_func(request, *args, **kwargs)

    return wrapped


def system_admin_required(view_func: Callable) -> Callable:
    @wraps(view_func)
    def wrapped(request: HttpRequest, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), resolve_url("core:login"))
        if not request.user.is_active or not request.user.is_superuser:
            raise PermissionDenied("هذا الإجراء متاح لمدير النظام فقط.")
        return view_func(request, *args, **kwargs)

    return wrapped


def _get_batch(batch_id) -> EmployeeImportBatch:
    return get_object_or_404(
        EmployeeImportBatch.objects.select_related("uploaded_by", "approved_by"),
        id=batch_id,
    )


def _decorate_batch(batch: EmployeeImportBatch) -> EmployeeImportBatch:
    batch.ui_status_color = STATUS_COLORS.get(batch.status, "secondary")
    batch.ui_status_label = batch.get_status_display()
    batch.ui_original_filename = redact_potential_national_ids(
        batch.original_filename
    )
    batch.ui_uploaded_by_name = redact_potential_national_ids(
        getattr(batch.uploaded_by, "username", "") or ""
    )
    batch.ui_approved_by_name = redact_potential_national_ids(
        getattr(batch.approved_by, "username", "") or ""
    )
    return batch


def _masked_national_id(last_four: str) -> str:
    return f"******{last_four}" if last_four else "غير متاح"


def _first_display_value(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return redact_potential_national_ids(value)
    return "—"


def _row_for_display(row: EmployeeImportRow) -> dict[str, Any]:
    """Expose only the redacted fields needed by the preview table."""

    data = row.display_data_json or {}
    return {
        "row_number": row.row_number,
        "employee_name": _first_display_value(
            data,
            "employee_name",
            "full_name_ar",
            "اسم الموظف",
        ),
        "national_id_masked": _masked_national_id(row.national_id_last4),
        "department": _first_display_value(
            data,
            "department",
            "department_name",
            "قسم",
            "القسم",
        ),
        "location": _first_display_value(
            data,
            "location",
            "location_name",
            "مكان الحضور والانصراف",
        ),
        "manager": _first_display_value(
            data,
            "manager_name",
            "manager",
            "المدير المباشر",
        ),
        "action_label": row.get_import_action_display(),
        "action_color": ROW_ACTION_COLORS.get(row.import_action, "secondary"),
        "status_label": row.get_validation_status_display(),
        "status_color": ROW_STATUS_COLORS.get(row.validation_status, "secondary"),
    }


def _requires_reference_creation(batch: EmployeeImportBatch) -> bool:
    return bool(batch.missing_department_rows or batch.missing_location_rows)


def _can_approve(batch: EmployeeImportBatch) -> bool:
    return (
        batch.status == EmployeeImportBatch.Status.PREVIEW_READY
        and batch.error_rows == 0
        and batch.approved_at is None
    )


def _breadcrumb(current_label: str) -> tuple[dict[str, str], ...]:
    return (
        {"label": "الرئيسية", "url_name": "core:dashboard"},
        {
            "label": "استيراد بيانات الموظفين",
            "url_name": "organization:employee_import_list",
        },
        {"label": current_label},
    )


def _preview_context(
    request: HttpRequest,
    batch: EmployeeImportBatch,
    *,
    approval_form: EmployeeImportApprovalForm | None = None,
) -> dict[str, Any]:
    rows = EmployeeImportRow.objects.filter(batch=batch).order_by("row_number")
    page_obj = Paginator(rows, 25).get_page(request.GET.get("page"))
    display_rows = tuple(_row_for_display(row) for row in page_obj.object_list)
    requires_reference_creation = _requires_reference_creation(batch)

    if approval_form is None:
        approval_form = EmployeeImportApprovalForm(
            requires_reference_creation=requires_reference_creation
        )

    return {
        "page_title": "معاينة استيراد بيانات الموظفين",
        "page_description": (
            "راجع ملخص التحقق والصفوف المنقحة قبل اعتماد أي تغيير."
        ),
        "breadcrumb_items": _breadcrumb("المعاينة"),
        "batch": _decorate_batch(batch),
        "page_obj": page_obj,
        "display_rows": display_rows,
        "approval_form": approval_form,
        "can_approve": _can_approve(batch),
        "requires_reference_creation": requires_reference_creation,
    }


@employee_import_permission_required
@require_GET
def employee_import_list(request: HttpRequest) -> HttpResponse:
    batches = EmployeeImportBatch.objects.select_related(
        "uploaded_by", "approved_by"
    ).order_by("-created_at")
    page_obj = Paginator(batches, 20).get_page(request.GET.get("page"))
    for batch in page_obj.object_list:
        _decorate_batch(batch)

    employee_page = None
    employee_search = ""
    employee_status = "active"
    if request.user.is_superuser:
        employee_search = (request.GET.get("employee_search") or "").strip()
        employee_status = request.GET.get("employee_status") or "active"
        employees = Employee.objects.select_related("identity").order_by("full_name_ar")
        if employee_search:
            employees = employees.filter(
                Q(full_name_ar__icontains=employee_search)
                | Q(employee_number__icontains=employee_search)
            )
        if employee_status == "archived":
            employees = employees.filter(
                Q(employment_status=Employee.EmploymentStatus.ARCHIVED)
                | Q(archived_at__isnull=False)
            )
        elif employee_status == "all":
            pass
        else:
            employee_status = "active"
            employees = employees.exclude(
                employment_status=Employee.EmploymentStatus.ARCHIVED
            ).filter(archived_at__isnull=True)
        employee_page = Paginator(employees, 25).get_page(
            request.GET.get("employee_page")
        )
        for employee in employee_page.object_list:
            employee.ui_assignment = current_assignment_for(employee)
            employee.ui_location = current_primary_location_for(employee)
            employee.ui_national_id = (
                f"******{employee.identity.national_id_last4}"
                if hasattr(employee, "identity")
                else "غير متاح"
            )

    return render(
        request,
        "organization/employee_import/list.html",
        {
            "page_title": "سجل استيراد بيانات الموظفين",
            "page_description": (
                "متابعة ملفات بيانات الموظفين من الرفع حتى المعاينة والاعتماد."
            ),
            "breadcrumb_items": (
                {"label": "الرئيسية", "url_name": "core:dashboard"},
                {"label": "استيراد بيانات الموظفين"},
            ),
            "page_obj": page_obj,
            "is_system_admin": request.user.is_superuser,
            "manual_employee_form": ManualEmployeeCreateForm()
            if request.user.is_superuser
            else None,
            "bulk_employee_form": BulkEmployeeManagementForm()
            if request.user.is_superuser
            else None,
            "employee_page": employee_page,
            "employee_search": employee_search,
            "employee_status": employee_status,
        },
    )


@system_admin_required
@require_POST
def employee_manual_create(request: HttpRequest) -> HttpResponse:
    form = ManualEmployeeCreateForm(request.POST)
    if not form.is_valid():
        messages.error(request, "تعذر إضافة الموظف. تحقق من جميع البيانات المطلوبة.")
        return redirect("organization:employee_import_list")
    try:
        employee = create_manual_employee(
            actor=request.user,
            normalized_national_id=form.cleaned_data["national_id"],
            full_name_ar=form.cleaned_data["full_name_ar"],
            employee_number=form.cleaned_data["employee_number"],
            normalized_mobile=form.cleaned_data["mobile"],
            department=form.cleaned_data["department"],
            location=form.cleaned_data["location"],
            manager=form.cleaned_data["manager_employee"],
        )
    except (ValueError, PermissionError, EmployeeImportServiceError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"تمت إضافة الموظف {employee.full_name_ar} بنجاح.")
    return redirect("organization:employee_import_list")


@system_admin_required
@require_POST
def employee_bulk_manage(request: HttpRequest) -> HttpResponse:
    form = BulkEmployeeManagementForm(request.POST)
    if not form.is_valid():
        first_error = next(
            (str(error) for errors in form.errors.values() for error in errors),
            "تحقق من بيانات العملية الجماعية.",
        )
        messages.error(request, first_error)
        return redirect("organization:employee_import_list")
    scope = form.cleaned_data["scope"]
    try:
        affected = bulk_set_employee_archive_status(
            actor=request.user,
            action=form.cleaned_data["action"],
            employee_ids=(
                list(form.cleaned_data["employee_ids"].values_list("id", flat=True))
                if scope == "selected"
                else None
            ),
            batch=form.cleaned_data["batch"] if scope == "batch" else None,
            all_employees=scope == "all",
            reason=form.cleaned_data["reason"],
        )
    except (ValueError, PermissionError) as exc:
        messages.error(request, str(exc))
    else:
        action_label = "أرشفة" if form.cleaned_data["action"] == "archive" else "استعادة"
        messages.success(request, f"تمت {action_label} {affected} موظف بنجاح.")
    return redirect("organization:employee_import_list")


@employee_import_permission_required
@require_http_methods(["GET", "POST"])
def employee_import_upload(request: HttpRequest) -> HttpResponse:
    form = EmployeeImportUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            batch = preview_employee_import(
                form.cleaned_data["workbook"],
                uploaded_by=request.user,
            )
        except EmployeeImportServiceError as exc:
            form.add_error("workbook", exc.message_ar)
        else:
            messages.success(
                request,
                "اكتملت قراءة الملف وحُفظت المعاينة دون تعديل بيانات الموظفين.",
            )
            return redirect(
                "organization:employee_import_preview",
                batch_id=batch.id,
            )

    return render(
        request,
        "organization/employee_import/upload.html",
        {
            "page_title": "رفع ملف بيانات الموظفين",
            "page_description": (
                "ارفع قالب xlsx المعتمد للتحقق منه وإنشاء معاينة آمنة."
            ),
            "breadcrumb_items": _breadcrumb("رفع ملف"),
            "form": form,
        },
    )


@employee_import_permission_required
@require_GET
def employee_import_detail(request: HttpRequest, batch_id) -> HttpResponse:
    batch = _decorate_batch(_get_batch(batch_id))
    recent_errors = EmployeeImportError.objects.filter(batch=batch).select_related(
        "row"
    )[:5]
    return render(
        request,
        "organization/employee_import/detail.html",
        {
            "page_title": "تفاصيل دفعة الاستيراد",
            "page_description": "بيانات الملف وملخص نتيجة التحقق والاعتماد.",
            "breadcrumb_items": _breadcrumb("تفاصيل الدفعة"),
            "batch": batch,
            "recent_errors": recent_errors,
            "can_approve": _can_approve(batch),
            "can_delete": can_delete_employee_import(batch),
        },
    )


@employee_import_permission_required
@require_POST
def employee_import_delete(request: HttpRequest, batch_id) -> HttpResponse:
    batch = _get_batch(batch_id)
    try:
        delete_employee_import(batch, deleted_by=request.user)
    except EmployeeImportServiceError as exc:
        messages.error(request, exc.message_ar)
        return redirect("organization:employee_import_detail", batch_id=batch.id)

    messages.success(request, "تم حذف دفعة الاستيراد التجريبية بنجاح.")
    return redirect("organization:employee_import_list")


@employee_import_permission_required
@require_GET
def employee_import_preview(request: HttpRequest, batch_id) -> HttpResponse:
    batch = _get_batch(batch_id)
    return render(
        request,
        "organization/employee_import/preview.html",
        _preview_context(request, batch),
    )


@employee_import_permission_required
@require_GET
def employee_import_errors(request: HttpRequest, batch_id) -> HttpResponse:
    batch = _decorate_batch(_get_batch(batch_id))
    error_items = EmployeeImportError.objects.filter(batch=batch).select_related(
        "row"
    )
    page_obj = Paginator(error_items, 30).get_page(request.GET.get("page"))
    return render(
        request,
        "organization/employee_import/errors.html",
        {
            "page_title": "أخطاء استيراد بيانات الموظفين",
            "page_description": (
                "رسائل التحقق الآمنة دون عرض السجل المدني أو الحمولة الأصلية."
            ),
            "breadcrumb_items": _breadcrumb("الأخطاء والتحذيرات"),
            "batch": batch,
            "page_obj": page_obj,
        },
    )


@employee_import_permission_required
@require_POST
def employee_import_approve(request: HttpRequest, batch_id) -> HttpResponse:
    batch = _get_batch(batch_id)
    if batch.status == EmployeeImportBatch.Status.APPROVED:
        messages.info(request, "هذه الدفعة معتمدة مسبقًا ولم تُنفذ مرة أخرى.")
        return redirect("organization:employee_import_detail", batch_id=batch.id)

    if not _can_approve(batch):
        messages.error(
            request,
            "لا يمكن اعتماد الدفعة قبل معالجة الأخطاء المانعة.",
        )
        return redirect("organization:employee_import_preview", batch_id=batch.id)

    requires_reference_creation = _requires_reference_creation(batch)
    form = EmployeeImportApprovalForm(
        request.POST,
        requires_reference_creation=requires_reference_creation,
    )
    if form.is_valid():
        try:
            approved_batch = approve_employee_import(
                batch,
                approved_by=request.user,
                create_missing_references=form.cleaned_data[
                    "create_missing_references"
                ],
            )
        except EmployeeImportServiceError as exc:
            form.add_error(None, exc.message_ar)
        else:
            messages.success(request, "تم اعتماد دفعة بيانات الموظفين بنجاح.")
            return redirect(
                "organization:employee_import_detail",
                batch_id=approved_batch.id,
            )

    return render(
        request,
        "organization/employee_import/preview.html",
        _preview_context(request, batch, approval_form=form),
    )


@employee_import_permission_required
@require_GET
def employee_import_template(request: HttpRequest) -> FileResponse:
    template_bytes = build_employee_import_template()
    return FileResponse(
        BytesIO(template_bytes),
        as_attachment=True,
        filename="employee_import_template.xlsx",
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )
