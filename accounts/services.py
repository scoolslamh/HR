from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from organization.models import Employee, UserDepartmentScope

from .models import Role, RolePermission, User, UserRole


def _user_snapshot(user: User) -> dict:
    try:
        employee_id = str(user.employee.id)
    except ObjectDoesNotExist:
        employee_id = None
    now = timezone.now()
    return {
        "username": user.username,
        "email": user.email,
        "is_active": user.is_active,
        "employee_id": employee_id,
        "role_ids": [
            str(value)
            for value in user.role_assignments.filter(
                is_active=True, valid_from__lte=now, valid_to__isnull=True
            ).values_list("role_id", flat=True)
        ],
        "department_ids": [
            str(value)
            for value in user.department_scopes.filter(valid_to__isnull=True).values_list(
                "department_id", flat=True
            )
        ],
    }


def _audit(*, actor, action, instance, before, after, reason):
    AuditLog.objects.create(
        actor_user=actor,
        actor_username_snapshot=actor.username,
        action=action,
        module="accounts",
        object_type=instance.__class__.__name__,
        object_id=instance.id,
        object_repr_masked=str(instance)[:255],
        before_json=before,
        after_json=after,
        reason=reason,
        outcome=AuditLog.Outcome.SUCCESS,
    )


@transaction.atomic
def save_user_access(
    *,
    user: User,
    actor: User,
    employee: Employee | None,
    roles,
    departments,
    access_level: str,
    include_descendants: bool,
    password: str = "",
    created: bool,
) -> User:
    before = None if created else _user_snapshot(user)
    user.created_by = user.created_by or actor
    user.updated_by = actor
    if password:
        user.set_password(password)
        user.password_changed_at = timezone.now()
        user.must_change_password = created
    user.save()

    Employee.objects.filter(user=user).exclude(pk=getattr(employee, "pk", None)).update(
        user=None
    )
    if employee and employee.user_id != user.id:
        employee.user = user
        employee.updated_by = actor
        employee.save(update_fields=("user", "updated_by", "updated_at"))

    now = timezone.now()
    selected_role_ids = {role.id for role in roles}
    active_roles = UserRole.objects.select_for_update().filter(
        user=user, is_active=True, valid_to__isnull=True
    )
    for assignment in active_roles.exclude(role_id__in=selected_role_ids):
        assignment.is_active = False
        assignment.valid_to = now
        assignment.save(update_fields=("is_active", "valid_to"))
    existing_role_ids = set(
        active_roles.filter(role_id__in=selected_role_ids).values_list(
            "role_id", flat=True
        )
    )
    for role_id in selected_role_ids - existing_role_ids:
        UserRole.objects.create(user=user, role_id=role_id, created_by=actor)

    selected_department_ids = {department.id for department in departments}
    active_scopes = UserDepartmentScope.objects.select_for_update().filter(
        user=user, valid_to__isnull=True
    )
    active_scopes.update(valid_to=now)
    for department_id in selected_department_ids:
        UserDepartmentScope.objects.create(
            user=user,
            department_id=department_id,
            access_level=access_level,
            include_descendants=include_descendants,
            valid_from=now,
            created_by=actor,
        )

    _audit(
        actor=actor,
        action="user.create" if created else "user.update_access",
        instance=user,
        before=before,
        after=_user_snapshot(user),
        reason="إدارة حساب المستخدم وصلاحياته ونطاقه التنظيمي",
    )
    return user


@transaction.atomic
def save_role_access(*, role: Role, actor: User, permissions, created: bool) -> Role:
    before = None
    if not created:
        before = {
            "code": role.code,
            "name_ar": role.name_ar,
            "is_active": role.is_active,
            "permission_ids": [
                str(value)
                for value in role.permission_assignments.filter(
                    revoked_at__isnull=True
                ).values_list("permission_id", flat=True)
            ],
        }
    role.created_by = role.created_by or actor
    role.updated_by = actor
    role.save()

    now = timezone.now()
    selected_ids = {permission.id for permission in permissions}
    active = RolePermission.objects.select_for_update().filter(
        role=role, revoked_at__isnull=True
    )
    for assignment in active.exclude(permission_id__in=selected_ids):
        assignment.revoked_at = now
        assignment.revoked_by = actor
        assignment.save(update_fields=("revoked_at", "revoked_by"))
    existing_ids = set(
        active.filter(permission_id__in=selected_ids).values_list(
            "permission_id", flat=True
        )
    )
    for permission_id in selected_ids - existing_ids:
        RolePermission.objects.create(
            role=role, permission_id=permission_id, granted_by=actor
        )

    after = {
        "code": role.code,
        "name_ar": role.name_ar,
        "is_active": role.is_active,
        "permission_ids": [str(value) for value in selected_ids],
    }
    _audit(
        actor=actor,
        action="role.create" if created else "role.update_permissions",
        instance=role,
        before=before,
        after=after,
        reason="إدارة الدور والصلاحيات",
    )
    return role
