from __future__ import annotations

import uuid

from attendance.models import ImportBatch
from organization.access import user_has_business_permission
from organization.models import Department


ATTENDANCE_PERIOD_SESSION_KEY = "attendance_period_id"


def user_can_select_attendance_period(user) -> bool:
    if not user.is_authenticated or not user.is_active:
        return False
    return (
        user.is_superuser
        or user_has_business_permission(user, "clarifications.view_all")
        or Department.objects.filter(
            department_head__user=user,
            is_active=True,
            archived_at__isnull=True,
        ).exists()
    )


def available_attendance_periods():
    return ImportBatch.objects.filter(
        status=ImportBatch.Status.APPROVED,
        archived_at__isnull=True,
        period_start__isnull=False,
        period_end__isnull=False,
    ).order_by("-approved_at", "-created_at")


def selected_attendance_period(request):
    periods = available_attendance_periods()
    raw_id = request.session.get(ATTENDANCE_PERIOD_SESSION_KEY)
    selected = None
    if raw_id:
        try:
            selected_id = uuid.UUID(str(raw_id))
        except ValueError:
            selected_id = None
        if selected_id:
            selected = periods.filter(pk=selected_id).first()
    if selected is None:
        selected = periods.first()
        if selected:
            request.session[ATTENDANCE_PERIOD_SESSION_KEY] = str(selected.id)
        else:
            request.session.pop(ATTENDANCE_PERIOD_SESSION_KEY, None)
    return selected


def filter_results_for_period(queryset, period):
    if period is None:
        return queryset.none()
    return queryset.filter(source_record__import_row__batch=period)
