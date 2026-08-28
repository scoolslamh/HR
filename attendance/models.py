import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from organization.models import Employee, Location


class ImportBatch(models.Model):
    class Status(models.TextChoices):
        UPLOADED = "uploaded", "مرفوع"
        PREVIEW_READY = "preview_ready", "جاهز للمعاينة"
        HAS_ERRORS = "has_errors", "به أخطاء"
        APPROVED = "approved", "معتمد"
        FAILED = "failed", "فشل"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    original_filename = models.CharField("اسم الملف الأصلي", max_length=255)
    display_name = models.CharField("الاسم الظاهر", max_length=255, blank=True)
    storage_key = models.CharField("مفتاح التخزين", max_length=500, unique=True)
    file_sha256 = models.CharField("بصمة الملف", max_length=64, unique=True)
    file_size_bytes = models.PositiveBigIntegerField("حجم الملف")
    mime_type = models.CharField("نوع الملف", max_length=100)
    template_version = models.CharField(
        "إصدار القالب", max_length=30, default="weekly_summary_v1"
    )
    source_name = models.CharField(
        "مصدر الملف", max_length=100, default="تقرير البصمة الأسبوعي"
    )
    source_period_title = models.CharField("عنوان الفترة", max_length=255, blank=True)
    period_start = models.DateField("بداية الفترة", null=True, blank=True)
    period_end = models.DateField("نهاية الفترة", null=True, blank=True)
    status = models.CharField(
        "الحالة", max_length=20, choices=Status.choices, default=Status.UPLOADED
    )
    source_row_count = models.PositiveIntegerField("صفوف المصدر", default=0)
    employee_count = models.PositiveIntegerField("عدد الموظفين", default=0)
    daily_record_count = models.PositiveIntegerField("سجلات الأيام", default=0)
    matched_row_count = models.PositiveIntegerField("السجلات المطابقة", default=0)
    unmatched_row_count = models.PositiveIntegerField("السجلات غير المطابقة", default=0)
    ignored_row_count = models.PositiveIntegerField("الصفوف المتجاهلة", default=0)
    summary_row_count = models.PositiveIntegerField("صفوف المجموع", default=0)
    error_count = models.PositiveIntegerField("الأخطاء", default=0)
    warning_count = models.PositiveIntegerField("التحذيرات", default=0)
    distinct_location_count = models.PositiveIntegerField("المواقع المختلفة", default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="رافع الملف",
        on_delete=models.SET_NULL,
        related_name="attendance_imports_uploaded",
        null=True,
        blank=True,
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="معتمد الملف",
        on_delete=models.SET_NULL,
        related_name="attendance_imports_approved",
        null=True,
        blank=True,
    )
    approved_at = models.DateTimeField("وقت الاعتماد", null=True, blank=True)
    archived_at = models.DateTimeField("وقت الأرشفة", null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="أرشفها",
        on_delete=models.SET_NULL,
        related_name="attendance_imports_archived",
        null=True,
        blank=True,
    )
    archive_reason = models.TextField("سبب الأرشفة", blank=True)
    created_at = models.DateTimeField("وقت الإنشاء", auto_now_add=True)

    class Meta:
        db_table = "import_batches"
        ordering = ("-created_at",)
        verbose_name = "دفعة استيراد حضور"
        verbose_name_plural = "دفعات استيراد الحضور"
        constraints = [
            models.CheckConstraint(
                condition=Q(file_size_bytes__gt=0), name="att_import_size_ck"
            ),
            models.CheckConstraint(
                condition=Q(period_start__isnull=True)
                | Q(period_end__isnull=True)
                | Q(period_end__gte=F("period_start")),
                name="att_import_period_ck",
            ),
            models.CheckConstraint(
                condition=(Q(status="approved") & Q(approved_at__isnull=False))
                | (~Q(status="approved") & Q(approved_at__isnull=True)),
                name="att_import_approval_ck",
            ),
            models.CheckConstraint(
                condition=Q(archived_at__isnull=True)
                | (Q(archived_by__isnull=False) & ~Q(archive_reason="")),
                name="att_import_archive_data_ck",
            ),
        ]
        indexes = [
            models.Index(fields=("status", "created_at"), name="att_import_status_idx"),
            models.Index(fields=("period_start", "period_end"), name="att_import_period_idx"),
        ]

    def __str__(self):
        return f"دفعة حضور {self.id}"


