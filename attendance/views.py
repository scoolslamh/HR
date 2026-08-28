from __future__ import annotations

from functools import wraps
from typing import Callable

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django.utils import timezone

from organization.access import user_has_business_permission
from organization.services.exceptions import SecurityConfigurationError
from organization.services.identity import (
    decrypt_sensitive_text,
    national_id_digest,
    normalize_national_id,
    redact_potential_national_ids,
)

from .forms import (
    AttendanceImportApprovalForm,
    AttendanceImportArchiveForm,
    AttendanceImportDeleteForm,
    AttendanceImportMetadataForm,
    AttendanceImportUploadForm,
    UnmatchedEmployeeResolutionForm,
)
from .models import ImportBatch, ImportError, ImportRow
from .services import (
    AttendanceImportServiceError,
    approve_attendance_import,
    archive_attendance_import,
    can_delete_attendance_import,
    delete_attendance_import,
    preview_attendance_import,
    resolve_unmatched_employee,
    restore_attendance_import,
    update_attendance_import_metadata,
)

ATTENDANCE_IMPORT_PERMISSION = "attendance.import"
ATTENDANCE_APPROVE_PERMISSION = "attendance.approve"

STATUS_COLORS = {
    ImportBatch.Status.UPLOADED: "secondary",
    ImportBatch.Status.PREVIEW_READY: "success",
    ImportBatch.Status.HAS_ERRORS: "danger",
    ImportBatch.Status.APPROVED: "primary",
    ImportBatch.Status.FAILED: "danger",
}

ROW_STATUS_COLORS = {
    ImportRow.ValidationStatus.VALID: "success",
    ImportRow.ValidationStatus.WARNING: "warning",
    ImportRow.ValidationStatus.ERROR: "danger",
}

MATCH_COLORS = {
    ImportRow.MatchStatus.MATCHED: "success",
    ImportRow.MatchStatus.UNMATCHED: "danger",
    ImportRow.MatchStatus.INVALID: "danger",
}

LOCATION_COLORS = {
    ImportRow.LocationMatchStatus.MATCHED: "success",
    ImportRow.LocationMatchStatus.MISMATCH: "warning",
    ImportRow.LocationMatchStatus.UNKNOWN: "secondary",
    ImportRow.LocationMatchStatus.NOT_REQUIRED: "secondary",
}


