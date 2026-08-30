from __future__ import annotations

from django.db.models import Q, QuerySet


WEEKLY_HOLIDAY_FILTER = (
    Q(source_status__icontains="عطلة الأسبوع")
    | Q(source_status__icontains="عطلة الاسبوع")
    | Q(source_status__icontains="عطله الأسبوع")
    | Q(source_status__icontains="عطله الاسبوع")
)


def exclude_weekly_holidays(queryset: QuerySet) -> QuerySet:
    """Keep imported holiday rows intact while omitting them from user reports."""

    return queryset.exclude(WEEKLY_HOLIDAY_FILTER)
