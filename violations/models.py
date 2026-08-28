import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


def clarification_evidence_path(instance, filename: str) -> str:
    extension = Path(filename).suffix.lower()[:10]
    return f"clarification-evidence/{instance.clarification_id}/{uuid.uuid4()}{extension}"


class ClarificationRequest(models.Model):
    class Kind(models.TextChoices):
        ABSENCE = "absence", "غياب"
        OUTSIDE_LOCATION = "outside_location", "توقيع خارج الموقع"
        AUTOMATIC_CHECKOUT = "automatic_checkout", "انصراف تلقائي"

    class Status(models.TextChoices):
        AWAITING_EMPLOYEE = "awaiting_employee", "بانتظار إفادة الموظف"
        AWAITING_MANAGER = "awaiting_manager", "بانتظار اعتماد رئيس القسم"
        RETURNED = "returned", "معادة للاستكمال"
        APPROVED = "approved", "معتمدة"
        REJECTED = "rejected", "مرفوضة"
        CANCELLED = "cancelled", "ألغيت بإعادة الاحتساب"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(
        "organization.Employee",
        on_delete=models.PROTECT,
        related_name="clarification_requests",
        verbose_name="الموظف",
    )
    department = models.ForeignKey(
        "organization.Department",
        on_delete=models.PROTECT,
        related_name="clarification_requests",
        verbose_name="القسم وقت المخالفة",
        null=True,
        blank=True,
    )
    attendance_result = models.ForeignKey(
        "attendance.DailyAttendanceResult",
        on_delete=models.PROTECT,
        related_name="clarification_requests",
        verbose_name="نتيجة الحضور",
    )
    attendance_date = models.DateField("تاريخ الحالة")
    kind = models.CharField("نوع الإفادة", max_length=30, choices=Kind.choices)
    status = models.CharField(
        "الحالة", max_length=30, choices=Status.choices, default=Status.AWAITING_EMPLOYEE
    )
    employee_explanation = models.TextField("مبرر الموظف", blank=True)
    submitted_at = models.DateTimeField("وقت إرسال الإفادة", null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="clarifications_reviewed",
        verbose_name="المعتمد",
        null=True,
        blank=True,
    )
    review_comment = models.TextField("تعليق رئيس القسم", blank=True)
    reviewed_at = models.DateTimeField("وقت القرار", null=True, blank=True)
    created_at = models.DateTimeField("وقت الإرسال التلقائي", default=timezone.now)
    updated_at = models.DateTimeField("آخر تحديث", auto_now=True)

    class Meta:
        db_table = "clarification_requests"
        ordering = ("-attendance_result__attendance_date", "employee__full_name_ar")
        verbose_name = "طلب إفادة"
        verbose_name_plural = "طلبات الإفادة"
        constraints = [
            models.UniqueConstraint(
                fields=("employee", "attendance_date", "kind"),
                name="clarification_employee_date_kind_uq",
            ),
            models.CheckConstraint(
                condition=Q(reviewed_at__isnull=True) | Q(reviewed_by__isnull=False),
                name="clarification_review_actor_ck",
            ),
        ]
        indexes = [
            models.Index(fields=("employee", "status"), name="clar_emp_status_idx"),
            models.Index(fields=("department", "status"), name="clar_dept_status_idx"),
            models.Index(fields=("kind", "status"), name="clar_kind_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} - {self.employee}"


class ClarificationEvidence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clarification = models.ForeignKey(
        ClarificationRequest,
        on_delete=models.PROTECT,
        related_name="evidence_files",
        verbose_name="طلب الإفادة",
    )
    file = models.FileField("الشاهد", upload_to=clarification_evidence_path, max_length=500)
    original_filename = models.CharField("اسم الملف الأصلي", max_length=255)
    content_type = models.CharField("نوع المحتوى", max_length=100)
    file_size = models.PositiveBigIntegerField("حجم الملف")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="clarification_evidence_uploaded",
        verbose_name="رافع الملف",
    )
    created_at = models.DateTimeField("وقت الرفع", auto_now_add=True)

    class Meta:
        db_table = "clarification_evidence"
        ordering = ("created_at",)
        verbose_name = "شاهد إفادة"
        verbose_name_plural = "شواهد الإفادات"
