from .navigation import get_navigation_groups
from django.core.exceptions import ObjectDoesNotExist
from organization.access import user_has_business_permission
from .periods import (
    available_attendance_periods,
    selected_attendance_period,
    user_can_select_attendance_period,
)


EMPLOYEE_NAVIGATION = (
    {
        "label": "بوابة الموظف",
        "items": (
            {
                "name": "employee_portal",
                "url_name": "violations:employee_portal",
                "label": "بياناتي وحضوري",
                "icon": "contact",
                "active_url_names": ("employee_portal", "employee_clarification"),
            },
        ),
    },
)


def application_shell(request):
    """Expose application-shell navigation without querying the database."""

    user = getattr(request, "user", None)
    session = getattr(request, "session", {})
    employee_portal_only = bool(session.get("employee_portal_mode"))
    if not employee_portal_only and user is not None and user.is_authenticated and not user.is_superuser:
        try:
            user.employee
        except ObjectDoesNotExist:
            pass
        else:
            employee_portal_only = not user.role_assignments.filter(
                is_active=True, valid_to__isnull=True
            ).exists()

    navigation = EMPLOYEE_NAVIGATION if employee_portal_only else get_navigation_groups()
    if (
        not employee_portal_only
        and user is not None
        and user.is_authenticated
        and not user.is_superuser
    ):
        navigation = tuple(
            {
                **group,
                "items": tuple(
                    item
                    for item in group["items"]
                    if not item.get("required_permission")
                    or user_has_business_permission(user, item["required_permission"])
                ),
            }
            for group in navigation
        )

    return {
        "application_name": "منصة الحضور والانضباط الوظيفي",
        "application_description": "إدارة وتحليل الحضور والمخالفات والمعالجات",
        "navigation_groups": navigation,
        "employee_portal_only": employee_portal_only,
        "can_select_attendance_period": (
            not employee_portal_only
            and user is not None
            and user_can_select_attendance_period(user)
        ),
        "attendance_periods": (
            available_attendance_periods()
            if not employee_portal_only
            and user is not None
            and user_can_select_attendance_period(user)
            else ()
        ),
        "selected_attendance_period": (
            selected_attendance_period(request)
            if not employee_portal_only
            and user is not None
            and user.is_authenticated
            else None
        ),
    }
