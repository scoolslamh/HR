from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.db.models import Max, Min, Q
from django.utils import timezone

from attendance.models import (
    CalculationRun,
    DailyAttendanceResult,
    ImportBatch,
    RawAttendanceRecord,
)
from audit.models import AuditLog
from organization.models import (
    EmploymentAssignment,
)


class AttendanceCalculationError(RuntimeError):
    """Raised when attendance calculation cannot be completed safely."""


@dataclass(frozen=True, slots=True)
class CalculationSummary:
    run: CalculationRun
    created: int


def _minutes(value: timedelta | None) -> int:
    if value is None:
        return 0
    return max(int(value.total_seconds() // 60), 0)


def _normalize_text(value: str | None) -> str:
    value = (value or "").strip().casefold()
    value = value.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    value = value.replace("ة", "ه").replace("ى", "ي")
    value = re.sub(r"[\u064b-\u065f\u0670]", "", value)
    value = re.sub(r"[^\w\u0600-\u06ff]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _locations_match(check_in_location: str | None, check_out_location: str | None) -> bool | None:
    if not check_in_location or not check_out_location:
        return None

    check_in_norm = _normalize_text(check_in_location)
    check_out_norm = _normalize_text(check_out_location)

    if not check_in_norm or not check_out_norm:
        return None

    return check_in_norm == check_out_norm


def _location_status(
    *,
    locations_match: bool | None,
):
    choices = DailyAttendanceResult.LocationStatus
    if locations_match is None:
        return choices.UNKNOWN
    return choices.MATCHED if locations_match else choices.BOTH_OUTSIDE


def _attendance_status(record: RawAttendanceRecord):
    text = _normalize_text(record.source_status)

    if "غياب" in text:
        return DailyAttendanceResult.AttendanceStatus.ABSENT

    if any(token in text for token in ("استثناء", "مهمه", "اجازه", "انتداب", "دوره")):
        return DailyAttendanceResult.AttendanceStatus.EXCUSED

    if record.source_check_in_at and record.source_check_out_at:
        return DailyAttendanceResult.AttendanceStatus.PRESENT

    if record.source_check_in_at or record.source_check_out_at:
        return DailyAttendanceResult.AttendanceStatus.INCOMPLETE

    return DailyAttendanceResult.AttendanceStatus.UNKNOWN


def _department_for(record: RawAttendanceRecord):
    return (
        EmploymentAssignment.objects.filter(
            employee=record.employee,
            is_primary=True,
            valid_from__lte=record.attendance_date,
        )
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gt=record.attendance_date))
        .order_by("-valid_from")
        .values_list("department_id", flat=True)
        .first()
    )


def _worked_minutes(record: RawAttendanceRecord) -> int:
    source_minutes = _minutes(record.source_actual_work_duration)
    if source_minutes:
        return source_minutes

    if record.source_check_in_at and record.source_check_out_at:
        delta = record.source_check_out_at - record.source_check_in_at
        if delta.total_seconds() < 0:
            delta += timedelta(days=1)
        return _minutes(delta)

    return 0


def _source_location_status(record: RawAttendanceRecord) -> Any:
    """
    Read the source location status defensively.

    Different project revisions may expose either `location_match_status`
    or `location_status` on RawAttendanceRecord. If neither exists, return None.
    """
    return getattr(
        record,
        "location_match_status",
        getattr(record, "location_status", None),
    )


def _build_result(
    record: RawAttendanceRecord,
    run: CalculationRun,
    version: int,
    department_id=None,
) -> DailyAttendanceResult:
    scheduled = _minutes(record.source_scheduled_duration)
    worked = _worked_minutes(record)
    early_leave = _minutes(record.source_early_departure_duration)
    shortfall = _minutes(record.source_shortfall_duration)
    early_arrival = _minutes(record.source_early_arrival_duration)

    # V1 derives lateness from source shortfall after subtracting
    # the explicitly reported early-departure duration.
    late = max(shortfall - early_leave, 0)
    overtime = max(worked - scheduled, 0) if scheduled else 0

    locations_match = _locations_match(
        record.source_check_in_location,
        record.source_check_out_location,
    )

    location_status = _location_status(
        locations_match=locations_match,
    )

    return DailyAttendanceResult(
        source_record_id=record.id,
        employee_id=record.employee_id,
        calculation_run=run,
        attendance_date=record.attendance_date,
        version=version,
        is_current=True,
        department_id=department_id,
        primary_location=None,
        first_check_in_at=record.source_check_in_at,
        last_check_out_at=record.source_check_out_at,
        check_in_location=record.source_check_in_location or "",
        check_out_location=record.source_check_out_location or "",
        scheduled_minutes=scheduled,
        worked_minutes=worked,
        late_minutes=late,
        early_leave_minutes=early_leave,
        shortfall_minutes=shortfall,
        early_arrival_minutes=early_arrival,
        overtime_minutes=overtime,
        attendance_status=_attendance_status(record),
        check_in_location_matches=locations_match,
        check_out_location_matches=locations_match,
        location_status=location_status,
        source_status=record.source_status or "",
        calculation_notes={
            "rules_version": "v1",
            "late_rule": "max(source_shortfall - source_early_departure, 0)",
            "source_location_status": _source_location_status(record),
            "location_rule": "compare_check_in_with_check_out",
        },
    )


def _legacy_calculate_records(
    *,
    records,
    requested_by=None,
    import_batch=None,
    reason="",
) -> CalculationSummary:
    records = list(records)

    # Prevent creating more than one current result for the same employee/day.
    # Prefer the most complete raw record, then the newest when tied.
    unique_records: dict[tuple, RawAttendanceRecord] = {}

    def record_score(record: RawAttendanceRecord) -> tuple[int, object]:
        completeness = sum(
            value is not None and value != ""
            for value in (
                record.source_check_in_at,
                record.source_check_out_at,
                record.source_check_in_location,
                record.source_check_out_location,
                record.source_actual_work_duration,
            )
        )
        return completeness, record.created_at

    for record in records:
        key = (record.employee_id, record.attendance_date)
        current = unique_records.get(key)

        if current is None or record_score(record) > record_score(current):
            unique_records[key] = record

    records = list(unique_records.values())

    if not records:
        raise AttendanceCalculationError("لا توجد سجلات حضور خام للاحتساب.")

    period_start = min(record.attendance_date for record in records)
    period_end = max(record.attendance_date for record in records)

    with transaction.atomic():
        has_current_results = DailyAttendanceResult.objects.filter(
            employee_id__in={record.employee_id for record in records},
            attendance_date__range=(period_start, period_end),
            is_current=True,
        ).exists()

        run = CalculationRun.objects.create(
            run_type=(
                CalculationRun.RunType.RECALCULATION
                if has_current_results
                else CalculationRun.RunType.INITIAL
            ),
            import_batch=import_batch,
            period_start=period_start,
            period_end=period_end,
            status=CalculationRun.Status.RUNNING,
            requested_by=requested_by,
            reason=reason,
            started_at=timezone.now(),
        )

        results: list[DailyAttendanceResult] = []
        now = timezone.now()

        for record in records:
            current = (
                DailyAttendanceResult.objects.select_for_update()
                .filter(
                    employee=record.employee,
                    attendance_date=record.attendance_date,
                    is_current=True,
                )
                .order_by("-version")
                .first()
            )

            version = 1

            if current:
                version = current.version + 1
                current.is_current = False
                current.superseded_at = now
                current.save(update_fields=("is_current", "superseded_at"))

            results.append(
                _build_result(
                    record=record,
                    run=run,
                    version=version,
                )
            )

        DailyAttendanceResult.objects.bulk_create(results)

        # Requests are derived from immutable calculated results and live in a
        # separate workflow; importing/recalculating never edits source data.
        from violations.services import create_automatic_clarifications

        create_automatic_clarifications(results)

        run.status = CalculationRun.Status.COMPLETED
        run.result_count = len(results)
        run.finished_at = timezone.now()
        run.save(
            update_fields=(
                "status",
                "result_count",
                "finished_at",
            )
        )

        AuditLog.objects.create(
            actor_user=(
                requested_by
                if getattr(requested_by, "is_authenticated", False)
                else None
            ),
            actor_username_snapshot=getattr(
                requested_by,
                "username",
                None,
            ),
            action="attendance.calculate",
            module="attendance",
            object_type="calculation_run",
            object_id=run.id,
            object_repr_masked=(f"احتساب {period_start} إلى {period_end}"),
            after_json={
                "result_count": len(results),
                "rules_version": "v1",
            },
            outcome=AuditLog.Outcome.SUCCESS,
        )

    return CalculationSummary(run=run, created=len(results))


_CALCULATION_BATCH_SIZE = 500
_RECORD_FIELDS = (
    "id", "employee_id", "attendance_date", "created_at", "source_check_in_at",
    "source_check_out_at", "source_check_in_location", "source_check_out_location",
    "source_status", "source_scheduled_duration", "source_actual_work_duration",
    "source_early_departure_duration", "source_shortfall_duration",
    "source_early_arrival_duration", "location_match_status",
)


def _record_score(record: RawAttendanceRecord) -> tuple[int, object]:
    completeness = sum(
        value is not None and value != ""
        for value in (
            record.source_check_in_at,
            record.source_check_out_at,
            record.source_check_in_location,
            record.source_check_out_location,
            record.source_actual_work_duration,
        )
    )
    return completeness, record.created_at


def _ordered_unique_records(records):
    if hasattr(records, "order_by"):
        source = records.only(*_RECORD_FIELDS).order_by(
            "employee_id", "attendance_date", "created_at", "id"
        ).iterator(chunk_size=_CALCULATION_BATCH_SIZE)
    else:
        source = iter(sorted(
            records,
            key=lambda record: (
                record.employee_id, record.attendance_date,
                record.created_at, record.id,
            ),
        ))

    current_key = None
    selected = None
    for record in source:
        key = (record.employee_id, record.attendance_date)
        if current_key is not None and key != current_key:
            yield selected
            selected = None
        if selected is None or _record_score(record) > _record_score(selected):
            selected = record
        current_key = key
    if selected is not None:
        yield selected


def _department_map(records) -> dict[tuple, object]:
    employee_ids = {record.employee_id for record in records}
    start = min(record.attendance_date for record in records)
    end = max(record.attendance_date for record in records)
    assignments = list(
        EmploymentAssignment.objects.filter(
            employee_id__in=employee_ids,
            is_primary=True,
            valid_from__lte=end,
        )
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gt=start))
        .only("employee_id", "department_id", "valid_from", "valid_to")
        .order_by("employee_id", "-valid_from")
    )
    by_employee = {}
    for assignment in assignments:
        by_employee.setdefault(assignment.employee_id, []).append(assignment)
    return {
        (record.employee_id, record.attendance_date): next(
            (
                assignment.department_id
                for assignment in by_employee.get(record.employee_id, ())
                if assignment.valid_from <= record.attendance_date
                and (assignment.valid_to is None or assignment.valid_to > record.attendance_date)
            ),
            None,
        )
        for record in records
    }


