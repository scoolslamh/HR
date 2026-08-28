from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import ImportBatch
from .services.weekly_import import (
    AttendanceImportServiceError,
    approve_attendance_import,
)


class AttendancePeriodIntegrityTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="period-integrity-admin",
            password="Strong-Test-Pass-2026",
        )

    def create_batch(self, suffix, start, end, *, status, approved_at=None):
        return ImportBatch.objects.create(
            original_filename=f"{suffix}.xlsx",
            storage_key=f"attendance/{suffix}.xlsx",
            file_sha256=suffix.ljust(64, "0"),
            file_size_bytes=100,
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            period_start=start,
            period_end=end,
            status=status,
            approved_by=self.admin if approved_at else None,
            approved_at=approved_at,
        )

    def test_overlapping_approved_period_is_rejected(self):
        self.create_batch(
            "approved",
            date(2026, 8, 1),
            date(2026, 8, 7),
            status=ImportBatch.Status.APPROVED,
            approved_at=timezone.now(),
        )
        candidate = self.create_batch(
            "candidate",
            date(2026, 8, 7),
            date(2026, 8, 13),
            status=ImportBatch.Status.PREVIEW_READY,
        )

        with self.assertRaises(AttendanceImportServiceError) as raised:
            approve_attendance_import(candidate, approved_by=self.admin)

        self.assertEqual(raised.exception.code, "overlapping_approved_period")