def _permission_required(code: str):
    def decorator(view_func: Callable) -> Callable:
        @wraps(view_func)
        def wrapped(request: HttpRequest, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not user_has_business_permission(request.user, code):
                return render(request, "core/errors/403.html", status=403)
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


attendance_import_permission_required = _permission_required(ATTENDANCE_IMPORT_PERMISSION)
attendance_approve_permission_required = _permission_required(ATTENDANCE_APPROVE_PERMISSION)


def system_admin_required(view_func):
    @wraps(view_func)
    def wrapped(request: HttpRequest, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not request.user.is_active or not request.user.is_superuser:
            return render(request, "core/errors/403.html", status=403)
        return view_func(request, *args, **kwargs)

    return wrapped


def _get_batch(batch_id):
    return get_object_or_404(
        ImportBatch.objects.select_related("uploaded_by", "approved_by"),
        pk=batch_id,
    )


def _decorate_batch(batch: ImportBatch) -> ImportBatch:
    batch.ui_status_label = "مؤرشف" if batch.archived_at else batch.get_status_display()
    batch.ui_status_color = "secondary" if batch.archived_at else STATUS_COLORS.get(batch.status, "secondary")
    batch.ui_original_filename = redact_potential_national_ids(batch.original_filename)
    batch.ui_display_name = redact_potential_national_ids(
        batch.display_name or batch.original_filename
    )
    batch.ui_source_period_title = redact_potential_national_ids(
        batch.source_period_title or ""
    )
    if batch.period_start and batch.period_end:
        period_days = (batch.period_end - batch.period_start).days + 1
        batch.ui_period_name = (
            f"من {batch.period_start:%Y/%m/%d} إلى {batch.period_end:%Y/%m/%d}"
            f" — {period_days} يومًا"
        )
    else:
        batch.ui_period_name = "غير مستخرجة"
    batch.ui_uploaded_by_name = redact_potential_national_ids(
        getattr(batch.uploaded_by, "username", "") or ""
    )
    batch.ui_approved_by_name = redact_potential_national_ids(
        getattr(batch.approved_by, "username", "") or ""
    )
    return batch


def _row_display(row: ImportRow) -> dict:
    data = row.display_data_json or {}
    is_ignored = (
        row.matched_employee_id is None
        and row.match_status == ImportRow.MatchStatus.UNMATCHED
        and row.validation_status != ImportRow.ValidationStatus.ERROR
    )
    location_labels = {
        ImportRow.LocationMatchStatus.MATCHED: "موقعا الحضور والانصراف متطابقان",
        ImportRow.LocationMatchStatus.MISMATCH: "موقع الحضور والانصراف مختلفان",
        ImportRow.LocationMatchStatus.UNKNOWN: "تعذر التحقق من الموقعين",
        ImportRow.LocationMatchStatus.NOT_REQUIRED: "غير مطلوب",
    }
    return {
        "row_number": row.row_number,
        "employee_name": data.get("employee_name") or "—",
        "national_id_masked": data.get("national_id_masked") or (
            f"******{row.national_id_last4}" if row.national_id_last4 else "غير متاح"
        ),
        "attendance_date": data.get("attendance_date") or "—",
        "check_in": data.get("check_in") or "—",
        "check_out": data.get("check_out") or "—",
        "check_in_location": data.get("check_in_location") or "—",
        "source_status": data.get("source_status") or "—",
        "match_label": "تم تجاهله" if is_ignored else row.get_match_status_display(),
        "match_color": "secondary" if is_ignored else MATCH_COLORS.get(row.match_status, "secondary"),
        "validation_label": row.get_validation_status_display(),
        "validation_color": ROW_STATUS_COLORS.get(row.validation_status, "secondary"),
        "location_label": location_labels.get(row.location_match_status, row.get_location_match_status_display()),
        "location_color": LOCATION_COLORS.get(row.location_match_status, "secondary"),
    }


def _breadcrumb(label: str):
    return (
        {"label": "الرئيسية", "url_name": "core:dashboard"},
        {"label": "استيراد الحضور", "url_name": "attendance:import_list"},
        {"label": label},
    )


def _can_approve(request: HttpRequest, batch: ImportBatch) -> bool:
    return (
        batch.status == ImportBatch.Status.PREVIEW_READY
        and batch.error_count == 0
        and batch.approved_at is None
        and user_has_business_permission(request.user, ATTENDANCE_APPROVE_PERMISSION)
    )


def _unmatched_employee_groups(batch: ImportBatch) -> tuple[dict, ...]:
    rows = (
        ImportRow.objects.filter(
            batch=batch,
            matched_employee__isnull=True,
            national_id_hash__isnull=False,
            errors__error_code="employee_not_found",
        )
        .order_by("row_number")
        .distinct()
    )
    grouped = {}
    for row in rows:
        item = grouped.setdefault(
            row.national_id_hash,
            {
                "national_id_hash": row.national_id_hash,
                "national_id_masked": (row.display_data_json or {}).get("national_id_masked") or f"******{row.national_id_last4}",
                "employee_name": (row.display_data_json or {}).get("employee_name") or "—",
                "job_title": (row.display_data_json or {}).get("job_title") or "—",
                "row_count": 0,
                "form": UnmatchedEmployeeResolutionForm(
                    initial={"national_id_hash": row.national_id_hash}
                ),
            },
        )
        item["row_count"] += 1
    return tuple(grouped.values())


@attendance_import_permission_required
@require_GET
def attendance_import_list(request: HttpRequest) -> HttpResponse:
    batches = ImportBatch.objects.select_related("uploaded_by", "approved_by")
    page_obj = Paginator(batches, 20).get_page(request.GET.get("page"))
    for batch in page_obj.object_list:
        _decorate_batch(batch)
        batch.ui_can_delete = can_delete_attendance_import(batch)
    return render(
        request,
        "attendance/import/list.html",
        {
            "page_title": "سجل استيراد الحضور",
            "page_description": "متابعة ملفات تقرير البصمة من الرفع حتى الاعتماد.",
            "breadcrumb_items": _breadcrumb("سجل الاستيراد"),
            "page_obj": page_obj,
            "is_system_admin": request.user.is_superuser,
        },
    )


@attendance_import_permission_required
@require_http_methods(["GET", "POST"])
def attendance_import_upload(request: HttpRequest) -> HttpResponse:
    form = AttendanceImportUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            batch = preview_attendance_import(
                form.cleaned_data["workbook"], uploaded_by=request.user
            )
        except AttendanceImportServiceError as exc:
            form.add_error("workbook", exc.message_ar)
        else:
            messages.success(request, "تمت قراءة ملف البصمة وحفظ المعاينة دون إنشاء سجلات نهائية.")
            return redirect("attendance:import_preview", batch_id=batch.id)
    return render(
        request,
        "attendance/import/upload.html",
        {
            "page_title": "رفع تقرير البصمة",
            "page_description": "ارفع التقرير الرسمي بصيغة xlsx، وسيتم تحديد الفترة من عنوان الشيت.",
            "breadcrumb_items": _breadcrumb("رفع ملف"),
            "form": form,
        },
    )


@attendance_import_permission_required
@require_GET
def attendance_import_detail(request: HttpRequest, batch_id) -> HttpResponse:
    batch = _decorate_batch(_get_batch(batch_id))
    recent_errors = ImportError.objects.filter(batch=batch).select_related("row")[:10]
    return render(
        request,
        "attendance/import/detail.html",
        {
            "page_title": "تفاصيل دفعة الحضور",
            "page_description": "ملخص الملف ونتيجة المطابقة والتحقق.",
            "breadcrumb_items": _breadcrumb("تفاصيل الدفعة"),
            "batch": batch,
            "recent_errors": recent_errors,
            "can_approve": _can_approve(request, batch),
            "can_delete": can_delete_attendance_import(batch),
            "is_system_admin": request.user.is_superuser,
        },
    )


@attendance_import_permission_required
@require_GET
def attendance_import_preview(request: HttpRequest, batch_id) -> HttpResponse:
    batch = _decorate_batch(_get_batch(batch_id))
    rows = ImportRow.objects.filter(batch=batch).select_related("matched_employee")
    page_obj = Paginator(rows, 30).get_page(request.GET.get("page"))
    display_rows = tuple(_row_display(row) for row in page_obj.object_list)
    return render(
        request,
        "attendance/import/preview.html",
        {
            "page_title": "معاينة تقرير البصمة",
            "page_description": "راجع السجلات المستخرجة والمطابقة قبل الاعتماد.",
            "breadcrumb_items": _breadcrumb("المعاينة"),
            "batch": batch,
            "page_obj": page_obj,
            "display_rows": display_rows,
            "can_approve": _can_approve(request, batch),
            "approval_form": AttendanceImportApprovalForm(),
            "unmatched_employees": _unmatched_employee_groups(batch),
            "can_resolve_unmatched": request.user.is_superuser,
        },
    )


@system_admin_required
@require_POST
def attendance_import_resolve_unmatched(request: HttpRequest, batch_id) -> HttpResponse:
    batch = _get_batch(batch_id)
    form = UnmatchedEmployeeResolutionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "تعذر معالجة الموظف: اختر القسم عند الإضافة.")
        return redirect("attendance:import_preview", batch_id=batch.id)
    try:
        action, row_count = resolve_unmatched_employee(
            batch,
            national_id_hash=form.cleaned_data["national_id_hash"],
            action=form.cleaned_data["action"],
            resolved_by=request.user,
            department=form.cleaned_data.get("department"),
        )
    except AttendanceImportServiceError as exc:
        messages.error(request, exc.message_ar)
    else:
        if action == "added":
            messages.success(request, f"تمت إضافة الموظف ومطابقة {row_count} سجل حضور.")
        else:
            messages.success(request, f"تم تجاهل الموظف واستبعاد {row_count} سجل حضور من الاعتماد.")
    return redirect("attendance:import_preview", batch_id=batch.id)


@attendance_import_permission_required
@require_GET
def attendance_import_errors(request: HttpRequest, batch_id) -> HttpResponse:
    batch = _decorate_batch(_get_batch(batch_id))
    errors = ImportError.objects.filter(batch=batch).select_related("row")
    severity = request.GET.get("severity")
    if severity in {ImportError.Severity.ERROR, ImportError.Severity.WARNING}:
        errors = errors.filter(severity=severity)
    page_obj = Paginator(errors, 30).get_page(request.GET.get("page"))
    return render(
        request,
        "attendance/import/errors.html",
        {
            "page_title": "أخطاء وتحذيرات استيراد الحضور",
            "page_description": "قيم السجل المدني معروضة بشكل مقنّع فقط.",
            "breadcrumb_items": _breadcrumb("الأخطاء والتحذيرات"),
            "batch": batch,
            "page_obj": page_obj,
            "selected_severity": severity or "",
        },
    )


@attendance_approve_permission_required
@require_POST
def attendance_import_approve(request: HttpRequest, batch_id) -> HttpResponse:
    batch = _get_batch(batch_id)
    form = AttendanceImportApprovalForm(request.POST)
    if not form.is_valid():
        messages.error(request, "يجب تأكيد مراجعة المعاينة قبل الاعتماد.")
        return redirect("attendance:import_preview", batch_id=batch.id)
    try:
        count = approve_attendance_import(batch, approved_by=request.user)
    except AttendanceImportServiceError as exc:
        messages.error(request, exc.message_ar)
    else:
        messages.success(request, f"تم اعتماد الدفعة وإنشاء {count} سجل حضور خام بنجاح.")
    return redirect("attendance:import_detail", batch_id=batch.id)


@system_admin_required
@require_POST
def attendance_import_delete(request: HttpRequest, batch_id) -> HttpResponse:
    batch = _get_batch(batch_id)
    expected_name = redact_potential_national_ids(
        batch.display_name or batch.original_filename
    )
    form = AttendanceImportDeleteForm(request.POST, expected_name=expected_name)
    if not form.is_valid():
        messages.error(request, "تعذر الحذف: تحقق من اسم الملف وسبب الحذف.")
        return redirect("attendance:import_list")
    try:
        delete_attendance_import(
            batch,
            deleted_by=request.user,
            reason=form.cleaned_data["reason"],
        )
    except AttendanceImportServiceError as exc:
        messages.error(request, exc.message_ar)
        return redirect("attendance:import_detail", batch_id=batch.id)
    messages.success(request, "تم حذف دفعة الاستيراد غير المعتمدة.")
    return redirect("attendance:import_list")


@system_admin_required
@require_POST
def attendance_import_update(request: HttpRequest, batch_id) -> HttpResponse:
    batch = _get_batch(batch_id)
    form = AttendanceImportMetadataForm(request.POST)
    if not form.is_valid():
        messages.error(request, "تعذر تعديل بيانات الملف. تحقق من الحقول المطلوبة.")
        return redirect("attendance:import_list")
    update_attendance_import_metadata(
        batch,
        display_name=form.cleaned_data["display_name"],
        source_period_title=form.cleaned_data["source_period_title"],
        reason=form.cleaned_data["reason"],
        updated_by=request.user,
    )
    messages.success(request, "تم تحديث اسم الملف وعنوان الفترة.")
    return redirect("attendance:import_list")


@system_admin_required
@require_POST
def attendance_import_archive(request: HttpRequest, batch_id) -> HttpResponse:
    batch = _get_batch(batch_id)
    form = AttendanceImportArchiveForm(request.POST)
    if not form.is_valid():
        messages.error(request, "سبب الأرشفة مطلوب.")
        return redirect("attendance:import_list")
    try:
        archive_attendance_import(
            batch, archived_by=request.user, reason=form.cleaned_data["reason"]
        )
    except AttendanceImportServiceError as exc:
        messages.error(request, exc.message_ar)
    else:
        messages.success(request, "تمت أرشفة الملف واستبعاده من التقارير.")
    return redirect("attendance:import_list")


@system_admin_required
@require_POST
def attendance_import_restore(request: HttpRequest, batch_id) -> HttpResponse:
    batch = _get_batch(batch_id)
    form = AttendanceImportArchiveForm(request.POST)
    if not form.is_valid():
        messages.error(request, "سبب الاستعادة مطلوب.")
        return redirect("attendance:import_list")
    try:
        restore_attendance_import(
            batch, restored_by=request.user, reason=form.cleaned_data["reason"]
        )
    except AttendanceImportServiceError as exc:
        messages.error(request, exc.message_ar)
    else:
        messages.success(request, "تمت استعادة الملف إلى التقارير.")
    return redirect("attendance:import_list")


# Daily attendance and reporting views
from django.db.models import Count, Q, Sum
from django.utils.dateparse import parse_date

from organization.models import Department, Employee, EmploymentAssignment
from organization.selectors import department_ids_in_user_scope

from .models import CalculationRun, DailyAttendanceResult
from .services.calculation import AttendanceCalculationError, calculate_all

ATTENDANCE_VIEW_PERMISSION = "attendance.view"
ATTENDANCE_REPORT_PERMISSION = "attendance.reports"
ATTENDANCE_CALCULATE_PERMISSION = "attendance.calculate"


def _can_view_attendance(user) -> bool:
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    return user_has_business_permission(user, ATTENDANCE_VIEW_PERMISSION) or bool(
        department_ids_in_user_scope(user)
    )


def attendance_view_required(view_func):
    @wraps(view_func)
    def wrapped(request: HttpRequest, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not _can_view_attendance(request.user):
            return render(request, "core/errors/403.html", status=403)
        return view_func(request, *args, **kwargs)

    return wrapped


def _result_queryset_for_user(user):
    qs = DailyAttendanceResult.objects.filter(
        is_current=True,
        source_record__import_row__batch__archived_at__isnull=True,
    ).select_related(
        "employee",
        "employee__identity",
        "department",
        "primary_location",
        "calculation_run",
    )
    if user.is_superuser:
        return qs
    allowed_ids = department_ids_in_user_scope(user)
    if not allowed_ids:
        return qs.none()
    today = timezone.localdate()
    return qs.filter(
        Q(department_id__in=allowed_ids)
        | (
            Q(department__isnull=True)
            & Q(employee__employment_assignments__department_id__in=allowed_ids)
            & Q(employee__employment_assignments__is_primary=True)
            & Q(employee__employment_assignments__valid_from__lte=today)
            & (
                Q(employee__employment_assignments__valid_to__isnull=True)
                | Q(employee__employment_assignments__valid_to__gt=today)
            )
        )
    ).distinct()


def _minutes_text(value: int) -> str:
    value = int(value or 0)
    hours, minutes = divmod(value, 60)
    return f"{hours:02d}:{minutes:02d}"


def _national_id_display(employee: Employee, user) -> str:
    try:
        identity = employee.identity
    except ObjectDoesNotExist:
        return "غير متاح"
    if not user.is_superuser:
        return f"******{identity.national_id_last4}"
    try:
        return decrypt_sensitive_text(
            bytes(identity.national_id_encrypted),
            context=f"employee-national-id:{employee.id}",
            key_version=identity.encryption_key_version,
        )
    except (ValueError, SecurityConfigurationError):
        return "غير متاح"


def _filter_by_employee_search(qs, search: str):
    search = search.strip()
    if not search:
        return qs
    try:
        normalized = normalize_national_id(search)
    except ValueError:
        return qs.filter(employee__full_name_ar__icontains=search)
    return qs.filter(employee__identity__national_id_hash=national_id_digest(normalized))


def _employee_search_display(search: str) -> str:
    try:
        normalize_national_id(search)
    except ValueError:
        return search
    return ""


def _current_department_map(employee_ids) -> dict:
    today = timezone.localdate()
    return {
        employee_id: department_name
        for employee_id, department_name in EmploymentAssignment.objects.filter(
            employee_id__in=employee_ids,
            is_primary=True,
            valid_from__lte=today,
        )
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gt=today))
        .order_by("employee_id", "valid_from")
        .values_list("employee_id", "department__name_ar")
    }


