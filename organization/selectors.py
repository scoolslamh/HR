from __future__ import annotations

from django.db import models
from django.db.models import QuerySet
from django.utils import timezone

from .access import active_department_scopes, user_has_active_role
from .models import Department, Employee, UserDepartmentScope


def _descendant_department_ids(root_ids: set) -> set:
    """Return roots and descendants without relying on a database-specific CTE."""

    department_ids = set(root_ids)
    frontier = set(root_ids)
    while frontier:
        children = set(
            Department.objects.filter(parent_id__in=frontier).values_list(
                "id", flat=True
            )
        )
        frontier = children - department_ids
        department_ids.update(frontier)
    return department_ids


def employees_in_user_department_scope(user, *, at=None) -> QuerySet[Employee]:
    """Employees visible through active department scopes and assignments only."""

    if not user.is_authenticated or not user.is_active:
        return Employee.objects.none()
    if user.is_superuser or user_has_active_role(user, "general_manager", at=at):
        return Employee.objects.all()

    at = at or timezone.now()
    effective_date = timezone.localdate(at)
    scopes = active_department_scopes(user, at=at)

    direct_ids = set(
        scopes.filter(include_descendants=False).values_list(
            "department_id", flat=True
        )
    )
    descendant_roots = set(
        scopes.filter(include_descendants=True).values_list(
            "department_id", flat=True
        )
    )
    allowed_department_ids = direct_ids | _descendant_department_ids(
        descendant_roots
    )
    if not allowed_department_ids:
        return Employee.objects.none()

    return Employee.objects.filter(
        employment_assignments__department_id__in=allowed_department_ids,
        employment_assignments__is_primary=True,
        employment_assignments__valid_from__lte=effective_date,
    ).filter(
        models.Q(employment_assignments__valid_to__isnull=True)
        | models.Q(employment_assignments__valid_to__gt=effective_date)
    ).distinct()


def department_ids_in_user_scope(user, *, access_levels=None, at=None) -> set:
    if not user.is_authenticated or not user.is_active:
        return set()
    if user.is_superuser or user_has_active_role(user, "general_manager", at=at):
        return set(Department.objects.values_list("id", flat=True))

    scopes = active_department_scopes(
        user,
        access_levels=access_levels,
        at=at,
    )
    direct_ids = set(
        scopes.filter(include_descendants=False).values_list(
            "department_id", flat=True
        )
    )
    descendant_roots = set(
        scopes.filter(include_descendants=True).values_list(
            "department_id", flat=True
        )
    )
    return direct_ids | _descendant_department_ids(descendant_roots)


def employee_directory_queryset(user, *, at=None) -> QuerySet[Employee]:
    return employees_in_user_department_scope(user, at=at).select_related("identity")


def current_assignment_for(employee: Employee, *, effective_date=None):
    effective_date = effective_date or timezone.localdate()
    return (
        employee.employment_assignments.select_related(
            "department", "job_title", "manager_employee"
        )
        .filter(is_primary=True, valid_from__lte=effective_date)
        .filter(models.Q(valid_to__isnull=True) | models.Q(valid_to__gt=effective_date))
        .order_by("-valid_from")
        .first()
    )


def current_primary_location_for(employee: Employee, *, effective_date=None):
    effective_date = effective_date or timezone.localdate()
    return (
        employee.primary_location_assignments.select_related("location")
        .filter(valid_from__lte=effective_date)
        .filter(models.Q(valid_to__isnull=True) | models.Q(valid_to__gt=effective_date))
        .order_by("-valid_from")
        .first()
    )