class ImportRow(models.Model):
    class MatchStatus(models.TextChoices):
        MATCHED = "matched", "مطابق"
        UNMATCHED = "unmatched", "غير مطابق"
        INVALID = "invalid", "غير صالح"

    class ValidationStatus(models.TextChoices):
        VALID = "valid", "صالح"
        WARNING = "warning", "تحذير"
        ERROR = "error", "خطأ"

    class LocationMatchStatus(models.TextChoices):
        MATCHED = "matched", "مطابق"
        MISMATCH = "mismatch", "موقع مختلف"
        UNKNOWN = "unknown", "غير معروف"
        NOT_REQUIRED = "not_required", "غير مطلوب"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.PROTECT,
        related_name="rows",
        db_index=False,
        verbose_name="دفعة الاستيراد",
    )
    row_number = models.PositiveIntegerField("رقم الصف")
    raw_payload_encrypted = models.BinaryField("الصف الأصلي المشفر")
    encryption_key_version = models.CharField("إصدار المفتاح", max_length=30)
    raw_payload_sha256 = models.CharField("بصمة الصف", max_length=64)
    schema_version = models.CharField(
        "إصدار المخطط", max_length=30, default="weekly_summary_v1"
    )
    national_id_hash = models.CharField(
        "بصمة السجل المدني", max_length=64, null=True, blank=True
    )
    national_id_last4 = models.CharField("آخر أربعة أرقام", max_length=4, blank=True)
    normalized_payload_json = models.JSONField("البيانات المطبعة", default=dict)
    display_data_json = models.JSONField("بيانات العرض المنقحة", default=dict)
    matched_employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="attendance_import_rows",
        null=True,
        blank=True,
        verbose_name="الموظف المطابق",
    )
    attendance_date = models.DateField("تاريخ الحضور", null=True, blank=True)
    source_check_in = models.TimeField("وقت الحضور", null=True, blank=True)
    source_check_out = models.TimeField("وقت الانصراف", null=True, blank=True)
    source_check_in_location = models.CharField(
        "مكان الحضور", max_length=255, null=True, blank=True
    )
    source_check_out_location = models.CharField(
        "مكان الانصراف", max_length=255, null=True, blank=True
    )
    source_status = models.CharField("حالة المصدر", max_length=100, null=True, blank=True)
    source_scheduled_duration = models.DurationField("مدة الدوام المصدرية", null=True, blank=True)
    source_actual_work_duration = models.DurationField("مدة العمل المصدرية", null=True, blank=True)
    source_early_departure_duration = models.DurationField("الانصراف المبكر المصدري", null=True, blank=True)
    source_shortfall_duration = models.DurationField("نقص الدوام المصدري", null=True, blank=True)
    source_early_arrival_duration = models.DurationField("الحضور المبكر المصدري", null=True, blank=True)
    proposed_record_fingerprint = models.CharField(
        "بصمة السجل المقترحة", max_length=64, null=True, blank=True
    )
    match_status = models.CharField("حالة المطابقة", max_length=20, choices=MatchStatus.choices)
    validation_status = models.CharField(
        "حالة التحقق", max_length=20, choices=ValidationStatus.choices
    )
    location_match_status = models.CharField(
        "حالة الموقع",
        max_length=20,
        choices=LocationMatchStatus.choices,
        default=LocationMatchStatus.UNKNOWN,
    )
    is_duplicate = models.BooleanField("سجل مكرر", default=False)
    created_at = models.DateTimeField("وقت التسجيل", auto_now_add=True)

    class Meta:
        db_table = "import_rows"
        ordering = ("batch_id", "row_number")
        verbose_name = "صف استيراد حضور"
        verbose_name_plural = "صفوف استيراد الحضور"
        constraints = [
            models.UniqueConstraint(
                fields=("batch", "row_number"), name="att_import_row_number_uq"
            ),
            models.CheckConstraint(
                condition=Q(row_number__gt=0), name="att_import_row_number_ck"
            ),
        ]
        indexes = [
            models.Index(fields=("batch", "validation_status"), name="att_row_valid_idx"),
            models.Index(fields=("batch", "match_status"), name="att_row_match_idx"),
            models.Index(fields=("national_id_hash",), name="att_row_nid_idx"),
        ]