def _filter_by_department(qs, department_id):
    if not department_id:
        return qs
    today = timezone.localdate()
    return qs.filter(
        Q(department_id=department_id)
        | (
            Q(department__isnull=True)
            & Q(employee__employment_assignments__department_id=department_id)
            & Q(employee__employment_assignments__is_primary=True)
            & Q(employee__employment_assignments__valid_from__lte=today)
            & (
                Q(employee__employment_assignments__valid_to__isnull=True)
                | Q(employee__employment_assignments__valid_to__gt=today)
            )
        )
    ).distinct()


def _department_choices(base_qs):
    today = timezone.localdate()
    result_department_ids = base_qs.values_list("department_id", flat=True)
    current_department_ids = EmploymentAssignment.objects.filter(
        employee_id__in=base_qs.values_list("employee_id", flat=True),
        is_primary=True,
        valid_from__lte=today,
    ).filter(Q(valid_to__isnull=True) | Q(valid_to__gt=today)).values_list(
        "department_id", flat=True
    )
    return Department.objects.filter(
        Q(id__in=result_department_ids) | Q(id__in=current_department_ids)
    ).order_by("name_ar").distinct()


def _employee_choices(base_qs, user):
    employees = list(
        Employee.objects.select_related("identity")
        .filter(id__in=base_qs.values_list("employee_id", flat=True))
        .order_by("full_name_ar")
        .distinct()
    )
    employee_ids = [employee.id for employee in employees]
    latest_departments = {
        employee_id: department_name
        for employee_id, department_name in base_qs.filter(employee_id__in=employee_ids)
        .order_by("employee_id", "attendance_date")
        .values_list("employee_id", "department__name_ar")
    }
    current_departments = _current_department_map(employee_ids)
    for employee in employees:
        employee.ui_national_id = _national_id_display(employee, user)
        employee.ui_department = (
            latest_departments.get(employee.id)
            or current_departments.get(employee.id)
            or "—"
        )
    return employees


