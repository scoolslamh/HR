from __future__ import annotations

from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditLog

from ..models import (
    Department,
    Employee,
    EmployeeIdentity,
    EmployeePrimaryLocation,
    EmploymentAssignment,
    JobTitle,
    Location,
    UserDepartmentScope,
)
from ..selectors import employees_in_user_department_scope
from .identity import (
    encrypt_sensitive_text,
    mask_mobile,
    national_id_digest,
    redact_potential_national_ids,
)


def _audit(
    *,
    actor,
    action: str,
    instance,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    department=None,
    reason: str,
) -> None:
    AuditLog.objects.create(
        actor_user=actor,
        actor_username_snapshot=redact_potential_national_ids(
            getattr(actor, "username", "") or ""
        )[:150]
        or None,
        action=action,
        module="organization",
        object_type=instance.__class__.__name__,
        object_id=instance.id,
        object_repr_masked=redact_potential_national_ids(str(instance))[:255],
        department_scope=department,
        before_json=before,
        after_json=after,
        reason=reason,
        outcome=AuditLog.Outcome.SUCCESS,
    )


def _active_assignment(employee: Employee, effective_date):
    return (
        EmploymentAssignment.objects.select_for_update()
        .filter(employee=employee, is_primary=True, valid_from__lte=effective_date)
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gt=effective_date))
        .order_by("-valid_from")
        .first()
    )


def _active_location(employee: Employee, effective_date):
    return (
        EmployeePrimaryLocation.objects.select_for_update()
        .filter(employee=employee, valid_from__lte=effective_date)
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gt=effective_date))
        .order_by("-valid_from")
        .first()
    )


def _require_system_admin(actor) -> None:
    if not getattr(actor, "is_authenticated", False) or not actor.is_active or not actor.is_superuser:
        raise PermissionError("هذا الإجراء متاح لمدير النظام فقط.")


@transaction.atomic
def create_manual_employee(
    *,
    actor,
    normalized_national_id: str,
    full_name_ar: str,
    employee_number: str | None,
    normalized_mobile: str | None,
    department: Department,
    location: Location,
    manager: Employee | None,
) -> Employee:
    _require_system_admin(actor)
    identity_hash = national_id_digest(normalized_national_id)
    if EmployeeIdentity.objects.filter(national_id_hash=identity_hash).exists():
        raise ValueError("السجل المدني مرتبط بموظف موجود مسبقًا.")
    if employee_number and Employee.objects.filter(employee_number=employee_number).exists():
        raise ValueError("الرقم الوظيفي مستخدم لموظف آخر.")

    employee = Employee.objects.create(
        full_name_ar=full_name_ar.strip(),
        employee_number=employee_number,
        mobile_masked=mask_mobile(normalized_mobile),
        employment_status=Employee.EmploymentStatus.ACTIVE,
        created_by=actor,
        updated_by=actor,
    )
    encrypted = encrypt_sensitive_text(
        normalized_national_id,
        context=f"employee-national-id:{employee.id}",
    )
    EmployeeIdentity.objects.create(
        employee=employee,
        national_id_hash=identity_hash,
        national_id_encrypted=encrypted.ciphertext,
        encryption_key_version=encrypted.key_version,
        national_id_last4=normalized_national_id[-4:],
        verified_at=timezone.now(),
        verification_source=EmployeeIdentity.VerificationSource.MANUAL,
        created_by=actor,
        updated_by=actor,
    )
    effective_date = timezone.localdate()
    EmploymentAssignment.objects.create(
        employee=employee,
        department=department,
        manager_employee=manager,
        assignment_type=EmploymentAssignment.AssignmentType.PRIMARY,
        valid_from=effective_date,
        is_primary=True,
        reason="إضافة موظف يدويًا",
        created_by=actor,
        updated_by=actor,
    )
    EmployeePrimaryLocation.objects.create(
        employee=employee,
        location=location,
        valid_from=effective_date,
        assignment_reason="إضافة موظف يدويًا",
        created_by=actor,
        updated_by=actor,
    )
    _audit(
        actor=actor,
        action="employee.create_manual",
        instance=employee,
        before=None,
        after={
            "employee_number": employee.employee_number,
            "department_id": str(department.id),
            "location_id": str(location.id),
            "national_id_masked": f"******{normalized_national_id[-4:]}",
        },
        department=department,
        reason="إضافة موظف يدويًا من صفحة الاستيراد",
    )
    return employee