class ImportError(models.Model):
    class Severity(models.TextChoices):
        WARNING = "warning", "تحذير"
        ERROR = "error", "خطأ مانع"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.PROTECT,
        related_name="errors",
        db_index=False,
        verbose_name="دفعة الاستيراد",
    )
    row = models.ForeignKey(
        ImportRow,
        on_delete=models.PROTECT,
        related_name="errors",
        null=True,
        blank=True,
        verbose_name="صف الاستيراد",
    )
    error_code = models.CharField("رمز الخطأ", max_length=80)
    severity = models.CharField("الدرجة", max_length=20, choices=Severity.choices)
    field_name = models.CharField("الحقل", max_length=100, blank=True)
    message_ar = models.CharField("الرسالة", max_length=500)
    masked_value = models.CharField("القيمة المقنعة", max_length=255, blank=True)
    created_at = models.DateTimeField("وقت التسجيل", auto_now_add=True)

    class Meta:
        db_table = "import_errors"
        ordering = ("batch_id", "row_id", "created_at")
        verbose_name = "خطأ استيراد حضور"
        verbose_name_plural = "أخطاء استيراد الحضور"
        indexes = [
            models.Index(fields=("batch", "severity"), name="att_error_severity_idx"),
            models.Index(fields=("error_code",), name="att_error_code_idx"),
        ]


class RawAttendanceRecord(models.Model):
    class MatchStatus(models.TextChoices):
        MATCHED = "matched", "مطابق"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    import_row = models.OneToOneField(
        ImportRow,
        on_delete=models.PROTECT,
        related_name="raw_record",
        verbose_name="صف الاستيراد",
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="raw_attendance_records",
        verbose_name="الموظف",
    )
    national_id_hash = models.CharField("بصمة السجل المدني", max_length=64)
    attendance_date = models.DateField("تاريخ الحضور")
    source_check_in_at = models.DateTimeField("وقت الحضور المصدري", null=True, blank=True)
    source_check_out_at = models.DateTimeField("وقت الانصراف المصدري", null=True, blank=True)
    source_check_in_location = models.CharField("مكان الحضور", max_length=255, null=True, blank=True)
    source_check_out_location = models.CharField("مكان الانصراف", max_length=255, null=True, blank=True)
    primary_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="raw_attendance_primary_location_records",
        null=True,
        blank=True,
        verbose_name="الموقع الأساسي النافذ",
    )
    source_status = models.CharField("حالة المصدر", max_length=100, null=True, blank=True)
    source_scheduled_duration = models.DurationField("مدة الدوام المصدرية", null=True, blank=True)
    source_actual_work_duration = models.DurationField("مدة العمل المصدرية", null=True, blank=True)
    source_early_departure_duration = models.DurationField("الانصراف المبكر المصدري", null=True, blank=True)
    source_shortfall_duration = models.DurationField("نقص الدوام المصدري", null=True, blank=True)
    source_early_arrival_duration = models.DurationField("الحضور المبكر المصدري", null=True, blank=True)
    record_fingerprint = models.CharField("بصمة السجل", max_length=64, unique=True)
    match_status = models.CharField(
        "حالة المطابقة", max_length=20, choices=MatchStatus.choices, default=MatchStatus.MATCHED
    )
    location_match_status = models.CharField(
        "حالة الموقع", max_length=20, choices=ImportRow.LocationMatchStatus.choices
    )
    matched_at = models.DateTimeField("وقت المطابقة")
    created_at = models.DateTimeField("وقت الإنشاء", auto_now_add=True)

    class Meta:
        db_table = "raw_attendance_records"
        ordering = ("-attendance_date", "employee_id")
        verbose_name = "سجل حضور خام"
        verbose_name_plural = "سجلات الحضور الخام"
        indexes = [
            models.Index(fields=("employee", "attendance_date"), name="raw_att_emp_date_idx"),
            models.Index(fields=("national_id_hash",), name="raw_att_nid_idx"),
            models.Index(fields=("location_match_status", "attendance_date"), name="raw_att_loc_status_idx"),
        ]