def _result_display(result: DailyAttendanceResult, user, current_departments) -> dict:
    return {
        "object": result,
        "employee": result.employee.full_name_ar,
        "department": (
            result.department.name_ar
            if result.department
            else current_departments.get(result.employee_id, "—")
        ),
        "national_id": _national_id_display(result.employee, user),
        "date": result.attendance_date,
        "check_in": timezone.localtime(result.first_check_in_at).strftime("%H:%M") if result.first_check_in_at else "—",
        "check_out": timezone.localtime(result.last_check_out_at).strftime("%H:%M") if result.last_check_out_at else "—",
        "scheduled": _minutes_text(result.scheduled_minutes),
        "worked": _minutes_text(result.worked_minutes),
        "late": _minutes_text(result.late_minutes),
        "early_leave": _minutes_text(result.early_leave_minutes),
        "shortfall": _minutes_text(result.shortfall_minutes),
        "status": result.get_attendance_status_display(),
        "location_status": (
            "موقع الحضور والانصراف مختلفان"
            if result.location_status == DailyAttendanceResult.LocationStatus.BOTH_OUTSIDE
            else "تعذر التحقق من الموقعين"
            if result.location_status == DailyAttendanceResult.LocationStatus.UNKNOWN
            else "موقع الحضور والانصراف متطابقان"
        ),
    }


