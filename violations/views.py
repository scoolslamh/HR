from __future__ import annotations

from functools import wraps
import uuid

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count, Max, Q
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from attendance.models import DailyAttendanceResult
from core.periods import selected_attendance_period
from organization.access import user_has_business_permission
from organization.models import Department
from organization.selectors import current_assignment_for, current_primary_location_for

from .forms import ClarificationReviewForm, EmployeeClarificationForm
from .models import ClarificationEvidence, ClarificationRequest
from .services import review_clarification, submit_employee_clarification


OUTSIDE_STATUSES = (
    DailyAttendanceResult.LocationStatus.BOTH_OUTSIDE,
)
CLOSED_STATUSES = (
    ClarificationRequest.Status.APPROVED,
    ClarificationRequest.Status.REJECTED,
    ClarificationRequest.Status.CANCELLED,
)


def employee_required(view_func):
    @wraps(view_func)
    def wrapped(request: HttpRequest, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), login_url="accounts:employee_login")
        try:
            employee = request.user.employee
        except ObjectDoesNotExist:
            return render(request, "core/errors/403.html", status=403)
        if (
            employee.employment_status != employee.EmploymentStatus.ACTIVE
            or employee.archived_at is not None
        ):
            return render(request, "core/errors/403.html", status=403)
        request.portal_employee = employee
        return view_func(request, *args, **kwargs)

    return wrapped


def _head_departments(user):
    try:
        employee = user.employee
    except Exception:
        return Department.objects.none()
    return Department.objects.filter(department_head=employee, is_active=True)


def _can_view_all(user) -> bool:
    return user.is_superuser or user_has_business_permission(user, "clarifications.view_all")


def _active_clarifications(attendance_period=None):
    queryset = ClarificationRequest.objects.filter(
        attendance_result__source_record__import_row__batch__archived_at__isnull=True
    )
    if attendance_period is None:
        return queryset.none()
    return queryset.filter(
        attendance_result__source_record__import_row__batch=attendance_period
    )


def _work_mission_results(attendance_period=None):
    queryset = DailyAttendanceResult.objects.filter(
        is_current=True,
        source_record__import_row__batch__archived_at__isnull=True,
    )
    if attendance_period is None:
        return queryset.none()
    return queryset.filter(
        source_record__import_row__batch=attendance_period
    ).filter(
        Q(source_status__icontains="مهمة عمل")
        | Q(source_status__icontains="مهمه عمل")
    )


@employee_required
@require_GET
def employee_portal(request: HttpRequest) -> HttpResponse:
    employee = request.portal_employee
    results = DailyAttendanceResult.objects.filter(
        employee=employee,
        is_current=True,
        source_record__import_row__batch__archived_at__isnull=True,
    )
    clarifications = ClarificationRequest.objects.filter(
        attendance_result__source_record__import_row__batch__archived_at__isnull=True,
        employee=employee,
    ).select_related(
        "attendance_result", "department"
    )
    stats = {
        "attendance": results.filter(attendance_status=DailyAttendanceResult.AttendanceStatus.PRESENT).count(),
        "absence": results.filter(attendance_status=DailyAttendanceResult.AttendanceStatus.ABSENT).count(),
        "late": results.filter(late_minutes__gt=0).count(),
        "permissions": results.filter(source_status__icontains="استئذان").count(),
        "work_missions": results.filter(
            Q(source_status__icontains="مهمة عمل")
            | Q(source_status__icontains="مهمه عمل")
        ).count(),
        "automatic_checkout": results.filter(source_status__icontains="انصراف تلقائي").count(),
        "outside": results.filter(location_status__in=OUTSIDE_STATUSES).count(),
        "open_clarifications": clarifications.exclude(
            status__in=CLOSED_STATUSES
        ).count(),
    }
    return render(
        request,
        "violations/employee_portal.html",
        {
            "page_title": "بياناتي",
            "page_description": "بيانات الموظف وسجل الحضور والإفادات الخاصة به.",
            "breadcrumb_items": ({"label": "بياناتي"},),
            "employee": employee,
            "assignment": current_assignment_for(employee),
            "primary_location": current_primary_location_for(employee),
            "stats": stats,
            "results": results.order_by("-attendance_date")[:30],
            "clarifications": clarifications[:20],
        },
    )


@employee_required
@require_http_methods(["GET", "POST"])
def employee_clarification(request: HttpRequest, clarification_id) -> HttpResponse:
    clarification = get_object_or_404(
        ClarificationRequest.objects.filter(
            attendance_result__source_record__import_row__batch__archived_at__isnull=True
        ).select_related("attendance_result", "department").prefetch_related("evidence_files"),
        pk=clarification_id,
        employee=request.portal_employee,
    )
    editable = clarification.status in {
        ClarificationRequest.Status.AWAITING_EMPLOYEE,
        ClarificationRequest.Status.RETURNED,
    }
    form = EmployeeClarificationForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and editable and form.is_valid():
        submit_employee_clarification(
            clarification=clarification,
            employee=request.portal_employee,
            explanation=form.cleaned_data["explanation"],
            evidence=form.cleaned_data["evidence"],
            actor=request.user,
        )
        messages.success(request, "تم إرسال الإفادة إلى رئيس القسم.")
        return redirect("violations:employee_portal")
    return render(
        request,
        "violations/employee_clarification.html",
        {
            "page_title": "تفاصيل الإفادة",
            "breadcrumb_items": ({"label": "بياناتي", "url_name": "violations:employee_portal"}, {"label": "الإفادة"}),
            "clarification": clarification,
            "form": form,
            "editable": editable,
        },
    )