class CalculationRun(models.Model):
    class RunType(models.TextChoices):
        INITIAL = "initial", "احتساب أولي"
        RECALCULATION = "recalculation", "إعادة احتساب"

    class Status(models.TextChoices):
        PENDING = "pending", "مجدول"
        RUNNING = "running", "قيد التنفيذ"
        COMPLETED = "completed", "مكتمل"
        FAILED = "failed", "فشل"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run_type = models.CharField(
        "نوع التشغيل", max_length=20, choices=RunType.choices, default=RunType.INITIAL
    )
    import_batch = models.ForeignKey(
        ImportBatch,
        verbose_name="دفعة الاستيراد",
        on_delete=models.PROTECT,
        related_name="calculation_runs",
        null=True,
        blank=True,
    )
    period_start = models.DateField("بداية الفترة")
    period_end = models.DateField("نهاية الفترة")
    status = models.CharField(
        "الحالة", max_length=20, choices=Status.choices, default=Status.PENDING
    )
    rules_version = models.CharField("إصدار القواعد", max_length=30, default="v1")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="طالب الاحتساب",
        on_delete=models.SET_NULL,
        related_name="attendance_calculation_runs",
        null=True,
        blank=True,
    )
    reason = models.TextField("سبب التشغيل", blank=True)
    result_count = models.PositiveIntegerField("عدد النتائج", default=0)
    started_at = models.DateTimeField("وقت البدء", null=True, blank=True)
    finished_at = models.DateTimeField("وقت الانتهاء", null=True, blank=True)
    failure_summary = models.TextField("ملخص الفشل", blank=True)
    created_at = models.DateTimeField("وقت الطلب", auto_now_add=True)

    class Meta:
        db_table = "calculation_runs"
        ordering = ("-created_at",)
        verbose_name = "تشغيل احتساب حضور"
        verbose_name_plural = "تشغيلات احتساب الحضور"
        constraints = [
            models.CheckConstraint(
                condition=Q(period_end__gte=F("period_start")),
                name="calc_run_period_ck",
            ),
        ]
        indexes = [
            models.Index(fields=("status", "created_at"), name="calc_run_status_idx"),
            models.Index(fields=("period_start", "period_end"), name="calc_run_period_idx"),
        ]

    def __str__(self):
        return f"احتساب {self.period_start} - {self.period_end}"