def _records_breadcrumb(label: str):
    return (
        {"label": "الرئيسية", "url_name": "core:dashboard"},
        {"label": "سجل الحضور", "url_name": "attendance:record_list"},
        {"label": label},
    )


@attendance_view_required
@require_http_methods(["GET", "POST"])
def attendance_record_list(request: HttpRequest) -> HttpResponse:
    filters = request.POST if request.method == "POST" else request.GET
    qs = _result_queryset_for_user(request.user)
    employee_id = filters.get("employee")
    department_id = filters.get("department")
    status = filters.get("status")
    location_status = filters.get("location_status")
    employee_search = filters.get("employee_search", "")
    date_from = parse_date(filters.get("date_from") or "")
    date_to = parse_date(filters.get("date_to") or "")

    if employee_id:
        qs = qs.filter(employee_id=employee_id)
    qs = _filter_by_department(qs, department_id)
    if status in DailyAttendanceResult.AttendanceStatus.values:
        qs = qs.filter(attendance_status=status)
    if location_status in DailyAttendanceResult.LocationStatus.values:
        qs = qs.filter(location_status=location_status)
    if date_from:
        qs = qs.filter(attendance_date__gte=date_from)
    if date_to:
        qs = qs.filter(attendance_date__lte=date_to)
    qs = _filter_by_employee_search(qs, employee_search)

    page_obj = Paginator(qs.order_by("-attendance_date", "employee__full_name_ar"), 40).get_page(
        request.GET.get("page")
    )
    current_departments = _current_department_map(
        [row.employee_id for row in page_obj.object_list]
    )
    rows = tuple(
        _result_display(row, request.user, current_departments)
        for row in page_obj.object_list
    )
    visible_base = _result_queryset_for_user(request.user)
    employees = _employee_choices(visible_base, request.user)
    departments = _department_choices(visible_base)

    return render(
        request,
        "attendance/records/list.html",
        {
            "page_title": "سجل الحضور اليومي",
            "page_description": "نتائج الحضور المحتسبة مع التصفية حسب الموظف والقسم والفترة.",
            "breadcrumb_items": _records_breadcrumb("السجل اليومي"),
            "page_obj": page_obj,
            "rows": rows,
            "employees": employees,
            "departments": departments,
            "attendance_status_choices": DailyAttendanceResult.AttendanceStatus.choices,
            "location_status_choices": DailyAttendanceResult.LocationStatus.choices,
            "filters": filters,
            "employee_search": _employee_search_display(employee_search),
        },
    )