def _calculate_record_batch(records, *, run, now) -> int:
    keys = {(record.employee_id, record.attendance_date) for record in records}
    employee_ids = {key[0] for key in keys}
    dates = [key[1] for key in keys]
    current_results = list(
        DailyAttendanceResult.objects.select_for_update()
        .filter(
            employee_id__in=employee_ids,
            attendance_date__range=(min(dates), max(dates)),
            is_current=True,
        )
        .only("id", "employee_id", "attendance_date", "version", "is_current", "superseded_at")
    )
    current_by_key = {
        (result.employee_id, result.attendance_date): result
        for result in current_results
        if (result.employee_id, result.attendance_date) in keys
    }
    departments = _department_map(records)
    results = []
    superseded = []
    for record in records:
        key = (record.employee_id, record.attendance_date)
        current = current_by_key.get(key)
        version = 1
        if current:
            version = current.version + 1
            current.is_current = False
            current.superseded_at = now
            superseded.append(current)
        results.append(
            _build_result(
                record=record,
                run=run,
                version=version,
                department_id=departments[key],
            )
        )
    if superseded:
        DailyAttendanceResult.objects.bulk_update(
            superseded, ("is_current", "superseded_at"),
            batch_size=_CALCULATION_BATCH_SIZE,
        )
    DailyAttendanceResult.objects.bulk_create(
        results, batch_size=_CALCULATION_BATCH_SIZE
    )
    from violations.services import create_automatic_clarifications
    create_automatic_clarifications(results)
    return len(results)


