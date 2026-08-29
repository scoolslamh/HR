from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from datetime import date, timedelta
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook

from organization.models import (
    Department,
    Employee,
    EmployeeIdentity,
    EmployeePrimaryLocation,
    EmploymentAssignment,
    Location,
)
from organization.services.identity import (
    decrypt_sensitive_bytes,
    encrypt_sensitive_text,
    national_id_digest,
)

from attendance.models import ImportBatch, ImportError, ImportRow, RawAttendanceRecord
from attendance.services.weekly_import import (
    AttendanceImportServiceError,
    approve_attendance_import,
    delete_attendance_import,
    preview_attendance_import,
    resolve_unmatched_employee,
)


def _key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


HEADERS = (
    "السجل المدني", "الاسم", "المسمى الوظيفي", "التاريخ", "حالة التحضير",
    "ساعات الدوام", "وقت الحضور", "مكان الحضور", "توقيت الانصراف",
    "مكان الانصراف", "ساعات الدوام الفعلي", "انصراف مبكر",
    "النقص في الدوام", "حضور مبكر",
)


def workbook_bytes(
    *,
    national_id="1023456789",
    employee_name="موظف تجريبي",
    location="المقر الرئيسي",
    out_location=None,
    second_day=True,
    duplicate_first_day=False,
) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "الحضور والانصراف"
    ws.append(["الحضور والانصراف للموظفين من: 2026/07/05 إلى 2026/07/09"])
    ws.append([None] * len(HEADERS))
    ws.append(HEADERS)
    out_location = location if out_location is None else out_location
    first_row = [national_id, employee_name, "محلل", "الأحد 2026/07/05", "مكتملة", "07:00", "07:00", location, "14:00", out_location, "07:00", "00:00", "00:00", "00:00"]
    ws.append(first_row)
    if duplicate_first_day:
        ws.append(first_row)
    if second_day:
        ws.append([None, None, None, "الاثنين 2026/07/06", "مكتملة", "07:00", "07:05", location, "14:03", out_location, "06:58", "00:00", "00:02", "00:00"])
    ws.append(["المجموع", None, None, None, None, "14:00", None, None, None, None, "13:58", None, "00:02", None])
    out = BytesIO()
    wb.save(out)
    wb.close()
    return out.getvalue()


def workbook_with_daily_rows(row_count: int, *, start=date(2026, 8, 1)) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "الحضور والانصراف"
    end = start + timedelta(days=max(row_count - 1, 0))
    ws.append(
        [
            f"الحضور والانصراف للموظفين من: {start:%Y/%m/%d} "
            f"إلى {end:%Y/%m/%d}"
        ]
    )
    ws.append([None] * len(HEADERS))
    ws.append(HEADERS)
    for index in range(row_count):
        ws.append(
            [
                "1023456789" if index == 0 else None,
                "موظف تجريبي" if index == 0 else None,
                "محلل" if index == 0 else None,
                start + timedelta(days=index),
                "مكتملة",
                "07:00",
                "07:00",
                "المقر الرئيسي",
                "14:00",
                "المقر الرئيسي",
                "07:00",
                "00:00",
                "00:00",
                "00:00",
            ]
        )
    out = BytesIO()
    wb.save(out)
    wb.close()
    return out.getvalue()


