from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from attendance.models import DailyAttendanceResult
from audit.models import AuditLog

from .models import ClarificationEvidence, ClarificationRequest


def clarification_kinds_for_result(result: DailyAttendanceResult) -> set[str]:
    kinds = set()
    if result.attendance_status == DailyAttendanceResult.AttendanceStatus.ABSENT:
        kinds.add(ClarificationRequest.Kind.ABSENCE)
    if "انصراف تلقائي" in (result.source_status or ""):
        kinds.add(ClarificationRequest.Kind.AUTOMATIC_CHECKOUT)
    return kinds


def _legacy_create_automatic_clarifications(results) -> int:
    created_count = 0
    for result in results:
        kinds = clarification_kinds_for_result(result)
        ClarificationRequest.objects.filter(
            employee=result.employee,
            attendance_date=result.attendance_date,
        ).exclude(kind__in=kinds).exclude(
            status=ClarificationRequest.Status.CANCELLED
        ).update(
            status=ClarificationRequest.Status.CANCELLED,
        )
        for kind in kinds:
            clarification = ClarificationRequest.objects.filter(
                employee=result.employee,
                attendance_date=result.attendance_date,
                kind=kind,
            ).first()
            created = clarification is None
            if created:
                clarification = ClarificationRequest.objects.create(
                    employee=result.employee,
                    attendance_date=result.attendance_date,
                    kind=kind,
                    attendance_result=result,
                    department=result.department,
                )
            if not created:
                clarification.attendance_result = result
                clarification.department = result.department
                update_fields = ["attendance_result", "department", "updated_at"]
                if clarification.status == ClarificationRequest.Status.CANCELLED:
                    clarification.status = ClarificationRequest.Status.AWAITING_EMPLOYEE
                    update_fields.append("status")
                clarification.save(update_fields=update_fields)
            created_count += int(created)
            if created:
                AuditLog.objects.create(
                    action="clarification.auto_create",
                    module="violations",
                    object_type="ClarificationRequest",
                    object_id=clarification.id,
                    object_repr_masked=f"إفادة {clarification.get_kind_display()}",
                    department_scope=result.department,
                    after_json={"kind": kind, "status": clarification.status},
                    outcome=AuditLog.Outcome.SUCCESS,
                )
    return created_count


def create_automatic_clarifications(results) -> int:
    """Synchronize one bounded calculation batch without per-row savepoints."""
    results = tuple(results)
    if not results:
        return 0

    result_by_key = {
        (result.employee_id, result.attendance_date): result for result in results
    }
    desired = {
        key: clarification_kinds_for_result(result)
        for key, result in result_by_key.items()
    }
    employee_ids = {key[0] for key in result_by_key}
    dates = [key[1] for key in result_by_key]
    existing = list(
        ClarificationRequest.objects.filter(
            employee_id__in=employee_ids,
            attendance_date__range=(min(dates), max(dates)),
        ).only(
            "id", "employee_id", "attendance_date", "kind", "status",
            "attendance_result_id", "department_id",
        )
    )
    existing_by_key = {
        (item.employee_id, item.attendance_date, item.kind): item
        for item in existing
        if (item.employee_id, item.attendance_date) in result_by_key
    }
    to_create = []
    to_update = []
    now = timezone.now()

    for key, result in result_by_key.items():
        kinds = desired[key]
        for kind in ClarificationRequest.Kind.values:
            clarification = existing_by_key.get((*key, kind))
            if kind not in kinds:
                if clarification and clarification.status != ClarificationRequest.Status.CANCELLED:
                    clarification.status = ClarificationRequest.Status.CANCELLED
                    clarification.updated_at = now
                    to_update.append(clarification)
                continue
            if clarification is None:
                to_create.append(
                    ClarificationRequest(
                        employee_id=result.employee_id,
                        attendance_date=result.attendance_date,
                        kind=kind,
                        attendance_result_id=result.id,
                        department_id=result.department_id,
                    )
                )
                continue
            clarification.attendance_result_id = result.id
            clarification.department_id = result.department_id
            clarification.updated_at = now
            if clarification.status == ClarificationRequest.Status.CANCELLED:
                clarification.status = ClarificationRequest.Status.AWAITING_EMPLOYEE
            to_update.append(clarification)

    if to_update:
        ClarificationRequest.objects.bulk_update(
            to_update,
            ("attendance_result", "department", "status", "updated_at"),
            batch_size=500,
        )
    if to_create:
        ClarificationRequest.objects.bulk_create(to_create, batch_size=500)
        AuditLog.objects.bulk_create(
            [
                AuditLog(
                    action="clarification.auto_create",
                    module="violations",
                    object_type="ClarificationRequest",
                    object_id=item.id,
                    object_repr_masked=f"إفادة {item.get_kind_display()}",
                    department_scope_id=item.department_id,
                    after_json={"kind": item.kind, "status": item.status},
                    outcome=AuditLog.Outcome.SUCCESS,
                )
                for item in to_create
            ],
            batch_size=500,
        )
    return len(to_create)