@attendance_view_required
@require_GET
def report_overview(request: HttpRequest) -> HttpResponse:
    qs = _result_queryset_for_user(request.user)
    date_from = parse_date(request.GET.get("date_from") or "")
    date_to = parse_date(request.GET.get("date_to") or "")
    if date_from:
        qs = qs.filter(attendance_date__gte=date_from)
    if date_to:
        qs = qs.filter(attendance_date__lte=date_to)

    outside_values = {DailyAttendanceResult.LocationStatus.BOTH_OUTSIDE}
    summary = qs.aggregate(
        records=Count("id"),
        employees=Count("employee", distinct=True),
        worked=Sum("worked_minutes"),
        late=Sum("late_minutes"),
        early_leave=Sum("early_leave_minutes"),
        shortfall=Sum("shortfall_minutes"),
    )
    summary["absent"] = qs.filter(attendance_status=DailyAttendanceResult.AttendanceStatus.ABSENT).count()
    summary["outside"] = qs.filter(location_status__in=outside_values).count()
    summary["worked_text"] = _minutes_text(summary.get("worked") or 0)
    summary["late_text"] = _minutes_text(summary.get("late") or 0)
    summary["early_leave_text"] = _minutes_text(summary.get("early_leave") or 0)
    summary["shortfall_text"] = _minutes_text(summary.get("shortfall") or 0)

    latest_runs = CalculationRun.objects.select_related("import_batch", "requested_by")[:8]
    return render(
        request,
        "attendance/reports/overview.html",
        {
            "page_title": "تقارير الحضور والانضباط",
            "page_description": "ملخص مؤشرات الحضور والتأخر وتنبيهات اختلاف موقعي الحضور والانصراف.",
            "breadcrumb_items": (
                {"label": "الرئيسية", "url_name": "core:dashboard"},
                {"label": "التقارير"},
            ),
            "summary": summary,
            "latest_runs": latest_runs,
            "filters": request.GET,
        },
    )


