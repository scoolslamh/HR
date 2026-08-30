from __future__ import annotations

from django.db.models import Count, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from attendance.models import DailyAttendanceResult, ImportBatch
from organization.access import user_has_business_permission
from organization.models import Employee, EmploymentAssignment
from organization.selectors import employees_in_user_department_scope


OUTSIDE_LOCATION_STATUSES = (
    DailyAttendanceResult.LocationStatus.BOTH_OUTSIDE,
)


def _date_label(value) -> str:
    return value.strftime("%Y/%m/%d")


def employees_for_dashboard_user(user):
    can_view_all = user.is_superuser or user_has_business_permission(
        user, "clarifications.view_all"
    )
    if can_view_all:
        employees = Employee.objects.all()
    else:
        scoped_employee_ids = employees_in_user_department_scope(user).values("id")
        employees = Employee.objects.filter(
            Q(id__in=scoped_employee_ids)
            | Q(
                employment_assignments__department__department_head__user=user,
                employment_assignments__is_primary=True,
            )
        ).distinct()
    return employees.filter(
        employment_status=Employee.EmploymentStatus.ACTIVE,
        archived_at__isnull=True,
    )


def dashboard_context_for_user(user, *, attendance_period=None) -> dict:
    employees = employees_for_dashboard_user(user)
    employee_ids = employees.values_list("id", flat=True)
    results = DailyAttendanceResult.objects.filter(
        is_current=True,
        employee_id__in=employee_ids,
        source_record__import_row__batch__archived_at__isnull=True,
    )
    if attendance_period is None:
        results = results.none()
    else:
        results = results.filter(
            source_record__import_row__batch=attendance_period
        )
    absent_count = results.filter(
        attendance_status=DailyAttendanceResult.AttendanceStatus.ABSENT
    ).count()
    annual_leave_filter = Q(source_status__icontains="إجازة سنوية") | Q(
        source_status__icontains="اجازة سنوية"
    )
    permission_filter = Q(source_status__icontains="استئذان")
    work_mission_filter = Q(source_status__icontains="مهمة عمل") | Q(
        source_status__icontains="مهمه عمل"
    )
    annual_leave_count = results.filter(annual_leave_filter).count()
    permission_count = results.filter(permission_filter).count()
    outside_count = results.filter(
        location_status__in=OUTSIDE_LOCATION_STATUSES
    ).count()
    work_mission_count = results.filter(work_mission_filter).count()

    def top_employee_rows(filtered_results):
        return tuple(
            (row["employee__full_name_ar"], row["total"])
            for row in filtered_results.values("employee__full_name_ar")
            .annotate(total=Count("id"))
            .order_by("-total", "employee__full_name_ar")[:3]
        )

    top_absent_employees = top_employee_rows(
        results.filter(
            attendance_status=DailyAttendanceResult.AttendanceStatus.ABSENT
        )
    )
    top_permission_employees = top_employee_rows(results.filter(permission_filter))
    top_work_mission_employees = top_employee_rows(results.filter(work_mission_filter))

    import_rows = ()
    if user_has_business_permission(user, "attendance.import"):
        import_rows = tuple(
            (
                batch.original_filename,
                batch.source_period_title or "غير محددة",
                batch.get_status_display(),
                batch.created_at.strftime("%Y/%m/%d %H:%M"),
            )
            for batch in ImportBatch.objects.filter(archived_at__isnull=True)[:3]
        )

    current_department_name = (
        EmploymentAssignment.objects.filter(
            employee_id=OuterRef("employee_id"),
            is_primary=True,
            valid_from__lte=timezone.localdate(),
        )
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gt=timezone.localdate()))
        .order_by("-valid_from")
        .values("department__name_ar")[:1]
    )
    unit_statuses = []
    unit_rows = (
        results.annotate(
            effective_department_name=Coalesce(
                "department__name_ar",
                Subquery(current_department_name),
            )
        )
        .exclude(effective_department_name__isnull=True)
        .values("effective_department_name")
        .annotate(
            total=Count("id"),
            disciplined=Count(
                "id",
                filter=Q(
                    attendance_status=DailyAttendanceResult.AttendanceStatus.PRESENT,
                    late_minutes=0,
                    early_leave_minutes=0,
                )
                & ~Q(location_status__in=OUTSIDE_LOCATION_STATUSES),
            ),
        )
        .order_by("effective_department_name")
    )
    for row in unit_rows:
        percentage = round((row["disciplined"] / row["total"]) * 100) if row["total"] else 0
        color = "green" if percentage >= 95 else "blue" if percentage >= 90 else "yellow"
        status = "مستقر" if percentage >= 95 else "جيد" if percentage >= 90 else "للمتابعة"
        unit_statuses.append(
            {"name": row["effective_department_name"], "value": percentage, "status": status, "color": color}
        )

    return {
        "dashboard_stats": (
            {"title": "عدد الموظفين", "value": employees.count(), "icon": "users", "color": "blue", "hint": "ضمن النطاق التنظيمي"},
            {"title": "أيام الغياب", "value": absent_count, "icon": "user-x", "color": "red", "hint": "خلال الفترة المختارة"},
            {"title": "الإجازات السنوية", "value": annual_leave_count, "icon": "calendar-heart", "color": "green", "hint": "خلال الفترة المختارة"},
            {"title": "الاستئذانات", "value": permission_count, "icon": "door-open", "color": "yellow", "hint": "خلال الفترة المختارة"},
            {"title": "مهمات العمل", "value": work_mission_count, "icon": "briefcase-business", "color": "blue", "hint": "خلال الفترة المختارة", "url_name": "violations:work_mission_list"},
            {"title": "اختلاف مواقع التوقيع", "value": outside_count, "icon": "map-pin-off", "color": "yellow", "hint": "خلال الفترة المختارة"},
        ),
        "import_rows": import_rows,
        "can_view_imports": user_has_business_permission(user, "attendance.import"),
        "unit_statuses": tuple(unit_statuses),
        "top_absent_employees": top_absent_employees,
        "top_permission_employees": top_permission_employees,
        "top_work_mission_employees": top_work_mission_employees,
    }
