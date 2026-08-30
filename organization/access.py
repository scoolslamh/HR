from __future__ import annotations

from django.db.models import Q
from django.utils import timezone

from accounts.models import UserRole

from .models import UserDepartmentScope


def user_has_active_role(user, code: str, *, at=None) -> bool:
    if not user.is_authenticated or not user.is_active:
        return False
    at = at or timezone.now()
    return UserRole.objects.filter(
        user=user,
        role__code=code,
        role__is_active=True,
        is_active=True,
        valid_from__lte=at,
    ).filter(Q(valid_to__isnull=True) | Q(valid_to__gt=at)).exists()


def user_has_business_permission(user, code: str, *, at=None) -> bool:
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    at = at or timezone.now()
    if user_has_active_role(user, "general_manager", at=at):
        return True
    return (
        UserRole.objects.filter(
            user=user,
            is_active=True,
            valid_from__lte=at,
            role__is_active=True,
            role__permission_assignments__permission__code=code,
            role__permission_assignments__permission__is_active=True,
            role__permission_assignments__granted_at__lte=at,
            role__permission_assignments__revoked_at__isnull=True,
        )
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gt=at))
        .exists()
    )


def active_department_scopes(user, *, access_levels=None, at=None):
    at = at or timezone.now()
    scopes = UserDepartmentScope.objects.filter(
        user=user,
        valid_from__lte=at,
    ).filter(Q(valid_to__isnull=True) | Q(valid_to__gt=at))
    if access_levels:
        scopes = scopes.filter(access_level__in=access_levels)
    return scopes


def user_can_view_employee_directory(user) -> bool:
    if not user.is_authenticated or not user.is_active:
        return False
    return (
        user.is_superuser
        or user_has_business_permission(user, "employees.view_department")
        or active_department_scopes(user).exists()
    )


def user_can_manage_references(user) -> bool:
    return user_has_business_permission(user, "organization.manage")