@transaction.atomic
def bulk_set_employee_archive_status(
    *,
    actor,
    action: str,
    employee_ids=None,
    batch=None,
    all_employees: bool = False,
    reason: str,
) -> int:
    _require_system_admin(actor)
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValueError("يجب كتابة سبب واضح للعملية.")
    if action not in {"archive", "restore"}:
        raise ValueError("الإجراء الجماعي غير صالح.")

    queryset = Employee.objects.select_for_update().all()
    scopes = sum(bool(value) for value in (employee_ids, batch, all_employees))
    if scopes != 1:
        raise ValueError("يجب تحديد نطاق واحد للعملية الجماعية.")
    if employee_ids:
        queryset = queryset.filter(id__in=employee_ids)
    elif batch:
        if batch.status != batch.Status.APPROVED:
            raise ValueError("لا يمكن إدارة موظفي دفعة غير معتمدة.")
        hashes = batch.rows.exclude(national_id_hash__isnull=True).values_list(
            "national_id_hash", flat=True
        )
        queryset = queryset.filter(identity__national_id_hash__in=hashes)

    if action == "archive":
        queryset = queryset.exclude(
            employment_status=Employee.EmploymentStatus.ARCHIVED
        ).filter(archived_at__isnull=True)
    else:
        queryset = queryset.filter(
            Q(employment_status=Employee.EmploymentStatus.ARCHIVED)
            | Q(archived_at__isnull=False)
        )

    employees = list(queryset)
    now = timezone.now()
    audit_events = []
    for employee in employees:
        before = {
            "employment_status": employee.employment_status,
            "archived": employee.archived_at is not None,
        }
        if action == "archive":
            employee.employment_status = Employee.EmploymentStatus.ARCHIVED
            employee.archived_at = now
        else:
            employee.employment_status = Employee.EmploymentStatus.ACTIVE
            employee.archived_at = None
        employee.updated_by = actor
        employee.save(
            update_fields=("employment_status", "archived_at", "updated_by", "updated_at")
        )
        audit_events.append(
            AuditLog(
                actor_user=actor,
                actor_username_snapshot=redact_potential_national_ids(actor.username)[:150],
                action=f"employee.bulk_{action}",
                module="organization",
                object_type="Employee",
                object_id=employee.id,
                object_repr_masked=redact_potential_national_ids(employee.full_name_ar)[:255],
                before_json=before,
                after_json={
                    "employment_status": employee.employment_status,
                    "archived": employee.archived_at is not None,
                },
                reason=reason,
                outcome=AuditLog.Outcome.SUCCESS,
            )
        )
    AuditLog.objects.bulk_create(audit_events)
    return len(employees)


def _replace_assignment(
    employee: Employee,
    current: EmploymentAssignment | None,
    *,
    department: Department,
    manager: Employee | None,
    effective_date,
    actor,
) -> EmploymentAssignment:
    if current and current.department_id == department.id:
        if current.manager_employee_id != (manager.id if manager else None):
            current.manager_employee = manager
            current.updated_by = actor
            current.reason = "تحديث بيانات الموظف"
            current.save(
                update_fields=(
                    "manager_employee",
                    "updated_by",
                    "reason",
                    "updated_at",
                )
            )
        return current

    if current and current.valid_from == effective_date:
        current.department = department
        current.manager_employee = manager
        current.updated_by = actor
        current.reason = "تحديث بيانات الموظف"
        current.save(
            update_fields=(
                "department",
                "manager_employee",
                "updated_by",
                "reason",
                "updated_at",
            )
        )
        return current

    if current:
        current.valid_to = effective_date
        current.updated_by = actor
        current.save(update_fields=("valid_to", "updated_by", "updated_at"))

    return EmploymentAssignment.objects.create(
        employee=employee,
        department=department,
        job_title=current.job_title if current else None,
        manager_employee=manager,
        assignment_type=EmploymentAssignment.AssignmentType.PRIMARY,
        valid_from=effective_date,
        is_primary=True,
        reason="تحديث بيانات الموظف",
        created_by=actor,
        updated_by=actor,
    )


def _replace_location(
    employee: Employee,
    current: EmployeePrimaryLocation | None,
    *,
    location: Location,
    effective_date,
    actor,
) -> EmployeePrimaryLocation:
    if current and current.location_id == location.id:
        return current
    if current and current.valid_from == effective_date:
        current.location = location
        current.updated_by = actor
        current.assignment_reason = "تحديث بيانات الموظف"
        current.save(
            update_fields=(
                "location",
                "updated_by",
                "assignment_reason",
                "updated_at",
            )
        )
        return current
    if current:
        current.valid_to = effective_date
        current.updated_by = actor
        current.save(update_fields=("valid_to", "updated_by", "updated_at"))
    return EmployeePrimaryLocation.objects.create(
        employee=employee,
        location=location,
        valid_from=effective_date,
        assignment_reason="تحديث بيانات الموظف",
        created_by=actor,
        updated_by=actor,
    )


