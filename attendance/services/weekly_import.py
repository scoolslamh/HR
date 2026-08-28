from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import BinaryIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditLog
from organization.models import EmployeeIdentity
from organization.models import Department, Employee, EmploymentAssignment
from organization.services.exceptions import SecurityConfigurationError
from organization.services.identity import (
    encrypt_sensitive_bytes,
    encrypt_sensitive_text,
    ensure_crypto_configured,
    mask_national_id,
    mask_untrusted_identifier,
    national_id_digest,
    redact_potential_national_ids,
    decrypt_sensitive_bytes,
    normalize_national_id,
)

from attendance.models import ImportBatch, ImportError, ImportRow, RawAttendanceRecord
from attendance.services.weekly_report_parser import ParsedDailyRow, ParserIssue, parse_weekly_report


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_XLSX_SIGNATURE = b"PK\x03\x04"
_STORAGE_PREFIX = "attendance/imports"
_LOOKUP_CHUNK_SIZE = 1000
_WRITE_BATCH_SIZE = 500


class AttendanceImportServiceError(RuntimeError):
    def __init__(self, message_ar: str, *, code: str = "attendance_import_error"):
        self.message_ar = message_ar
        self.code = code
        super().__init__(message_ar)


@dataclass(frozen=True, slots=True)
class ImportPreviewResult:
    batch: ImportBatch


@dataclass(frozen=True, slots=True)
class _PreparedPreviewRow:
    parsed_row: ParsedDailyRow
    national_id_hash: str | None
    employee_id: uuid.UUID | None
    fingerprint: str | None


def _chunks(values, *, size: int):
    chunk = []
    for value in values:
        chunk.append(value)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _identity_employee_map(national_id_hashes: set[str]) -> dict[str, uuid.UUID]:
    identities: dict[str, uuid.UUID] = {}
    for hash_chunk in _chunks(national_id_hashes, size=_LOOKUP_CHUNK_SIZE):
        for identity in EmployeeIdentity.objects.filter(
            national_id_hash__in=hash_chunk
        ).values("national_id_hash", "employee_id"):
            identities[identity["national_id_hash"]] = identity["employee_id"]
    return identities


def _existing_fingerprints(queryset, field_name: str, fingerprints: set[str]) -> set[str]:
    existing: set[str] = set()
    for fingerprint_chunk in _chunks(fingerprints, size=_LOOKUP_CHUNK_SIZE):
        existing.update(
            queryset.filter(
                **{f"{field_name}__in": fingerprint_chunk}
            ).values_list(field_name, flat=True)
        )
    return existing


def _max_upload_bytes() -> int:
    value = getattr(settings, "ATTENDANCE_IMPORT_MAX_BYTES", 10 * 1024 * 1024)
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 10 * 1024 * 1024
    return max(value, 1)


def _read_uploaded_file(uploaded_file) -> bytes:
    try:
        uploaded_file.seek(0)
    except (AttributeError, OSError):
        pass
    content = uploaded_file.read()
    if not isinstance(content, bytes):
        content = bytes(content)
    return content


def _safe_filename(name: str) -> str:
    cleaned = Path(name or "attendance.xlsx").name
    cleaned = redact_potential_national_ids(cleaned)
    cleaned = re.sub(r"[^\w.()\-\u0600-\u06FF ]+", "_", cleaned, flags=re.UNICODE)
    return cleaned[:255] or "attendance.xlsx"


def _validate_workbook(name: str, content: bytes) -> None:
    if Path(name).suffix.lower() != ".xlsx":
        raise AttendanceImportServiceError("يُسمح برفع ملفات Excel بصيغة xlsx فقط.", code="invalid_extension")
    if not content:
        raise AttendanceImportServiceError("الملف المرفوع فارغ.", code="empty_file")
    if len(content) > _max_upload_bytes():
        raise AttendanceImportServiceError("حجم الملف يتجاوز الحد المسموح.", code="file_too_large")
    if not content.startswith(_XLSX_SIGNATURE):
        raise AttendanceImportServiceError("الملف لا يبدو ملف Excel صالحًا بصيغة xlsx.", code="invalid_file_type")


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")


def _time_text(value: time | None) -> str:
    return value.isoformat() if value else ""


def _date_text(value: date | None) -> str:
    return value.isoformat() if value else ""


def _duration_seconds(value) -> int | None:
    return int(value.total_seconds()) if value is not None else None