@attendance_view_required
@require_http_methods(["GET", "POST"])
def outside_location_report(request: HttpRequest) -> HttpResponse:
    filters = request.POST if request.method == "POST" else request.GET
    outside_values = (DailyAttendanceResult.LocationStatus.BOTH_OUTSIDE,)
    qs = _result_queryset_for_user(request.user)
    department_id = filters.get("department")
    employee_id = filters.get("employee")
    employee_search = filters.get("employee_search", "")
    date_from = parse_date(filters.get("date_from") or "")
    date_to = parse_date(filters.get("date_to") or "")
    details_kind = filters.get("details")
    details_employee_id = filters.get("details_employee")
    qs = _filter_by_department(qs, department_id)
    if employee_id:
        qs = qs.filter(employee_id=employee_id)
    if date_from:
        qs = qs.filter(attendance_date__gte=date_from)
    if date_to:
        qs = qs.filter(attendance_date__lte=date_to)
    qs = _filter_by_employee_search(qs, employee_search)

    detail_filters = {
        "outside": Q(location_status__in=outside_values),
        "absent": Q(attendance_status=DailyAttendanceResult.AttendanceStatus.ABSENT),
        "automatic_checkout": Q(source_status__icontains="انصراف تلقائي"),
        "permissions": Q(source_status__icontains="استئذان"),
    }
    detail_labels = {
        "outside": "تنبيهات اختلاف موقع الحضور والانصراف",
        "absent": "أيام الغياب",
        "automatic_checkout": "حالات الانصراف التلقائي",
        "permissions": "الاستئذانات",
    }
    relevant_filter = Q()
    for condition in detail_filters.values():
        relevant_filter |= condition

    details_employee = None
    details_label = ""
    if details_employee_id and details_kind in detail_filters:
        details_qs = qs.filter(detail_filters[details_kind])
        details_employee = get_object_or_404(
            Employee.objects.filter(id__in=qs.values_list("employee_id", flat=True)),
            pk=details_employee_id,
        )
        details_label = detail_labels[details_kind]
        report_rows = details_qs.filter(employee=details_employee).order_by("-attendance_date")
    else:
        report_rows = (
            qs.filter(relevant_filter)
            .values("employee_id", "employee__full_name_ar")
            .annotate(
                violation_days=Count(
                    "attendance_date",
                    filter=Q(location_status__in=outside_values),
                    distinct=True,
                ),
                absence_days=Count(
                    "attendance_date",
                    filter=Q(attendance_status=DailyAttendanceResult.AttendanceStatus.ABSENT),
                    distinct=True,
                ),
                automatic_checkout_days=Count(
                    "attendance_date",
                    filter=Q(source_status__icontains="انصراف تلقائي"),
                    distinct=True,
                ),
                permission_days=Count(
                    "attendance_date",
                    filter=Q(source_status__icontains="استئذان"),
                    distinct=True,
                ),
            )
            .order_by("employee__full_name_ar")
        )

    page_obj = Paginator(report_rows, 40).get_page(request.GET.get("page"))
    if details_employee:
        current_departments = _current_department_map(
            [result.employee_id for result in page_obj.object_list]
        )
        for result in page_obj.object_list:
            result.employee.ui_national_id = _national_id_display(
                result.employee, request.user
            )
            result.ui_department_name = (
                result.department.name_ar
                if result.department
                else current_departments.get(result.employee_id, "—")
            )
            result.ui_location_status = (
                "موقع الحضور والانصراف مختلفان"
                if result.location_status == DailyAttendanceResult.LocationStatus.BOTH_OUTSIDE
                else "تعذر التحقق من الموقعين"
            )
    else:
        employee_ids = [row["employee_id"] for row in page_obj.object_list]
        employee_map = {
            employee.id: employee
            for employee in Employee.objects.select_related("identity").filter(
                id__in=employee_ids
            )
        }
        latest_departments = {
            employee_id: department_name
            for employee_id, department_name in qs.filter(employee_id__in=employee_ids)
            .order_by("employee_id", "attendance_date")
            .values_list("employee_id", "department__name_ar")
        }
        current_departments = _current_department_map(employee_ids)
        for row in page_obj.object_list:
            employee = employee_map[row["employee_id"]]
            row["national_id"] = _national_id_display(employee, request.user)
            row["department_name"] = (
                latest_departments.get(row["employee_id"])
                or current_departments.get(row["employee_id"])
                or "—"
            )
    visible_base = _result_queryset_for_user(request.user)
    employees = _employee_choices(visible_base, request.user)
    departments = _department_choices(visible_base)

    return render(
        request,
        "attendance/reports/outside_location.html",
        {
            "page_title": "اختلاف موقع الحضور والانصراف",
            "page_description": "تنبيهات الأيام التي يختلف فيها موقع الحضور عن موقع الانصراف، دون إنشاء إفادة.",
            "breadcrumb_items": (
                {"label": "الرئيسية", "url_name": "core:dashboard"},
                {"label": "التقارير", "url_name": "attendance:report_overview"},
                {"label": "اختلاف الموقعين"},
            ),
            "page_obj": page_obj,
            "employees": employees,
            "departments": departments,
            "filters": filters,
            "employee_search": _employee_search_display(employee_search),
            "details_employee": details_employee,
            "details_kind": details_kind,
            "details_label": details_label,
        },
    )


@_permission_required(ATTENDANCE_CALCULATE_PERMISSION)
@require_POST
def run_attendance_calculation(request: HttpRequest) -> HttpResponse:
    try:
        summary = calculate_all(requested_by=request.user)
    except AttendanceCalculationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"تم إنشاء {summary.created} نتيجة حضور يومية بنجاح.")
    return redirect("attendance:report_overview")