@transaction.atomic
def submit_employee_clarification(*, clarification, employee, explanation, evidence, actor):
    locked = ClarificationRequest.objects.select_for_update().get(pk=clarification.pk)
    if locked.employee_id != employee.id:
        raise PermissionError("لا يمكنك تعديل إفادة موظف آخر.")
    if locked.status not in {
        ClarificationRequest.Status.AWAITING_EMPLOYEE,
        ClarificationRequest.Status.RETURNED,
    }:
        raise ValueError("لا يمكن تعديل الإفادة في حالتها الحالية.")
    locked.employee_explanation = explanation
    locked.status = ClarificationRequest.Status.AWAITING_MANAGER
    locked.submitted_at = timezone.now()
    locked.reviewed_by = None
    locked.reviewed_at = None
    locked.review_comment = ""
    locked.save()
    if evidence:
        ClarificationEvidence.objects.create(
            clarification=locked,
            file=evidence,
            original_filename=evidence.name[:255],
            content_type=(getattr(evidence, "content_type", "") or "")[:100],
            file_size=evidence.size,
            uploaded_by=actor,
        )
    AuditLog.objects.create(
        actor_user=actor,
        actor_username_snapshot=actor.username,
        action="clarification.submit",
        module="violations",
        object_type="ClarificationRequest",
        object_id=locked.id,
        object_repr_masked=f"إفادة {locked.get_kind_display()}",
        department_scope=locked.department,
        after_json={"status": locked.status, "has_evidence": bool(evidence)},
        outcome=AuditLog.Outcome.SUCCESS,
    )
    return locked


@transaction.atomic
def review_clarification(*, clarification, actor, decision, comment):
    locked = ClarificationRequest.objects.select_for_update().select_related(
        "employee", "department__department_head"
    ).get(pk=clarification.pk)
    if locked.status != ClarificationRequest.Status.AWAITING_MANAGER:
        raise ValueError("الإفادة ليست بانتظار الاعتماد.")
    if locked.employee.user_id == actor.id:
        raise PermissionError("لا يجوز اعتماد إفادة أنشأها المستخدم لنفسه.")
    if not locked.department or locked.department.department_head_id is None:
        raise PermissionError("لم يحدد رئيس القسم المختص.")
    if locked.department.department_head.user_id != actor.id and not actor.is_superuser:
        raise PermissionError("الإفادة خارج نطاق اعتمادك.")
    statuses = {
        "approve": ClarificationRequest.Status.APPROVED,
        "reject": ClarificationRequest.Status.REJECTED,
        "return": ClarificationRequest.Status.RETURNED,
    }
    if decision not in statuses:
        raise ValueError("قرار الاعتماد غير صالح.")
    if decision in {"reject", "return"} and not comment.strip():
        raise ValueError("التعليق مطلوب عند الرفض أو الإعادة.")
    locked.status = statuses[decision]
    locked.reviewed_by = actor
    locked.review_comment = comment.strip()
    locked.reviewed_at = timezone.now()
    locked.save()
    AuditLog.objects.create(
        actor_user=actor,
        actor_username_snapshot=actor.username,
        action=f"clarification.{decision}",
        module="violations",
        object_type="ClarificationRequest",
        object_id=locked.id,
        object_repr_masked=f"إفادة {locked.get_kind_display()}",
        department_scope=locked.department,
        after_json={"status": locked.status},
        reason=locked.review_comment,
        outcome=AuditLog.Outcome.SUCCESS,
    )
    return locked