def calculate_records(
    *, records, requested_by=None, import_batch=None, reason=""
) -> CalculationSummary:
    is_queryset = hasattr(records, "aggregate")
    if is_queryset:
        bounds = records.aggregate(start=Min("attendance_date"), end=Max("attendance_date"))
        period_start, period_end = bounds["start"], bounds["end"]
        employee_scope = records.values("employee_id")
    else:
        records = list(records)
        if not records:
            raise AttendanceCalculationError("لا توجد سجلات حضور خام للاحتساب.")
        period_start = min(record.attendance_date for record in records)
        period_end = max(record.attendance_date for record in records)
        employee_scope = {record.employee_id for record in records}
    if period_start is None or period_end is None:
        raise AttendanceCalculationError("لا توجد سجلات حضور خام للاحتساب.")

    with transaction.atomic():
        has_current_results = DailyAttendanceResult.objects.filter(
            employee_id__in=employee_scope,
            attendance_date__range=(period_start, period_end),
            is_current=True,
        ).exists()
        run = CalculationRun.objects.create(
            run_type=(CalculationRun.RunType.RECALCULATION if has_current_results else CalculationRun.RunType.INITIAL),
            import_batch=import_batch,
            period_start=period_start,
            period_end=period_end,
            status=CalculationRun.Status.RUNNING,
            requested_by=requested_by,
            reason=reason,
            started_at=timezone.now(),
        )
        created_count = 0
        pending = []
        now = timezone.now()
        for record in _ordered_unique_records(records):
            pending.append(record)
            if len(pending) >= _CALCULATION_BATCH_SIZE:
                created_count += _calculate_record_batch(pending, run=run, now=now)
                pending.clear()
        if pending:
            created_count += _calculate_record_batch(pending, run=run, now=now)
            pending.clear()

        run.status = CalculationRun.Status.COMPLETED
        run.result_count = created_count
        run.finished_at = timezone.now()
        run.save(update_fields=("status", "result_count", "finished_at"))
        AuditLog.objects.create(
            actor_user=requested_by if getattr(requested_by, "is_authenticated", False) else None,
            actor_username_snapshot=getattr(requested_by, "username", None),
            action="attendance.calculate",
            module="attendance",
            object_type="calculation_run",
            object_id=run.id,
            object_repr_masked=f"احتساب {period_start} إلى {period_end}",
            after_json={"result_count": created_count, "rules_version": "v1"},
            outcome=AuditLog.Outcome.SUCCESS,
        )
    return CalculationSummary(run=run, created=created_count)


def calculate_batch(
    batch: ImportBatch,
    *,
    requested_by=None,
    reason="اعتماد دفعة الحضور",
):
    if batch.archived_at:
        raise AttendanceCalculationError("لا يمكن احتساب ملف حضور مؤرشف.")
    records = RawAttendanceRecord.objects.filter(import_row__batch=batch)

    return calculate_records(
        records=records,
        requested_by=requested_by,
        import_batch=batch,
        reason=reason,
    )


def calculate_all(*, requested_by=None):
    records = RawAttendanceRecord.objects.filter(
        import_row__batch__archived_at__isnull=True
    )

    return calculate_records(
        records=records,
        requested_by=requested_by,
        reason="احتساب جميع سجلات الحضور الخام",
    )
