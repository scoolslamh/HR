from __future__ import annotations

from django.db.models import Count, Q, Sum

from attendance.models import DailyAttendanceResult


def _minutes_text(value: int) -> str:
    hours, minutes = divmod(int(value or 0), 60)
    return f"{hours:02d}:{minutes:02d}"


def build_employee_report_summary(queryset) -> tuple[tuple[str, int | str], ...]:
    leave_filter = Q(source_status__icontains="إجازة") | Q(
        source_status__icontains="اجازة"
    )
    mission_filter = Q(source_status__icontains="مهمة عمل") | Q(
        source_status__icontains="مهمه عمل"
    )
    aggregate = queryset.aggregate(
        total=Count("id"),
        present=Count(
            "id",
            filter=Q(attendance_status=DailyAttendanceResult.AttendanceStatus.PRESENT),
        ),
        absent=Count(
            "id",
            filter=Q(attendance_status=DailyAttendanceResult.AttendanceStatus.ABSENT),
        ),
        incomplete=Count(
            "id",
            filter=Q(attendance_status=DailyAttendanceResult.AttendanceStatus.INCOMPLETE),
        ),
        leaves=Count("id", filter=leave_filter),
        permissions=Count("id", filter=Q(source_status__icontains="استئذان")),
        missions=Count("id", filter=mission_filter),
        delegation=Count("id", filter=Q(source_status__icontains="انتداب")),
        training=Count("id", filter=Q(source_status__icontains="تدريب")),
        outside=Count(
            "id",
            filter=Q(
                location_status=DailyAttendanceResult.LocationStatus.BOTH_OUTSIDE
            ),
        ),
        scheduled_minutes=Sum("scheduled_minutes"),
        worked_minutes=Sum("worked_minutes"),
        late_minutes=Sum("late_minutes"),
        early_leave_minutes=Sum("early_leave_minutes"),
        shortfall_minutes=Sum("shortfall_minutes"),
        overtime_minutes=Sum("overtime_minutes"),
    )
    return (
        ("إجمالي الأيام", aggregate["total"] or 0),
        ("أيام الحضور", aggregate["present"] or 0),
        ("أيام الغياب", aggregate["absent"] or 0),
        ("البصمات الناقصة", aggregate["incomplete"] or 0),
        ("الإجازات", aggregate["leaves"] or 0),
        ("الاستئذانات", aggregate["permissions"] or 0),
        ("مهمات العمل", aggregate["missions"] or 0),
        ("الانتداب", aggregate["delegation"] or 0),
        ("التدريب", aggregate["training"] or 0),
        ("اختلاف مواقع التوقيع", aggregate["outside"] or 0),
        ("الوقت المجدول", _minutes_text(aggregate["scheduled_minutes"])),
        ("العمل الفعلي", _minutes_text(aggregate["worked_minutes"])),
        ("التأخر", _minutes_text(aggregate["late_minutes"])),
        ("الانصراف المبكر", _minutes_text(aggregate["early_leave_minutes"])),
        ("نقص الدوام", _minutes_text(aggregate["shortfall_minutes"])),
        ("العمل الإضافي", _minutes_text(aggregate["overtime_minutes"])),
    )