class DailyAttendanceResult(models.Model):
    class AttendanceStatus(models.TextChoices):
        PRESENT = "present", "حاضر"
        ABSENT = "absent", "غياب"
        EXCUSED = "excused", "مستثنى"
        INCOMPLETE = "incomplete", "بصمة ناقصة"
        UNKNOWN = "unknown", "غير محدد"

    class LocationStatus(models.TextChoices):
        MATCHED = "matched", "الموقعان مطابقان"
        CHECK_IN_OUTSIDE = "check_in_outside", "الحضور خارج الموقع"
        CHECK_OUT_OUTSIDE = "check_out_outside", "الانصراف خارج الموقع"
        BOTH_OUTSIDE = "both_outside", "الحضور والانصراف خارج الموقع"
        UNKNOWN = "unknown", "موقع غير معروف"
        NOT_REQUIRED = "not_required", "لا يوجد موقع معتمد"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_record = models.ForeignKey(
        RawAttendanceRecord,
        verbose_name="السجل الخام",
        on_delete=models.PROTECT,
        related_name="daily_results",
    )
    employee = models.ForeignKey(
        Employee,
        verbose_name="الموظف",
        on_delete=models.PROTECT,
        related_name="daily_attendance_results",
    )
    calculation_run = models.ForeignKey(
        CalculationRun,
        verbose_name="تشغيل الاحتساب",
        on_delete=models.PROTECT,
        related_name="results",
    )
    attendance_date = models.DateField("التاريخ")
    version = models.PositiveIntegerField("الإصدار", default=1)
    is_current = models.BooleanField("الإصدار الحالي", default=True)
    department = models.ForeignKey(
        "organization.Department",
        verbose_name="القسم وقت الحضور",
        on_delete=models.PROTECT,
        related_name="daily_attendance_results",
        null=True,
        blank=True,
    )
    primary_location = models.ForeignKey(
        Location,
        verbose_name="الموقع المعتمد",
        on_delete=models.PROTECT,
        related_name="daily_attendance_results",
        null=True,
        blank=True,
    )
    first_check_in_at = models.DateTimeField("وقت الحضور", null=True, blank=True)
    last_check_out_at = models.DateTimeField("وقت الانصراف", null=True, blank=True)
    check_in_location = models.CharField("مكان الحضور", max_length=255, blank=True)
    check_out_location = models.CharField("مكان الانصراف", max_length=255, blank=True)
    scheduled_minutes = models.PositiveIntegerField("دقائق الدوام المجدولة", default=0)
    worked_minutes = models.PositiveIntegerField("دقائق العمل الفعلية", default=0)
    late_minutes = models.PositiveIntegerField("دقائق التأخر", default=0)
    early_leave_minutes = models.PositiveIntegerField("دقائق الانصراف المبكر", default=0)
    shortfall_minutes = models.PositiveIntegerField("دقائق النقص", default=0)
    early_arrival_minutes = models.PositiveIntegerField("دقائق الحضور المبكر", default=0)
    overtime_minutes = models.PositiveIntegerField("دقائق العمل الإضافي", default=0)
    attendance_status = models.CharField(
        "حالة اليوم", max_length=20, choices=AttendanceStatus.choices
    )
    check_in_location_matches = models.BooleanField(
        "مكان الحضور مطابق", null=True, blank=True
    )
    check_out_location_matches = models.BooleanField(
        "مكان الانصراف مطابق", null=True, blank=True
    )
    location_status = models.CharField(
        "حالة الموقع", max_length=30, choices=LocationStatus.choices
    )
    source_status = models.CharField("حالة المصدر", max_length=100, blank=True)
    calculation_notes = models.JSONField("تفاصيل الاحتساب", default=dict)
    superseded_at = models.DateTimeField("وقت الاستبدال", null=True, blank=True)
    created_at = models.DateTimeField("وقت الإنشاء", auto_now_add=True)

    class Meta:
        db_table = "daily_attendance_results"
        ordering = ("-attendance_date", "employee__full_name_ar")
        verbose_name = "نتيجة حضور يومية"
        verbose_name_plural = "نتائج الحضور اليومية"
        constraints = [
            models.UniqueConstraint(
                fields=("employee", "attendance_date", "version"),
                name="daily_result_version_uq",
            ),
            models.UniqueConstraint(
                fields=("employee", "attendance_date"),
                condition=Q(is_current=True),
                name="daily_result_current_uq",
            ),
            models.CheckConstraint(condition=Q(version__gt=0), name="daily_result_version_ck"),
        ]
        indexes = [
            models.Index(
                fields=("employee", "attendance_date"), name="daily_result_emp_date_idx"
            ),
            models.Index(
                fields=("department", "attendance_date", "is_current"),
                name="daily_result_dept_date_idx",
            ),
            models.Index(
                fields=("attendance_status", "attendance_date"),
                name="daily_result_status_idx",
            ),
            models.Index(
                fields=("location_status", "attendance_date"),
                name="daily_result_location_idx",
            ),
        ]

    def __str__(self):
        return f"{self.employee} - {self.attendance_date}"