def _record_fingerprint(*, employee_id, row: ParsedDailyRow) -> str | None:
    if not employee_id or not row.attendance_date:
        return None
    pieces = [
        str(employee_id),
        row.attendance_date.isoformat(),
        _time_text(row.check_in),
        _time_text(row.check_out),
        (row.check_in_location or "").strip(),
        (row.check_out_location or "").strip(),
    ]
    return hashlib.sha256("|".join(pieces).encode("utf-8")).hexdigest()


def _stored_row_fingerprint(*, employee_id, row: ImportRow) -> str | None:
    if not employee_id or not row.attendance_date:
        return None
    pieces = [
        str(employee_id),
        row.attendance_date.isoformat(),
        _time_text(row.source_check_in),
        _time_text(row.source_check_out),
        (row.source_check_in_location or "").strip(),
        (row.source_check_out_location or "").strip(),
    ]
    return hashlib.sha256("|".join(pieces).encode("utf-8")).hexdigest()


def _normalize_location(value: str | None) -> str:
    value = (value or "").strip().casefold()
    value = value.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    value = value.replace("ة", "ه").replace("ى", "ي")
    value = re.sub(r"[\u064b-\u065f\u0670]", "", value)
    value = re.sub(r"[^\w\u0600-\u06ff]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _location_match_status(*, check_in_location: str | None, check_out_location: str | None) -> str:
    check_in = _normalize_location(check_in_location)
    check_out = _normalize_location(check_out_location)
    if not check_in or not check_out:
        return ImportRow.LocationMatchStatus.UNKNOWN
    if check_in == check_out:
        return ImportRow.LocationMatchStatus.MATCHED
    return ImportRow.LocationMatchStatus.MISMATCH


def _display_data(row: ParsedDailyRow) -> dict:
    return {
        "employee_name": row.employee_name,
        "job_title": row.job_title,
        "national_id_masked": mask_national_id(row.national_id) if row.national_id else "غير متاح",
        "attendance_date": _date_text(row.attendance_date),
        "source_status": row.source_status or "",
        "check_in": _time_text(row.check_in),
        "check_out": _time_text(row.check_out),
        "check_in_location": row.check_in_location or "",
        "check_out_location": row.check_out_location or "",
    }


def _normalized_payload(row: ParsedDailyRow) -> dict:
    return {
        "employee_name": row.employee_name,
        "job_title": row.job_title,
        "attendance_date": _date_text(row.attendance_date),
        "source_status": row.source_status,
        "scheduled_seconds": _duration_seconds(row.scheduled_duration),
        "check_in": _time_text(row.check_in),
        "check_in_location": row.check_in_location,
        "check_out": _time_text(row.check_out),
        "check_out_location": row.check_out_location,
        "actual_work_seconds": _duration_seconds(row.actual_work_duration),
        "early_departure_seconds": _duration_seconds(row.early_departure_duration),
        "shortfall_seconds": _duration_seconds(row.shortfall_duration),
        "early_arrival_seconds": _duration_seconds(row.early_arrival_duration),
    }


def _error_from_issue(batch, import_row, issue: ParserIssue) -> ImportError:
    return ImportError(
        batch=batch,
        row=import_row,
        error_code=issue.code,
        severity=(ImportError.Severity.ERROR if issue.severity == "error" else ImportError.Severity.WARNING),
        field_name=issue.field_name,
        message_ar=issue.message_ar,
        masked_value=issue.masked_value,
    )


def _audit(*, actor, action: str, batch: ImportBatch, outcome: str, reason: str = "", after_json=None):
    username = redact_potential_national_ids(getattr(actor, "username", "") or "")
    AuditLog.objects.create(
        actor_user=actor if getattr(actor, "is_authenticated", False) else None,
        actor_username_snapshot=username or None,
        action=action,
        module="attendance",
        object_type="attendance_import_batch",
        object_id=batch.id,
        object_repr_masked=f"دفعة حضور {str(batch.id)[:8]}",
        reason=reason or None,
        after_json=after_json,
        outcome=outcome,
    )


def preview_attendance_import(uploaded_file, *, uploaded_by) -> ImportBatch:
    ensure_crypto_configured()
    content = _read_uploaded_file(uploaded_file)
    original_name = _safe_filename(getattr(uploaded_file, "name", "attendance.xlsx"))
    _validate_workbook(original_name, content)
    file_sha256 = hashlib.sha256(content).hexdigest()
    if ImportBatch.objects.filter(file_sha256=file_sha256).exists():
        raise AttendanceImportServiceError("تم رفع هذا الملف سابقًا.", code="duplicate_file")

    try:
        parsed = parse_weekly_report(content)
    except ValueError as exc:
        raise AttendanceImportServiceError(str(exc), code="invalid_report_layout") from exc
    except Exception as exc:
        raise AttendanceImportServiceError(
            "تعذر قراءة ملف Excel. تأكد من سلامة الملف ومن مطابقته لقالب تقرير البصمة.",
            code="parse_failed",
        ) from exc

    encrypted_workbook = encrypt_sensitive_bytes(content, context=f"attendance-workbook:{file_sha256}")
    storage_key = f"{_STORAGE_PREFIX}/{uuid.uuid4().hex}.bin"

    national_id_hashes = {
        national_id: national_id_digest(national_id)
        for national_id in {
            row.national_id for row in parsed.rows if row.national_id is not None
        }
    }
    identities = _identity_employee_map(set(national_id_hashes.values()))
    prepared_rows: list[_PreparedPreviewRow] = []
    fingerprints: set[str] = set()
    for parsed_row in parsed.rows:
        national_hash = (
            national_id_hashes.get(parsed_row.national_id)
            if parsed_row.national_id is not None
            else None
        )
        employee_id = identities.get(national_hash) if national_hash else None
        fingerprint = _record_fingerprint(employee_id=employee_id, row=parsed_row)
        prepared_rows.append(
            _PreparedPreviewRow(
                parsed_row=parsed_row,
                national_id_hash=national_hash,
                employee_id=employee_id,
                fingerprint=fingerprint,
            )
        )
        if fingerprint:
            fingerprints.add(fingerprint)

    existing_raw_fingerprints = _existing_fingerprints(
        RawAttendanceRecord.objects.all(), "record_fingerprint", fingerprints
    )
    existing_import_fingerprints = _existing_fingerprints(
        ImportRow.objects.all(), "proposed_record_fingerprint", fingerprints
    )

    with transaction.atomic():
        saved_storage_key = default_storage.save(storage_key, ContentFile(encrypted_workbook.ciphertext))
        try:
            batch = ImportBatch.objects.create(
                original_filename=original_name,
                storage_key=saved_storage_key,
                file_sha256=file_sha256,
                file_size_bytes=len(content),
                mime_type=XLSX_MIME,
                source_period_title=parsed.period_title,
                period_start=parsed.period_start,
                period_end=parsed.period_end,
                source_row_count=parsed.source_row_count,
                employee_count=parsed.employee_count,
                daily_record_count=len(parsed.rows),
                ignored_row_count=parsed.ignored_row_count,
                summary_row_count=parsed.summary_row_count,
                uploaded_by=uploaded_by,
            )

            pending_rows: list[ImportRow] = []
            pending_errors: list[ImportError] = []
            matched_count = 0
            unmatched_count = 0
            error_count = 0
            warning_count = 0
            locations: set[str] = set()
            seen_fingerprints: set[str] = set()

            def flush_pending() -> None:
                if pending_rows:
                    ImportRow.objects.bulk_create(
                        pending_rows, batch_size=_WRITE_BATCH_SIZE
                    )
                    pending_rows.clear()
                if pending_errors:
                    ImportError.objects.bulk_create(
                        pending_errors, batch_size=_WRITE_BATCH_SIZE
                    )
                    pending_errors.clear()

            for issue in parsed.issues:
                pending_errors.append(_error_from_issue(batch, None, issue))
                error_count += issue.severity == "error"
                warning_count += issue.severity != "error"

            for prepared_row in prepared_rows:
                parsed_row = prepared_row.parsed_row
                national_hash = prepared_row.national_id_hash
                employee_id = prepared_row.employee_id
                fingerprint = prepared_row.fingerprint

                match_status = ImportRow.MatchStatus.MATCHED if employee_id else ImportRow.MatchStatus.UNMATCHED
                row_issues = list(parsed_row.issues)
                if not employee_id:
                    row_issues.append(
                        ParserIssue(
                            parsed_row.row_number,
                            "employee_not_found",
                            "error",
                            "national_id",
                            "لا يوجد موظف مطابق للسجل المدني في البيانات الأساسية.",
                            mask_national_id(parsed_row.national_id) if parsed_row.national_id else "غير متاح",
                        )
                    )

                loc_status = _location_match_status(
                    check_in_location=parsed_row.check_in_location,
                    check_out_location=parsed_row.check_out_location,
                )
                is_duplicate = bool(
                    fingerprint
                    and (
                        fingerprint in existing_raw_fingerprints
                        or fingerprint in existing_import_fingerprints
                        or fingerprint in seen_fingerprints
                    )
                )
                if fingerprint:
                    seen_fingerprints.add(fingerprint)
                if is_duplicate:
                    row_issues.append(
                        ParserIssue(
                            parsed_row.row_number,
                            "duplicate_daily_record",
                            "error",
                            "row",
                            "سجل الحضور اليومي مكرر في دفعة سابقة أو في هذه الدفعة.",
                        )
                    )

                has_errors = any(i.severity == "error" for i in row_issues)
                has_warnings = any(i.severity != "error" for i in row_issues)
                validation_status = (
                    ImportRow.ValidationStatus.ERROR
                    if has_errors
                    else ImportRow.ValidationStatus.WARNING
                    if has_warnings or loc_status == ImportRow.LocationMatchStatus.MISMATCH
                    else ImportRow.ValidationStatus.VALID
                )
                if loc_status == ImportRow.LocationMatchStatus.MISMATCH:
                    row_issues.append(
                        ParserIssue(
                            parsed_row.row_number,
                            "location_mismatch",
                            "warning",
                            "check_in_location",
                            "مكان الحضور يختلف عن مكان الانصراف؛ سُجل كتنبيه فقط ولا يتطلب إفادة.",
                        )
                    )
                    validation_status = ImportRow.ValidationStatus.WARNING if not has_errors else validation_status
                elif loc_status == ImportRow.LocationMatchStatus.UNKNOWN:
                    row_issues.append(
                        ParserIssue(
                            parsed_row.row_number,
                            "location_incomplete",
                            "warning",
                            "location",
                            "تعذر مقارنة الموقعين لأن مكان الحضور أو الانصراف غير متاح؛ سُجل كتنبيه فقط.",
                        )
                    )
                    validation_status = ImportRow.ValidationStatus.WARNING if not has_errors else validation_status

                raw_payload_bytes = _json_bytes(parsed_row.raw_payload)
                encrypted_payload = encrypt_sensitive_bytes(
                    raw_payload_bytes,
                    context=f"attendance-row:{batch.id}:{parsed_row.row_number}",
                )
                import_row = ImportRow(
                    batch=batch,
                    row_number=parsed_row.row_number,
                    raw_payload_encrypted=encrypted_payload.ciphertext,
                    encryption_key_version=encrypted_payload.key_version,
                    raw_payload_sha256=hashlib.sha256(raw_payload_bytes).hexdigest(),
                    national_id_hash=national_hash,
                    national_id_last4=(parsed_row.national_id[-4:] if parsed_row.national_id else ""),
                    normalized_payload_json=_normalized_payload(parsed_row),
                    display_data_json=_display_data(parsed_row),
                    matched_employee_id=employee_id,
                    attendance_date=parsed_row.attendance_date,
                    source_check_in=parsed_row.check_in,
                    source_check_out=parsed_row.check_out,
                    source_check_in_location=parsed_row.check_in_location,
                    source_check_out_location=parsed_row.check_out_location,
                    source_status=parsed_row.source_status,
                    source_scheduled_duration=parsed_row.scheduled_duration,
                    source_actual_work_duration=parsed_row.actual_work_duration,
                    source_early_departure_duration=parsed_row.early_departure_duration,
                    source_shortfall_duration=parsed_row.shortfall_duration,
                    source_early_arrival_duration=parsed_row.early_arrival_duration,
                    proposed_record_fingerprint=fingerprint,
                    match_status=(ImportRow.MatchStatus.INVALID if parsed_row.national_id is None else match_status),
                    validation_status=validation_status,
                    location_match_status=loc_status,
                    is_duplicate=is_duplicate,
                )
                pending_rows.append(import_row)
                for issue in row_issues:
                    pending_errors.append(_error_from_issue(batch, import_row, issue))
                    error_count += issue.severity == "error"
                    warning_count += issue.severity != "error"

                matched_count += bool(employee_id)
                unmatched_count += not bool(employee_id)
                for location in (parsed_row.check_in_location, parsed_row.check_out_location):
                    if location:
                        locations.add(_normalize_location(location))
                if len(pending_rows) >= _WRITE_BATCH_SIZE:
                    flush_pending()

            flush_pending()

            batch.matched_row_count = matched_count
            batch.unmatched_row_count = unmatched_count
            batch.error_count = int(error_count)
            batch.warning_count = int(warning_count)
            batch.distinct_location_count = len(locations)
            batch.status = (
                ImportBatch.Status.HAS_ERRORS if error_count else ImportBatch.Status.PREVIEW_READY
            )
            batch.save(
                update_fields=(
                    "matched_row_count",
                    "unmatched_row_count",
                    "error_count",
                    "warning_count",
                    "distinct_location_count",
                    "status",
                )
            )
            _audit(
                actor=uploaded_by,
                action="attendance.import.preview",
                batch=batch,
                outcome=AuditLog.Outcome.SUCCESS,
                after_json={
                    "daily_records": batch.daily_record_count,
                    "matched": batch.matched_row_count,
                    "unmatched": batch.unmatched_row_count,
                    "errors": batch.error_count,
                    "warnings": batch.warning_count,
                },
            )
            return batch
        except Exception:
            default_storage.delete(saved_storage_key)
            raise


def _refresh_batch_validation(batch: ImportBatch) -> None:
    error_count = ImportError.objects.filter(
        batch=batch, severity=ImportError.Severity.ERROR
    ).count()
    warning_count = ImportError.objects.filter(
        batch=batch, severity=ImportError.Severity.WARNING
    ).count()
    unmatched_count = ImportError.objects.filter(
        batch=batch,
        error_code="employee_not_found",
        row__isnull=False,
    ).count()
    matched_count = ImportRow.objects.filter(
        batch=batch, matched_employee__isnull=False
    ).count()
    batch.error_count = error_count
    batch.warning_count = warning_count
    batch.unmatched_row_count = unmatched_count
    batch.matched_row_count = matched_count
    batch.status = ImportBatch.Status.HAS_ERRORS if error_count else ImportBatch.Status.PREVIEW_READY
    batch.save(
        update_fields=(
            "error_count",
            "warning_count",
            "unmatched_row_count",
            "matched_row_count",
            "status",
        )
    )


def _refresh_row_validation(row: ImportRow) -> None:
    severities = set(row.errors.values_list("severity", flat=True))
    row.validation_status = (
        ImportRow.ValidationStatus.ERROR
        if ImportError.Severity.ERROR in severities
        else ImportRow.ValidationStatus.WARNING
        if ImportError.Severity.WARNING in severities
        else ImportRow.ValidationStatus.VALID
    )
    row.save(update_fields=("validation_status",))


def _national_id_from_import_row(row: ImportRow) -> str:
    decrypted = decrypt_sensitive_bytes(
        bytes(row.raw_payload_encrypted),
        context=f"attendance-row:{row.batch_id}:{row.row_number}",
        key_version=row.encryption_key_version,
    )
    payload = json.loads(decrypted.decode("utf-8"))
    return normalize_national_id(payload.get("national_id"))


@transaction.atomic
def resolve_unmatched_employee(
    batch: ImportBatch,
    *,
    national_id_hash: str,
    action: str,
    resolved_by,
    department: Department | None = None,
) -> tuple[str, int]:
    """Ignore or create one unique unmatched employee and refresh the batch."""
    _require_system_admin(resolved_by)
    locked = ImportBatch.objects.select_for_update().get(pk=batch.pk)
    if locked.status == ImportBatch.Status.APPROVED:
        raise AttendanceImportServiceError("لا يمكن معالجة دفعة معتمدة.", code="already_approved")

    rows = list(
        ImportRow.objects.select_for_update()
        .filter(
            batch=locked,
            national_id_hash=national_id_hash,
            matched_employee__isnull=True,
            errors__error_code="employee_not_found",
        )
        .distinct()
        .order_by("row_number")
    )
    if not rows:
        raise AttendanceImportServiceError(
            "لم يعد الموظف ضمن قائمة غير المطابقين.", code="unmatched_employee_not_found"
        )

    if action == "ignore":
        ImportError.objects.filter(row__in=rows, error_code="employee_not_found").delete()
        for row in rows:
            _refresh_row_validation(row)
        _refresh_batch_validation(locked)
        _audit(
            actor=resolved_by,
            action="attendance.import.ignore_unmatched_employee",
            batch=locked,
            outcome=AuditLog.Outcome.SUCCESS,
            after_json={"rows_ignored": len(rows), "national_id_last4": rows[0].national_id_last4},
        )
        return "ignored", len(rows)

    if action != "add" or department is None:
        raise AttendanceImportServiceError("بيانات إضافة الموظف غير مكتملة.", code="invalid_resolution")
    if not department.is_active:
        raise AttendanceImportServiceError("القسم المختار غير نشط.", code="inactive_reference")

    normalized_national_id = _national_id_from_import_row(rows[0])
    if national_id_digest(normalized_national_id) != national_id_hash:
        raise AttendanceImportServiceError("تعذر التحقق من هوية الموظف المستوردة.", code="identity_mismatch")
    if EmployeeIdentity.objects.filter(national_id_hash=national_id_hash).exists():
        raise AttendanceImportServiceError(
            "السجل المدني مرتبط بموظف موجود؛ أعد فتح المعاينة لتحديث المطابقة.",
            code="identity_already_exists",
        )

    display = rows[0].display_data_json or {}
    full_name = (display.get("employee_name") or "").strip()
    if not full_name:
        raise AttendanceImportServiceError("اسم الموظف غير متاح في الشيت.", code="missing_employee_name")
    employee = Employee.objects.create(
        full_name_ar=full_name,
        employment_status=Employee.EmploymentStatus.ACTIVE,
        created_by=resolved_by,
        updated_by=resolved_by,
    )
    encrypted = encrypt_sensitive_text(
        normalized_national_id,
        context=f"employee-national-id:{employee.id}",
    )
    EmployeeIdentity.objects.create(
        employee=employee,
        national_id_hash=national_id_hash,
        national_id_encrypted=encrypted.ciphertext,
        encryption_key_version=encrypted.key_version,
        national_id_last4=normalized_national_id[-4:],
        verified_at=timezone.now(),
        verification_source=EmployeeIdentity.VerificationSource.IMPORT,
        created_by=resolved_by,
        updated_by=resolved_by,
    )
    effective_date = locked.period_start or min(
        row.attendance_date for row in rows if row.attendance_date
    )
    EmploymentAssignment.objects.create(
        employee=employee,
        department=department,
        assignment_type=EmploymentAssignment.AssignmentType.PRIMARY,
        valid_from=effective_date,
        is_primary=True,
        reason="إضافة من معالجة استيراد الحضور",
        created_by=resolved_by,
        updated_by=resolved_by,
    )
    ImportError.objects.filter(row__in=rows, error_code="employee_not_found").delete()
    for row in rows:
        row.matched_employee = employee
        row.match_status = ImportRow.MatchStatus.MATCHED
        row.location_match_status = _location_match_status(
            check_in_location=row.source_check_in_location,
            check_out_location=row.source_check_out_location,
        )
        row.proposed_record_fingerprint = _stored_row_fingerprint(employee_id=employee.id, row=row)
        row.save(
            update_fields=(
                "matched_employee",
                "match_status",
                "location_match_status",
                "proposed_record_fingerprint",
            )
        )
        if row.location_match_status == ImportRow.LocationMatchStatus.MISMATCH:
            ImportError.objects.get_or_create(
                batch=locked,
                row=row,
                error_code="location_mismatch",
                defaults={
                    "severity": ImportError.Severity.WARNING,
                    "field_name": "check_in_location",
                    "message_ar": "مكان الحضور يختلف عن مكان الانصراف؛ سُجل كتنبيه فقط ولا يتطلب إفادة.",
                },
            )
        elif row.location_match_status == ImportRow.LocationMatchStatus.UNKNOWN:
            ImportError.objects.get_or_create(
                batch=locked,
                row=row,
                error_code="location_incomplete",
                defaults={
                    "severity": ImportError.Severity.WARNING,
                    "field_name": "location",
                    "message_ar": "تعذر مقارنة الموقعين لأن مكان الحضور أو الانصراف غير متاح؛ سُجل كتنبيه فقط.",
                },
            )
        _refresh_row_validation(row)

    _refresh_batch_validation(locked)
    _audit(
        actor=resolved_by,
        action="attendance.import.create_unmatched_employee",
        batch=locked,
        outcome=AuditLog.Outcome.SUCCESS,
        after_json={
            "employee_id": str(employee.id),
            "department_id": str(department.id),
            "rows_matched": len(rows),
            "national_id_masked": f"******{normalized_national_id[-4:]}",
        },
    )
    return "added", len(rows)


def _combine_datetime(day: date, value: time | None):
    if value is None:
        return None
    naive = datetime.combine(day, value)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def approve_attendance_import(batch: ImportBatch, *, approved_by) -> int:
    with transaction.atomic():
        locked = ImportBatch.objects.select_for_update().get(pk=batch.pk)
        if locked.status == ImportBatch.Status.APPROVED:
            raise AttendanceImportServiceError("تم اعتماد هذه الدفعة سابقًا.", code="already_approved")
        if locked.status != ImportBatch.Status.PREVIEW_READY or locked.error_count:
            raise AttendanceImportServiceError("لا يمكن اعتماد دفعة تحتوي أخطاء مانعة.", code="batch_not_approvable")
        if (
            locked.period_start
            and locked.period_end
            and ImportBatch.objects.filter(
                status=ImportBatch.Status.APPROVED,
                archived_at__isnull=True,
                period_start__lte=locked.period_end,
                period_end__gte=locked.period_start,
            )
            .exclude(pk=locked.pk)
            .exists()
        ):
            raise AttendanceImportServiceError(
                "تتداخل هذه الفترة مع فترة حضور معتمدة سابقًا.",
                code="overlapping_approved_period",
            )

        rows = list(
            ImportRow.objects.select_related("matched_employee")
            .filter(batch=locked, validation_status__in=[ImportRow.ValidationStatus.VALID, ImportRow.ValidationStatus.WARNING], matched_employee__isnull=False, is_duplicate=False)
            .order_by("row_number")
        )
        now = timezone.now()
        records = []
        for row in rows:
            if not row.proposed_record_fingerprint or not row.attendance_date:
                raise AttendanceImportServiceError("توجد صفوف غير مكتملة تمنع الاعتماد.", code="incomplete_staged_row")
            records.append(
                RawAttendanceRecord(
                    import_row=row,
                    employee=row.matched_employee,
                    national_id_hash=row.national_id_hash or "",
                    attendance_date=row.attendance_date,
                    source_check_in_at=_combine_datetime(row.attendance_date, row.source_check_in),
                    source_check_out_at=_combine_datetime(row.attendance_date, row.source_check_out),
                    source_check_in_location=row.source_check_in_location,
                    source_check_out_location=row.source_check_out_location,
                    primary_location=None,
                    source_status=row.source_status,
                    source_scheduled_duration=row.source_scheduled_duration,
                    source_actual_work_duration=row.source_actual_work_duration,
                    source_early_departure_duration=row.source_early_departure_duration,
                    source_shortfall_duration=row.source_shortfall_duration,
                    source_early_arrival_duration=row.source_early_arrival_duration,
                    record_fingerprint=row.proposed_record_fingerprint,
                    location_match_status=row.location_match_status,
                    matched_at=now,
                )
            )
        try:
            RawAttendanceRecord.objects.bulk_create(records)
        except IntegrityError as exc:
            raise AttendanceImportServiceError("تعذر الاعتماد بسبب سجل حضور مكرر.", code="duplicate_on_approve") from exc

        locked.status = ImportBatch.Status.APPROVED
        locked.approved_by = approved_by
        locked.approved_at = now
        locked.save(update_fields=("status", "approved_by", "approved_at"))

        # Build the first daily-analysis version immediately after a successful
        # approval. Importing here avoids a module-level circular dependency.
        from attendance.services.calculation import calculate_batch

        if records:
            calculate_batch(locked, requested_by=approved_by)
        _audit(
            actor=approved_by,
            action="attendance.import.approve",
            batch=locked,
            outcome=AuditLog.Outcome.SUCCESS,
            after_json={"created_records": len(records)},
        )
        return len(records)


def can_delete_attendance_import(batch: ImportBatch) -> bool:
    return batch.status != ImportBatch.Status.APPROVED and not RawAttendanceRecord.objects.filter(import_row__batch=batch).exists()


def _require_system_admin(actor) -> None:
    if not getattr(actor, "is_authenticated", False) or not actor.is_active or not actor.is_superuser:
        raise AttendanceImportServiceError(
            "هذا الإجراء متاح لمدير النظام فقط.", code="system_admin_required"
        )


def _validated_reason(reason: str) -> str:
    normalized = (reason or "").strip()
    if len(normalized) < 5:
        raise AttendanceImportServiceError("يجب كتابة سبب واضح للإجراء.", code="reason_required")
    return normalized


@transaction.atomic
def update_attendance_import_metadata(
    batch: ImportBatch,
    *,
    display_name: str,
    source_period_title: str,
    reason: str,
    updated_by,
) -> ImportBatch:
    _require_system_admin(updated_by)
    reason = _validated_reason(reason)
    display_name = display_name.strip()
    if not display_name:
        raise AttendanceImportServiceError("الاسم الظاهر مطلوب.", code="display_name_required")
    locked = ImportBatch.objects.select_for_update().get(pk=batch.pk)
    before = {
        "display_name": locked.display_name,
        "source_period_title": locked.source_period_title,
    }
    locked.display_name = display_name
    locked.source_period_title = source_period_title.strip()
    locked.save(update_fields=("display_name", "source_period_title"))
    AuditLog.objects.create(
        actor_user=updated_by,
        actor_username_snapshot=redact_potential_national_ids(updated_by.username),
        action="attendance.import.update_metadata",
        module="attendance",
        object_type="attendance_import_batch",
        object_id=locked.id,
        object_repr_masked=f"دفعة حضور {str(locked.id)[:8]}",
        before_json=before,
        after_json={
            "display_name": redact_potential_national_ids(locked.display_name),
            "source_period_title": redact_potential_national_ids(locked.source_period_title),
        },
        reason=reason,
        outcome=AuditLog.Outcome.SUCCESS,
    )
    return locked


@transaction.atomic
def archive_attendance_import(batch: ImportBatch, *, archived_by, reason: str) -> ImportBatch:
    _require_system_admin(archived_by)
    reason = _validated_reason(reason)
    locked = ImportBatch.objects.select_for_update().get(pk=batch.pk)
    if locked.status != ImportBatch.Status.APPROVED:
        raise AttendanceImportServiceError(
            "الأرشفة مخصصة للدفعات المعتمدة؛ يمكن حذف الدفعة غير المعتمدة.",
            code="archive_requires_approved",
        )
    if locked.archived_at:
        raise AttendanceImportServiceError("الدفعة مؤرشفة مسبقًا.", code="already_archived")
    locked.archived_at = timezone.now()
    locked.archived_by = archived_by
    locked.archive_reason = reason
    locked.save(update_fields=("archived_at", "archived_by", "archive_reason"))
    AuditLog.objects.create(
        actor_user=archived_by,
        actor_username_snapshot=redact_potential_national_ids(archived_by.username),
        action="attendance.import.archive",
        module="attendance",
        object_type="attendance_import_batch",
        object_id=locked.id,
        object_repr_masked=f"دفعة حضور {str(locked.id)[:8]}",
        after_json={"archived": True},
        reason=reason,
        outcome=AuditLog.Outcome.SUCCESS,
    )
    return locked


@transaction.atomic
def restore_attendance_import(batch: ImportBatch, *, restored_by, reason: str) -> ImportBatch:
    _require_system_admin(restored_by)
    reason = _validated_reason(reason)
    locked = ImportBatch.objects.select_for_update().get(pk=batch.pk)
    if not locked.archived_at:
        raise AttendanceImportServiceError("الدفعة غير مؤرشفة.", code="not_archived")
    locked.archived_at = None
    locked.archived_by = None
    locked.archive_reason = ""
    locked.save(update_fields=("archived_at", "archived_by", "archive_reason"))
    AuditLog.objects.create(
        actor_user=restored_by,
        actor_username_snapshot=redact_potential_national_ids(restored_by.username),
        action="attendance.import.restore",
        module="attendance",
        object_type="attendance_import_batch",
        object_id=locked.id,
        object_repr_masked=f"دفعة حضور {str(locked.id)[:8]}",
        before_json={"archived": True},
        after_json={"archived": False},
        reason=reason,
        outcome=AuditLog.Outcome.SUCCESS,
    )
    return locked


def delete_attendance_import(batch: ImportBatch, *, deleted_by, reason: str) -> None:
    _require_system_admin(deleted_by)
    reason = _validated_reason(reason)
    with transaction.atomic():
        locked = ImportBatch.objects.select_for_update().get(pk=batch.pk)
        if not can_delete_attendance_import(locked):
            raise AttendanceImportServiceError("لا يمكن حذف دفعة حضور معتمدة.", code="cannot_delete_approved")
        storage_key = locked.storage_key
        batch_id = locked.id
        ImportError.objects.filter(batch=locked).delete()
        ImportRow.objects.filter(batch=locked).delete()
        locked.delete()
        default_storage.delete(storage_key)
        AuditLog.objects.create(
            actor_user=deleted_by,
            actor_username_snapshot=redact_potential_national_ids(getattr(deleted_by, "username", "") or "") or None,
            action="attendance.import.delete",
            module="attendance",
            object_type="attendance_import_batch",
            object_id=batch_id,
            object_repr_masked=f"دفعة حضور {str(batch_id)[:8]}",
            reason=reason,
            outcome=AuditLog.Outcome.SUCCESS,
        )
