from __future__ import annotations

from django.db.models import Count, Max, Q

from attendance.models import DailyAttendanceResult, ImportBatch
from organization.access import user_has_business_permission
from organization.models import Employee
from organization.selectors import employees_in_user_department_scope


ARABIC_WEEKDAYS = (
    "الاثنين",
    "الثلاثاء",
    "الأربعاء",
    "الخميس",
    "الجمعة",
    "السبت",
    "الأحد",
)

OUTSIDE_LOCATION_STATUSES = (
    DailyAttendanceResult.LocationStatus.BOTH_OUTSIDE,
)


def _date_label(value) -> str:
    return value.strftime("%Y/%m/%d")


def _anomaly_label(result: DailyAttendanceResult) -> str:
    if result.attendance_status == DailyAttendanceResult.AttendanceStatus.ABSENT:
        return "غياب"
    if result.location_status in OUTSIDE_LOCATION_STATUSES:
        return "اختلاف موقع الحضور والانصراف"
    if result.late_minutes:
        return "تأخر عن الدوام"
    if result.early_leave_minutes:
        return "انصراف مبكر"
    return result.get_attendance_status_display()


def dashboard_context_for_user(user) -> dict:
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
    employees = employees.filter(
        employment_status=Employee.EmploymentStatus.ACTIVE,
        archived_at__isnull=True,
    )
    employee_ids = employees.values_list("id", flat=True)
    results = DailyAttendanceResult.objects.filter(
        is_current=True,
        employee_id__in=employee_ids,
        source_record__import_row__batch__archived_at__isnull=True,
    )
    latest_date = results.aggregate(value=Max("attendance_date"))["value"]
    latest_results = results.filter(attendance_date=latest_date) if latest_date else results.none()

    present_count = latest_results.filter(
        attendance_status=DailyAttendanceResult.AttendanceStatus.PRESENT
    ).count()
    absent_count = latest_results.filter(
        attendance_status=DailyAttendanceResult.AttendanceStatus.ABSENT
    ).count()
    late_count = latest_results.filter(late_minutes__gt=0).count()
    outside_count = latest_results.filter(
        location_status__in=OUTSIDE_LOCATION_STATUSES
    ).count()
    work_mission_count = results.filter(
        Q(source_status__icontains="مهمة عمل")
        | Q(source_status__icontains="مهمه عمل")
    ).count()
    latest_total = latest_results.count()
    date_hint = f"بيانات {_date_label(latest_date)}" if latest_date else "لا توجد نتائج حضور محتسبة"

    weekly_rows = list(
        results.values("attendance_date")
        .annotate(
            total=Count("id"),
            present=Count(
                "id",
                filter=Q(attendance_status=DailyAttendanceResult.AttendanceStatus.PRESENT),
            ),
        )
        .order_by("-attendance_date")[:5]
    )
    weekly_attendance = []
    for row in reversed(weekly_rows):
        attendance_date = row["attendance_date"]
        percentage = round((row["present"] / row["total"]) * 100) if row["total"] else 0
        weekly_attendance.append(
            {
                "day": f"{ARABIC_WEEKDAYS[attendance_date.weekday()]} {_date_label(attendance_date)}",
                "value": percentage,
                "count": row["present"],
            }
        )
    weekly_average = (
        round(sum(item["value"] for item in weekly_attendance) / len(weekly_attendance), 1)
        if weekly_attendance
        else 0
    )

    anomalies = (
        results.filter(
            Q(attendance_status=DailyAttendanceResult.AttendanceStatus.ABSENT)
            | Q(late_minutes__gt=0)
            | Q(early_leave_minutes__gt=0)
            | Q(location_status__in=OUTSIDE_LOCATION_STATUSES)
        )
        .select_related("employee")
        .order_by("-attendance_date", "employee__full_name_ar")[:5]
    )
    violation_rows = tuple(
        (
            result.employee.full_name_ar,
            _anomaly_label(result),
            "محتسبة",
            _date_label(result.attendance_date),
        )
        for result in anomalies
    )

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

    unit_statuses = []
    unit_rows = (
        latest_results.exclude(department__isnull=True)
        .values("department__name_ar")
        .annotate(
            total=Count("id"),
            present=Count(
                "id",
                filter=Q(attendance_status=DailyAttendanceResult.AttendanceStatus.PRESENT),
            ),
        )
        .order_by("department__name_ar")
    )
    for row in unit_rows:
        percentage = round((row["present"] / row["total"]) * 100) if row["total"] else 0
        color = "green" if percentage >= 95 else "blue" if percentage >= 90 else "yellow"
        status = "مستقر" if percentage >= 95 else "جيد" if percentage >= 90 else "للمتابعة"
        unit_statuses.append(
            {"name": row["department__name_ar"], "value": percentage, "status": status, "color": color}
        )

    recent_alerts = []
    if latest_date:
        recent_alerts.append(
            {
                "title": f"تم احتساب {latest_total} سجل حضور في {_date_label(latest_date)}",
                "time": "آخر يوم متاح",
                "icon": "calendar-check",
                "color": "green",
            }
        )
        if absent_count:
            recent_alerts.append(
                {"title": f"يوجد {absent_count} حالة غياب", "time": date_hint, "icon": "user-x", "color": "red"}
            )
        if outside_count:
            recent_alerts.append(
                {"title": f"يوجد {outside_count} تنبيه اختلاف موقع الحضور والانصراف", "time": date_hint, "icon": "map-pin-off", "color": "yellow"}
            )

    return {
        "dashboard_stats": (
            {"title": "إجمالي الموظفين", "value": employees.count(), "icon": "users", "color": "blue", "hint": "ضمن نطاقك التنظيمي"},
            {"title": "الحضور", "value": present_count, "icon": "user-check", "color": "green", "hint": date_hint},
            {"title": "المتأخرون", "value": late_count, "icon": "clock-alert", "color": "yellow", "hint": date_hint},
            {"title": "الغياب", "value": absent_count, "icon": "user-x", "color": "red", "hint": date_hint},
            {"title": "اختلاف الموقعين", "value": outside_count, "icon": "map-pin-off", "color": "yellow", "hint": date_hint},
            {"title": "مهمات العمل", "value": work_mission_count, "icon": "briefcase-business", "color": "blue", "hint": "اضغط لعرض الموظفين", "url_name": "violations:work_mission_list"},
        ),
        "latest_attendance_date": latest_date,
        "weekly_attendance": tuple(weekly_attendance),
        "weekly_average": weekly_average,
        "import_rows": import_rows,
        "can_view_imports": user_has_business_permission(user, "attendance.import"),
        "violation_rows": violation_rows,
        "recent_alerts": tuple(recent_alerts),
        "unit_statuses": tuple(unit_statuses),
    }