@transaction.atomic
def update_employee(
    employee: Employee,
    *,
    actor,
    full_name_ar: str,
    employee_number: str | None,
    normalized_mobile: str | None,
    employment_status: str,
    department: Department,
    location: Location,
    location_effective_date,
    manager: Employee | None,
) -> Employee:
    locked = Employee.objects.select_for_update().get(pk=employee.pk)
    if not actor.is_superuser and not employees_in_user_department_scope(actor).filter(
        pk=locked.pk
    ).exists():
        raise PermissionError("الموظف خارج النطاق التنظيمي للمستخدم.")

    effective_date = timezone.localdate()
    current_assignment = _active_assignment(locked, effective_date)
    current_location = _active_location(locked, effective_date)
    location_at_effective_date = _active_location(locked, location_effective_date)
    before = {
        "full_name_ar": locked.full_name_ar,
        "employee_number": locked.employee_number,
        "mobile_masked": locked.mobile_masked,
        "employment_status": locked.employment_status,
        "department_id": str(current_assignment.department_id)
        if current_assignment
        else None,
        "location_id": str(current_location.location_id) if current_location else None,
        "manager_employee_id": str(current_assignment.manager_employee_id)
        if current_assignment and current_assignment.manager_employee_id
        else None,
    }

    locked.full_name_ar = full_name_ar.strip()
    locked.employee_number = employee_number
    if normalized_mobile:
        locked.mobile_masked = mask_mobile(normalized_mobile)
    locked.employment_status = employment_status
    locked.updated_by = actor
    locked.save(
        update_fields=(
            "full_name_ar",
            "employee_number",
            "mobile_masked",
            "employment_status",
            "updated_by",
            "updated_at",
        )
    )
    assignment = _replace_assignment(
        locked,
        current_assignment,
        department=department,
        manager=manager,
        effective_date=effective_date,
        actor=actor,
    )
    is_backdating_current_location = (
        current_location is not None
        and current_location.location_id == location.id
        and location_effective_date < current_location.valid_from
    )
    if is_backdating_current_location:
        if location_at_effective_date and location_at_effective_date.id != current_location.id:
            location_at_effective_date.valid_to = location_effective_date
            location_at_effective_date.updated_by = actor
            location_at_effective_date.save(
                update_fields=("valid_to", "updated_by", "updated_at")
            )
        current_location.valid_from = location_effective_date
        current_location.updated_by = actor
        current_location.assignment_reason = "تصحيح تاريخ سريان مقر التوقيع"
        current_location.save(
            update_fields=(
                "valid_from",
                "updated_by",
                "assignment_reason",
                "updated_at",
            )
        )
        primary_location = current_location
    else:
        primary_location = _replace_location(
            locked,
            location_at_effective_date,
            location=location,
            effective_date=location_effective_date,
            actor=actor,
        )
    after = {
        "full_name_ar": locked.full_name_ar,
        "employee_number": locked.employee_number,
        "mobile_masked": locked.mobile_masked,
        "employment_status": locked.employment_status,
        "department_id": str(assignment.department_id),
        "location_id": str(primary_location.location_id),
        "location_effective_date": location_effective_date.isoformat(),
        "manager_employee_id": str(assignment.manager_employee_id)
        if assignment.manager_employee_id
        else None,
    }
    _audit(
        actor=actor,
        action="employee.update",
        instance=locked,
        before=before,
        after=after,
        department=department,
        reason="تعديل بيانات الموظف",
    )

    return locked


@transaction.atomic
def save_reference(instance, *, actor, created: bool):
    before = None
    if not created:
        original = instance.__class__.objects.get(pk=instance.pk)
        before = _reference_snapshot(original)
    instance.created_by = instance.created_by or actor
    instance.updated_by = actor
    if isinstance(instance, Department):
        instance.level = instance.parent.level + 1 if instance.parent else 0
    instance.save()
    _audit(
        actor=actor,
        action=f"{instance.__class__.__name__.lower()}.{'create' if created else 'update'}",
        instance=instance,
        before=before,
        after=_reference_snapshot(instance),
        department=instance if isinstance(instance, Department) else getattr(instance, "department", None),
        reason="إنشاء مرجع تنظيمي" if created else "تعديل مرجع تنظيمي",
    )
    return instance


def _reference_snapshot(instance) -> dict[str, Any]:
    data = {
        "code": instance.code,
        "name_ar": instance.name_ar,
        "is_active": instance.is_active,
    }
    if isinstance(instance, Department):
        data.update(
            {
                "unit_type": instance.unit_type,
                "parent_id": str(instance.parent_id) if instance.parent_id else None,
                "signing_location_id": (
                    str(instance.signing_location_id)
                    if instance.signing_location_id
                    else None
                ),
                "department_head_id": (
                    str(instance.department_head_id)
                    if instance.department_head_id
                    else None
                ),
            }
        )
    elif isinstance(instance, Location):
        data.update(
            {
                "location_type": instance.location_type,
                "department_id": str(instance.department_id)
                if instance.department_id
                else None,
            }
        )
    return data


@transaction.atomic
def disable_reference(instance, *, actor):
    locked = instance.__class__.objects.select_for_update().get(pk=instance.pk)
    before = _reference_snapshot(locked)
    locked.is_active = False
    locked.updated_by = actor
    update_fields = ["is_active", "updated_by", "updated_at"]
    if isinstance(locked, Department):
        today = timezone.localdate()
        if locked.valid_to is None and locked.valid_from < today:
            locked.valid_to = today
            update_fields.append("valid_to")
    locked.save(update_fields=update_fields)
    _audit(
        actor=actor,
        action=f"{locked.__class__.__name__.lower()}.disable",
        instance=locked,
        before=before,
        after=_reference_snapshot(locked),
        department=locked if isinstance(locked, Department) else getattr(locked, "department", None),
        reason="تعطيل مرجع تنظيمي دون حذف تاريخي",
    )
    return locked