@override_settings(
    PII_ENCRYPTION_KEY=_key(),
    NATIONAL_ID_HMAC_KEY=_key(),
    PII_ENCRYPTION_KEY_VERSION="test-v1",
    ATTENDANCE_IMPORT_MAX_BYTES=5 * 1024 * 1024,
)
class AttendanceWeeklyImportTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.override_media = override_settings(MEDIA_ROOT=self.temp_dir.name)
        self.override_media.enable()
        self.addCleanup(self.override_media.disable)
        self.addCleanup(self.temp_dir.cleanup)

        self.user = get_user_model().objects.create_superuser(
            username="attendance-admin", password="Strong-Test-Pass-2026"
        )
        self.department = Department.objects.create(
            code="SEC", name_ar="الأمن والسلامة", unit_type=Department.UnitType.DEPARTMENT,
            level=1, valid_from=date(2026, 1, 1), created_by=self.user,
        )
        self.location = Location.objects.create(
            code="HQ", name_ar="المقر الرئيسي", location_type=Location.LocationType.HEADQUARTERS,
            department=self.department, created_by=self.user,
        )
        self.employee = Employee.objects.create(full_name_ar="موظف تجريبي", created_by=self.user)
        encrypted = encrypt_sensitive_text("1023456789", context=f"employee-national-id:{self.employee.id}")
        EmployeeIdentity.objects.create(
            employee=self.employee,
            national_id_hash=national_id_digest("1023456789"),
            national_id_encrypted=encrypted.ciphertext,
            encryption_key_version=encrypted.key_version,
            national_id_last4="6789",
            verified_at=timezone.now(),
            verification_source=EmployeeIdentity.VerificationSource.IMPORT,
            created_by=self.user,
        )
        EmployeePrimaryLocation.objects.create(
            employee=self.employee, location=self.location, valid_from=date(2026, 1, 1), created_by=self.user,
        )

    def upload(self, content=None, name="weekly.xlsx"):
        return SimpleUploadedFile(
            name, content or workbook_bytes(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def test_preview_matches_employee_and_preserves_only_masked_display(self):
        batch = preview_attendance_import(self.upload(), uploaded_by=self.user)
        self.assertEqual(batch.status, ImportBatch.Status.PREVIEW_READY)
        self.assertEqual(batch.daily_record_count, 2)
        self.assertEqual(batch.matched_row_count, 2)
        self.assertEqual(batch.error_count, 0)
        row = batch.rows.order_by("row_number").first()
        self.assertEqual(row.matched_employee, self.employee)
        self.assertEqual(row.display_data_json["national_id_masked"], "******6789")
        self.assertNotIn("1023456789", str(row.display_data_json))

    def test_unmatched_employee_is_blocking_error_and_not_created(self):
        batch = preview_attendance_import(
            self.upload(workbook_bytes(national_id="1099999999", second_day=False)),
            uploaded_by=self.user,
        )
        self.assertEqual(batch.status, ImportBatch.Status.HAS_ERRORS)
        self.assertEqual(batch.unmatched_row_count, 1)
        self.assertTrue(ImportError.objects.filter(batch=batch, error_code="employee_not_found").exists())
        error = ImportError.objects.get(batch=batch, error_code="employee_not_found")
        self.assertEqual(error.row_id, batch.rows.get().id)
        self.assertEqual(Employee.objects.count(), 1)

    def test_raw_payload_remains_encrypted_and_decryptable_after_bulk_insert(self):
        batch = preview_attendance_import(
            self.upload(workbook_bytes(second_day=False)), uploaded_by=self.user
        )
        row = batch.rows.get()

        plaintext = decrypt_sensitive_bytes(
            bytes(row.raw_payload_encrypted),
            context=f"attendance-row:{batch.id}:{row.row_number}",
            key_version=row.encryption_key_version,
        )

        self.assertEqual(json.loads(plaintext)["national_id"], "1023456789")
        self.assertNotIn(b"1023456789", bytes(row.raw_payload_encrypted))

    def test_record_fingerprint_format_is_unchanged(self):
        batch = preview_attendance_import(
            self.upload(workbook_bytes(second_day=False)), uploaded_by=self.user
        )
        row = batch.rows.get()
        expected = hashlib.sha256(
            "|".join(
                (
                    str(self.employee.id),
                    "2026-07-05",
                    "07:00:00",
                    "14:00:00",
                    "المقر الرئيسي",
                    "المقر الرئيسي",
                )
            ).encode("utf-8")
        ).hexdigest()

        self.assertEqual(row.proposed_record_fingerprint, expected)

    def test_national_id_digest_is_calculated_once_for_repeated_employee(self):
        with patch(
            "attendance.services.weekly_import.national_id_digest",
            wraps=national_id_digest,
        ) as digest:
            batch = preview_attendance_import(self.upload(), uploaded_by=self.user)

        self.assertEqual(batch.matched_row_count, 2)
        self.assertEqual(digest.call_count, 1)

    def test_duplicate_in_raw_attendance_is_detected(self):
        first = preview_attendance_import(
            self.upload(workbook_bytes(second_day=False)), uploaded_by=self.user
        )
        approve_attendance_import(first, approved_by=self.user)

        second = preview_attendance_import(
            self.upload(
                workbook_bytes(employee_name="اسم مختلف", second_day=False),
                "second.xlsx",
            ),
            uploaded_by=self.user,
        )

        self.assertTrue(second.rows.get().is_duplicate)
        self.assertTrue(
            second.errors.filter(error_code="duplicate_daily_record").exists()
        )

    def test_duplicate_in_existing_import_row_is_detected(self):
        preview_attendance_import(
            self.upload(workbook_bytes(second_day=False)), uploaded_by=self.user
        )

        second = preview_attendance_import(
            self.upload(
                workbook_bytes(employee_name="اسم آخر", second_day=False),
                "second.xlsx",
            ),
            uploaded_by=self.user,
        )

        self.assertTrue(second.rows.get().is_duplicate)

    def test_duplicate_inside_same_workbook_is_detected_in_row_order(self):
        batch = preview_attendance_import(
            self.upload(
                workbook_bytes(second_day=False, duplicate_first_day=True)
            ),
            uploaded_by=self.user,
        )
        rows = list(batch.rows.order_by("row_number"))

        self.assertFalse(rows[0].is_duplicate)
        self.assertTrue(rows[1].is_duplicate)
        self.assertTrue(
            ImportError.objects.filter(
                batch=batch,
                row=rows[1],
                error_code="duplicate_daily_record",
            ).exists()
        )

    def test_preview_rolls_back_all_database_rows_when_bulk_insert_fails(self):
        with patch(
            "attendance.services.weekly_import.ImportRow.objects.bulk_create",
            side_effect=RuntimeError("forced bulk insert failure"),
        ):
            with self.assertRaises(RuntimeError):
                preview_attendance_import(self.upload(), uploaded_by=self.user)

        self.assertEqual(ImportBatch.objects.count(), 0)
        self.assertEqual(ImportRow.objects.count(), 0)
        self.assertEqual(ImportError.objects.count(), 0)

    def test_preview_query_count_does_not_grow_per_daily_row(self):
        with CaptureQueriesContext(connection) as small_queries:
            small_batch = preview_attendance_import(
                self.upload(
                    workbook_with_daily_rows(5, start=date(2026, 8, 1)),
                    "small.xlsx",
                ),
                uploaded_by=self.user,
            )
        with CaptureQueriesContext(connection) as larger_queries:
            larger_batch = preview_attendance_import(
                self.upload(
                    workbook_with_daily_rows(120, start=date(2027, 1, 1)),
                    "larger.xlsx",
                ),
                uploaded_by=self.user,
            )

        self.assertEqual(small_batch.matched_row_count, 5)
        self.assertEqual(larger_batch.matched_row_count, 120)
        self.assertLessEqual(len(larger_queries), len(small_queries) + 8)

    def test_large_15000_row_preview_preserves_results(self):
        """Exercise the production-sized path without changing import rules."""
        batch = preview_attendance_import(
            self.upload(
                workbook_with_daily_rows(15_000, start=date(2030, 1, 1)),
                "large-15000.xlsx",
            ),
            uploaded_by=self.user,
        )

        self.assertEqual(batch.daily_record_count, 15_000)
        self.assertEqual(batch.matched_row_count, 15_000)
        self.assertEqual(batch.error_count, 0)
        self.assertEqual(batch.rows.count(), 15_000)

    def test_approval_bulk_creates_in_bounded_batches(self):
        batch = preview_attendance_import(
            self.upload(
                workbook_with_daily_rows(1_201, start=date(2075, 1, 1)),
                "approval-batches.xlsx",
            ),
            uploaded_by=self.user,
        )
        original_bulk_create = RawAttendanceRecord.objects.bulk_create
        batch_sizes = []

        def recording_bulk_create(records, **kwargs):
            batch_sizes.append(len(records))
            return original_bulk_create(records, **kwargs)

        with patch(
            "attendance.services.weekly_import.RawAttendanceRecord.objects.bulk_create",
            side_effect=recording_bulk_create,
        ), patch("attendance.services.calculation.calculate_batch"):
            created = approve_attendance_import(batch, approved_by=self.user)

        self.assertEqual(created, 1_201)
        self.assertEqual(batch_sizes, [500, 500, 201])

    def test_unmatched_employee_can_be_ignored_then_batch_can_be_approved(self):
        batch = preview_attendance_import(
            self.upload(workbook_bytes(national_id="1099999999", second_day=False)),
            uploaded_by=self.user,
        )
        row = batch.rows.get()

        action, count = resolve_unmatched_employee(
            batch,
            national_id_hash=row.national_id_hash,
            action="ignore",
            resolved_by=self.user,
        )

        batch.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual((action, count), ("ignored", 1))
        self.assertEqual(batch.status, ImportBatch.Status.PREVIEW_READY)
        self.assertEqual(batch.error_count, 0)
        self.assertEqual(batch.unmatched_row_count, 0)
        self.assertIsNone(row.matched_employee)
        self.assertEqual(approve_attendance_import(batch, approved_by=self.user), 0)

    def test_unmatched_employee_can_be_added_with_department_and_location(self):
        batch = preview_attendance_import(
            self.upload(workbook_bytes(national_id="1099999999", second_day=False)),
            uploaded_by=self.user,
        )
        row = batch.rows.get()

        action, count = resolve_unmatched_employee(
            batch,
            national_id_hash=row.national_id_hash,
            action="add",
            resolved_by=self.user,
            department=self.department,
        )

        batch.refresh_from_db()
        row.refresh_from_db()
        created_employee = row.matched_employee
        self.assertEqual((action, count), ("added", 1))
        self.assertEqual(created_employee.full_name_ar, "موظف تجريبي")
        self.assertTrue(EmployeeIdentity.objects.filter(employee=created_employee).exists())
        self.assertTrue(
            EmploymentAssignment.objects.filter(
                employee=created_employee,
                department=self.department,
                valid_from=date(2026, 7, 5),
            ).exists()
        )
        self.assertFalse(EmployeePrimaryLocation.objects.filter(employee=created_employee).exists())
        self.assertEqual(batch.status, ImportBatch.Status.PREVIEW_READY)
        self.assertEqual(batch.error_count, 0)
        self.assertEqual(batch.matched_row_count, 1)

    def test_unmatched_resolution_is_visible_in_preview(self):
        batch = preview_attendance_import(
            self.upload(workbook_bytes(national_id="1099999999", second_day=False)),
            uploaded_by=self.user,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("attendance:import_preview", args=[batch.id]))

        self.assertContains(response, "الموظفون غير المطابقين")
        self.assertContains(response, "إضافة ومطابقة")
        self.assertContains(response, "تجاهل السجلات")
        self.assertContains(response, "******9999")

    def test_same_file_cannot_be_previewed_twice(self):
        content = workbook_bytes()
        preview_attendance_import(self.upload(content), uploaded_by=self.user)
        with self.assertRaises(AttendanceImportServiceError):
            preview_attendance_import(self.upload(content, "copy.xlsx"), uploaded_by=self.user)

    def test_location_mismatch_is_warning_not_blocking_error(self):
        batch = preview_attendance_import(
            self.upload(workbook_bytes(location="موقع ميداني", out_location="المقر الرئيسي")), uploaded_by=self.user
        )
        self.assertEqual(batch.status, ImportBatch.Status.PREVIEW_READY)
        self.assertEqual(batch.error_count, 0)
        self.assertGreater(batch.warning_count, 0)
        self.assertTrue(batch.rows.filter(location_match_status=ImportRow.LocationMatchStatus.MISMATCH).exists())

    def test_missing_checkout_location_is_warning_not_blocking_error(self):
        batch = preview_attendance_import(
            self.upload(workbook_bytes(out_location="")), uploaded_by=self.user
        )

        self.assertEqual(batch.status, ImportBatch.Status.PREVIEW_READY)
        self.assertEqual(batch.error_count, 0)
        self.assertTrue(
            ImportError.objects.filter(batch=batch, error_code="location_incomplete").exists()
        )

    def test_approve_creates_raw_records_and_is_idempotent(self):
        batch = preview_attendance_import(self.upload(), uploaded_by=self.user)
        created = approve_attendance_import(batch, approved_by=self.user)
        self.assertEqual(created, 2)
        self.assertEqual(RawAttendanceRecord.objects.filter(import_row__batch=batch).count(), 2)
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.Status.APPROVED)
        with self.assertRaises(AttendanceImportServiceError):
            approve_attendance_import(batch, approved_by=self.user)

    def test_unapproved_batch_can_be_deleted(self):
        batch = preview_attendance_import(self.upload(), uploaded_by=self.user)
        batch_id = batch.id
        delete_attendance_import(batch, deleted_by=self.user, reason="حذف ملف اختبار غير معتمد")
        self.assertFalse(ImportBatch.objects.filter(id=batch_id).exists())

    def test_approved_batch_cannot_be_deleted(self):
        batch = preview_attendance_import(self.upload(), uploaded_by=self.user)
        approve_attendance_import(batch, approved_by=self.user)
        batch.refresh_from_db()
        with self.assertRaises(AttendanceImportServiceError):
            delete_attendance_import(batch, deleted_by=self.user, reason="محاولة حذف ملف معتمد")

    def test_views_require_business_permission(self):
        normal_user = get_user_model().objects.create_user(
            username="no-permission", password="Strong-Test-Pass-2026"
        )
        self.client.force_login(normal_user)
        response = self.client.get(reverse("attendance:import_list"))
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.user)
        response = self.client.get(reverse("attendance:import_upload"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-attendance-upload-form")
        self.assertContains(response, "تقدم رفع ملف الحضور")
        self.assertContains(response, "attendance/js/import-upload")

    def test_detail_displays_inclusive_period_day_count(self):
        batch = preview_attendance_import(self.upload(), uploaded_by=self.user)
        self.client.force_login(self.user)

        response = self.client.get(reverse("attendance:import_detail", args=[batch.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "من 2026/07/05 إلى 2026/07/09 — 5 يومًا")
        self.assertContains(response, "النص الأصلي أعلى الشيت")