@require_GET
def manager_dashboard(request: HttpRequest) -> HttpResponse:
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    departments = _head_departments(request.user)
    if (
        not request.user.is_superuser
        and (
            not departments.exists()
            or not user_has_business_permission(request.user, "clarifications.approve_department")
        )
    ):
        return render(request, "core/errors/403.html", status=403)
    attendance_period = selected_attendance_period(request)
    items = _active_clarifications(attendance_period).select_related("employee", "department", "attendance_result")
    if not request.user.is_superuser:
        items = items.filter(department__in=departments)
    summary = items.aggregate(
        total=Count("id"),
        open=Count("id", filter=~Q(status__in=CLOSED_STATUSES)),
        processed=Count("id", filter=Q(status__in=CLOSED_STATUSES)),
        awaiting_manager=Count("id", filter=Q(status=ClarificationRequest.Status.AWAITING_MANAGER)),
    )
    return render(request, "violations/manager_dashboard.html", {
        "page_title": "إفادات موظفي القسم",
        "page_description": "متابعة إفادات الموظفين واعتمادها ضمن نطاق القسم.",
        "breadcrumb_items": ({"label": "الإفادات"},),
        "summary": summary,
        "items": items[:100],
    })


@require_http_methods(["GET", "POST"])
def manager_review(request: HttpRequest, clarification_id) -> HttpResponse:
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    clarification = get_object_or_404(
        _active_clarifications(selected_attendance_period(request)).select_related("employee__user", "department__department_head__user", "attendance_result").prefetch_related("evidence_files"),
        pk=clarification_id,
    )
    allowed = request.user.is_superuser or (
        user_has_business_permission(request.user, "clarifications.approve_department")
        and clarification.department_id in set(_head_departments(request.user).values_list("id", flat=True))
    )
    if not allowed:
        return render(request, "core/errors/403.html", status=403)
    form = ClarificationReviewForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            review_clarification(
                clarification=clarification,
                actor=request.user,
                decision=form.cleaned_data["decision"],
                comment=form.cleaned_data["comment"],
            )
        except (ValueError, PermissionError) as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "تم حفظ قرار الإفادة.")
            return redirect("violations:manager_dashboard")
    return render(request, "violations/manager_review.html", {
        "page_title": "مراجعة إفادة الموظف",
        "breadcrumb_items": ({"label": "الإفادات", "url_name": "violations:manager_dashboard"}, {"label": "المراجعة"}),
        "clarification": clarification,
        "form": form,
    })


@require_GET
def executive_dashboard(request: HttpRequest) -> HttpResponse:
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    if not _can_view_all(request.user):
        return render(request, "core/errors/403.html", status=403)
    items = _active_clarifications(selected_attendance_period(request))
    summary = items.aggregate(
        total=Count("id"),
        approved=Count("id", filter=Q(status=ClarificationRequest.Status.APPROVED)),
        open=Count("id", filter=~Q(status__in=CLOSED_STATUSES)),
    )
    departments = items.values("department_id", "department__name_ar").annotate(
        total=Count("id"),
        approved=Count("id", filter=Q(status=ClarificationRequest.Status.APPROVED)),
        open=Count("id", filter=~Q(status__in=CLOSED_STATUSES)),
    ).order_by("department__name_ar")
    selected_department = (request.GET.get("department") or "").strip()
    detail_items = items.none()
    if selected_department:
        try:
            department_id = uuid.UUID(selected_department)
        except ValueError:
            selected_department = ""
        else:
            detail_items = items.filter(department_id=department_id).select_related(
                "employee", "department", "attendance_result"
            )[:100]
    return render(request, "violations/executive_dashboard.html", {
        "page_title": "المؤشر العام للإفادات",
        "page_description": "تفاصيل الإفادات المرسلة آليًا حسب الأقسام.",
        "breadcrumb_items": ({"label": "المؤشر العام للإفادات"},),
        "summary": summary,
        "departments": departments,
        "detail_items": detail_items,
        "selected_department": selected_department,
    })


@require_GET
def work_mission_list(request: HttpRequest) -> HttpResponse:
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    can_view_all = _can_view_all(request.user)
    departments = _head_departments(request.user)
    can_view_department = (
        departments.exists()
        and user_has_business_permission(request.user, "clarifications.approve_department")
    )
    if not can_view_all and not can_view_department:
        return render(request, "core/errors/403.html", status=403)

    results = _work_mission_results(selected_attendance_period(request))
    if not can_view_all:
        results = results.filter(department__in=departments)
    rows = (
        results.values(
            "employee_id",
            "employee__full_name_ar",
            "department__name_ar",
        )
        .annotate(mission_count=Count("id"), latest_date=Max("attendance_date"))
        .order_by("employee__full_name_ar")
    )
    page_obj = Paginator(rows, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "violations/work_mission_list.html",
        {
            "page_title": "مهمات العمل المسجلة",
            "page_description": "الموظفون الذين سجلت لهم مهمة عمل في الشيت ضمن نطاق صلاحيتك.",
            "breadcrumb_items": (
                {"label": "الرئيسية", "url_name": "core:dashboard"},
                {"label": "مهمات العمل"},
            ),
            "page_obj": page_obj,
            "mission_total": results.count(),
            "employee_total": rows.count(),
        },
    )


@require_GET
def evidence_download(request: HttpRequest, evidence_id) -> HttpResponse:
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    evidence = get_object_or_404(
        ClarificationEvidence.objects.select_related("clarification__employee", "clarification__department__department_head"),
        pk=evidence_id,
    )
    clarification = evidence.clarification
    is_owner = clarification.employee.user_id == request.user.id
    is_head = clarification.department and clarification.department.department_head and clarification.department.department_head.user_id == request.user.id
    if not (is_owner or is_head or _can_view_all(request.user)):
        raise Http404
    evidence.file.open("rb")
    return FileResponse(evidence.file, as_attachment=True, filename=evidence.original_filename)
