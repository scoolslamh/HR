import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q


class Department(models.Model):
    class UnitType(models.TextChoices):
        SECTOR = "sector", "قطاع"
        DIRECTORATE = "directorate", "إدارة"
        DEPARTMENT = "department", "قسم"
        UNIT = "unit", "وحدة"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField("الرمز", max_length=50, unique=True)
    name_ar = models.CharField("الاسم العربي", max_length=200)
    unit_type = models.CharField(
        "نوع الوحدة",
        max_length=20,
        choices=UnitType.choices,
    )
    parent = models.ForeignKey(
        "self",
        verbose_name="الوحدة الأم",
        on_delete=models.PROTECT,
        related_name="children",
        null=True,
        blank=True,
    )
    signing_location = models.ForeignKey(
        "Location",
        verbose_name="مكان التوقيع",
        on_delete=models.PROTECT,
        related_name="signing_departments",
        null=True,
        blank=True,
    )
    department_head = models.ForeignKey(
        "Employee",
        verbose_name="رئيس القسم",
        on_delete=models.PROTECT,
        related_name="headed_departments",
        null=True,
        blank=True,
    )
    path_cache = models.CharField(
        "المسار المخبأ",
        max_length=1000,
        null=True,
        blank=True,
        editable=False,
    )
    level = models.PositiveSmallIntegerField("المستوى", default=0)
    is_active = models.BooleanField("نشط", default=True)
    valid_from = models.DateField("صالح من")
    valid_to = models.DateField("صالح حتى", null=True, blank=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("تاريخ التحديث", auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="أنشأه",
        on_delete=models.SET_NULL,
        related_name="departments_created",
        null=True,
        blank=True,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="آخر من عدله",
        on_delete=models.SET_NULL,
        related_name="departments_updated",
        null=True,
        blank=True,
    )
    archived_at = models.DateTimeField("تاريخ الأرشفة", null=True, blank=True)

    class Meta:
        db_table = "departments"
        verbose_name = "وحدة تنظيمية"
        verbose_name_plural = "الوحدات التنظيمية"
        ordering = ("code",)
        constraints = [
            models.CheckConstraint(
                condition=~Q(parent=F("id")),
                name="dept_no_self_parent_ck",
            ),
            models.CheckConstraint(
                condition=Q(valid_to__isnull=True) | Q(valid_to__gt=F("valid_from")),
                name="dept_valid_period_ck",
            ),
        ]
        indexes = [
            # The ForeignKey creates the required parent_id index automatically.
            models.Index(
                fields=("is_active", "archived_at"),
                name="dept_active_archive_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} - {self.name_ar}"


class UserDepartmentScope(models.Model):
    class AccessLevel(models.TextChoices):
        VIEW = "view", "عرض"
        MANAGE = "manage", "إدارة"
        APPROVE = "approve", "اعتماد"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="المستخدم",
        on_delete=models.PROTECT,
        related_name="department_scopes",
        db_index=False,
    )
    department = models.ForeignKey(
        Department,
        verbose_name="الوحدة التنظيمية",
        on_delete=models.PROTECT,
        related_name="user_scopes",
        db_index=False,
    )
    role = models.ForeignKey(
        "accounts.Role",
        verbose_name="الدور",
        on_delete=models.PROTECT,
        related_name="department_scopes",
        null=True,
        blank=True,
    )
    include_descendants = models.BooleanField(
        "يشمل الوحدات الفرعية",
        default=False,
    )
    access_level = models.CharField(
        "مستوى الوصول",
        max_length=20,
        choices=AccessLevel.choices,
    )
    valid_from = models.DateTimeField("صالح من")
    valid_to = models.DateTimeField("صالح حتى", null=True, blank=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="أنشأه",
        on_delete=models.SET_NULL,
        related_name="department_scopes_created",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "user_department_scopes"
        verbose_name = "نطاق وحدة تنظيمية"
        verbose_name_plural = "نطاقات الوحدات التنظيمية"
        ordering = ("user_id", "department_id", "valid_from")
        constraints = [
            models.UniqueConstraint(
                fields=("user", "department", "access_level", "valid_from"),
                condition=Q(role__isnull=True),
                name="scope_uniq_without_role",
            ),
            models.UniqueConstraint(
                fields=(
                    "user",
                    "department",
                    "role",
                    "access_level",
                    "valid_from",
                ),
                condition=Q(role__isnull=False),
                name="scope_uniq_with_role",
            ),
            models.CheckConstraint(
                condition=Q(valid_to__isnull=True) | Q(valid_to__gt=F("valid_from")),
                name="scope_valid_period_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=("user", "valid_from", "valid_to"),
                name="scope_user_valid_idx",
            ),
            models.Index(
                fields=("department", "valid_from", "valid_to"),
                name="scope_dept_valid_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} - {self.department} ({self.get_access_level_display()})"


class Location(models.Model):
    class LocationType(models.TextChoices):
        HEADQUARTERS = "headquarters", "مقر"
        BRANCH = "branch", "فرع"
        FIELD = "field", "موقع ميداني"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField("الرمز", max_length=50, unique=True)
    name_ar = models.CharField("الاسم العربي", max_length=200)
    location_type = models.CharField(
        "نوع الموقع",
        max_length=20,
        choices=LocationType.choices,
    )
    department = models.ForeignKey(
        Department,
        verbose_name="الجهة المالكة",
        on_delete=models.PROTECT,
        related_name="locations",
        null=True,
        blank=True,
        db_index=False,
    )
    address_ar = models.TextField("العنوان", blank=True)
    latitude = models.DecimalField(
        "خط العرض",
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )
    longitude = models.DecimalField(
        "خط الطول",
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )
    timezone = models.CharField(
        "المنطقة الزمنية",
        max_length=50,
        default="Asia/Riyadh",
    )
    is_active = models.BooleanField("نشط", default=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("تاريخ التحديث", auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="أنشأه",
        on_delete=models.SET_NULL,
        related_name="locations_created",
        null=True,
        blank=True,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="آخر من عدله",
        on_delete=models.SET_NULL,
        related_name="locations_updated",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "locations"
        verbose_name = "موقع"
        verbose_name_plural = "المواقع"
        ordering = ("code",)
        constraints = [
            models.CheckConstraint(
                condition=Q(latitude__isnull=True)
                | Q(latitude__gte=-90, latitude__lte=90),
                name="loc_latitude_range_ck",
            ),
            models.CheckConstraint(
                condition=Q(longitude__isnull=True)
                | Q(longitude__gte=-180, longitude__lte=180),
                name="loc_longitude_range_ck",
            ),
            models.CheckConstraint(
                condition=Q(timezone="Asia/Riyadh"),
                name="loc_timezone_riyadh_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=("department", "is_active"),
                name="loc_dept_active_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} - {self.name_ar}"


class JobTitle(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField("الرمز", max_length=50, unique=True)
    name_ar = models.CharField("المسمى العربي", max_length=200)
    is_active = models.BooleanField("نشط", default=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("تاريخ التحديث", auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="أنشأه",
        on_delete=models.SET_NULL,
        related_name="job_titles_created",
        null=True,
        blank=True,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="آخر من عدله",
        on_delete=models.SET_NULL,
        related_name="job_titles_updated",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "job_titles"
        verbose_name = "مسمى وظيفي"
        verbose_name_plural = "المسميات الوظيفية"
        ordering = ("name_ar",)

    def __str__(self) -> str:
        return f"{self.code} - {self.name_ar}"


class Employee(models.Model):
    class EmploymentStatus(models.TextChoices):
        ACTIVE = "active", "نشط"
        SUSPENDED = "suspended", "موقوف"
        TERMINATED = "terminated", "منتهي الخدمة"
        ARCHIVED = "archived", "مؤرشف"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee_number = models.CharField(
        "الرقم الوظيفي",
        max_length=50,
        null=True,
        blank=True,
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name="حساب المستخدم",
        on_delete=models.SET_NULL,
        related_name="employee",
        null=True,
        blank=True,
    )
    full_name_ar = models.CharField("الاسم العربي", max_length=250)
    preferred_name_ar = models.CharField(
        "اسم العرض",
        max_length=150,
        blank=True,
    )
    work_email = models.EmailField("البريد الوظيفي", blank=True)
    mobile_masked = models.CharField("رقم الجوال المقنع", max_length=30, blank=True)
    employment_status = models.CharField(
        "الحالة الوظيفية",
        max_length=20,
        choices=EmploymentStatus.choices,
        default=EmploymentStatus.ACTIVE,
    )
    hire_date = models.DateField("تاريخ التعيين", null=True, blank=True)
    termination_date = models.DateField(
        "تاريخ انتهاء الخدمة",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("تاريخ التحديث", auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="أنشأه",
        on_delete=models.SET_NULL,
        related_name="employees_created",
        null=True,
        blank=True,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="آخر من عدله",
        on_delete=models.SET_NULL,
        related_name="employees_updated",
        null=True,
        blank=True,
    )
    archived_at = models.DateTimeField("تاريخ الأرشفة", null=True, blank=True)

    class Meta:
        db_table = "employees"
        verbose_name = "موظف"
        verbose_name_plural = "الموظفون"
        ordering = ("full_name_ar",)
        constraints = [
            models.UniqueConstraint(
                fields=("employee_number",),
                condition=Q(employee_number__isnull=False),
                name="emp_number_present_uq",
            ),
            models.CheckConstraint(
                condition=Q(employee_number__isnull=True)
                | ~Q(employee_number=""),
                name="emp_number_not_empty_ck",
            ),
            models.CheckConstraint(
                condition=Q(termination_date__isnull=True)
                | Q(hire_date__isnull=True)
                | Q(termination_date__gte=F("hire_date")),
                name="emp_employment_dates_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=("employment_status", "archived_at"),
                name="emp_status_archive_idx",
            ),
            models.Index(fields=("full_name_ar",), name="emp_full_name_idx"),
        ]

    def __str__(self) -> str:
        return self.full_name_ar


class EmployeeIdentity(models.Model):
    class IdentityType(models.TextChoices):
        NATIONAL_ID = "national_id", "السجل المدني"

    class VerificationSource(models.TextChoices):
        MANUAL = "manual", "إدخال يدوي"
        IMPORT = "import", "استيراد"
        INTEGRATION = "integration", "تكامل"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.OneToOneField(
        Employee,
        verbose_name="الموظف",
        on_delete=models.PROTECT,
        related_name="identity",
    )
    identity_type = models.CharField(
        "نوع الهوية",
        max_length=20,
        choices=IdentityType.choices,
        default=IdentityType.NATIONAL_ID,
    )
    national_id_hash = models.CharField(
        "بصمة السجل المدني",
        max_length=64,
        unique=True,
    )
    national_id_encrypted = models.BinaryField("السجل المدني المشفر")
    encryption_key_version = models.CharField("إصدار مفتاح التشفير", max_length=30)
    national_id_last4 = models.CharField("آخر أربعة أرقام", max_length=4)
    normalized_length = models.PositiveSmallIntegerField(
        "طول القيمة بعد التطبيع",
        default=10,
    )
    verified_at = models.DateTimeField("تاريخ التحقق", null=True, blank=True)
    verification_source = models.CharField(
        "مصدر التحقق",
        max_length=50,
        choices=VerificationSource.choices,
        blank=True,
    )
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("تاريخ التحديث", auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="أنشأه",
        on_delete=models.SET_NULL,
        related_name="employee_identities_created",
        null=True,
        blank=True,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="آخر من عدله",
        on_delete=models.SET_NULL,
        related_name="employee_identities_updated",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "employee_identities"
        verbose_name = "هوية موظف"
        verbose_name_plural = "هويات الموظفين"
        constraints = [
            models.CheckConstraint(
                condition=Q(identity_type="national_id"),
                name="emp_identity_type_ck",
            ),
            models.CheckConstraint(
                condition=Q(national_id_last4__regex=r"^[0-9]{4}$"),
                name="emp_identity_last4_ck",
            ),
            models.CheckConstraint(
                condition=Q(normalized_length=10),
                name="emp_identity_length_ck",
            ),
        ]

    def __str__(self) -> str:
        return f"هوية {self.employee}"


class EmploymentAssignment(models.Model):
    class AssignmentType(models.TextChoices):
        PRIMARY = "primary", "أساسي"
        ACTING = "acting", "تكليف"
        SECONDMENT = "secondment", "ندب"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(
        Employee,
        verbose_name="الموظف",
        on_delete=models.PROTECT,
        related_name="employment_assignments",
        db_index=False,
    )
    department = models.ForeignKey(
        Department,
        verbose_name="الوحدة التنظيمية",
        on_delete=models.PROTECT,
        related_name="employment_assignments",
        db_index=False,
    )
    job_title = models.ForeignKey(
        JobTitle,
        verbose_name="المسمى الوظيفي",
        on_delete=models.PROTECT,
        related_name="employment_assignments",
        null=True,
        blank=True,
    )
    manager_employee = models.ForeignKey(
        Employee,
        verbose_name="المدير المباشر",
        on_delete=models.PROTECT,
        related_name="managed_employment_assignments",
        null=True,
        blank=True,
    )
    assignment_type = models.CharField(
        "نوع الإسناد",
        max_length=20,
        choices=AssignmentType.choices,
        default=AssignmentType.PRIMARY,
    )
    valid_from = models.DateField("صالح من")
    valid_to = models.DateField("صالح حتى", null=True, blank=True)
    is_primary = models.BooleanField("إسناد أساسي", default=True)
    reason = models.TextField("السبب", blank=True)
    reference_number = models.CharField(
        "رقم المرجع",
        max_length=100,
        blank=True,
    )
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("تاريخ التحديث", auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="أنشأه",
        on_delete=models.SET_NULL,
        related_name="employment_assignments_created",
        null=True,
        blank=True,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="آخر من عدله",
        on_delete=models.SET_NULL,
        related_name="employment_assignments_updated",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "employment_assignments"
        verbose_name = "إسناد وظيفي"
        verbose_name_plural = "الإسنادات الوظيفية"
        ordering = ("employee_id", "-valid_from")
        constraints = [
            models.UniqueConstraint(
                fields=(
                    "employee",
                    "valid_from",
                    "assignment_type",
                    "department",
                ),
                name="emp_assign_natural_uq",
            ),
            models.CheckConstraint(
                condition=Q(valid_to__isnull=True) | Q(valid_to__gt=F("valid_from")),
                name="emp_assign_period_ck",
            ),
            models.CheckConstraint(
                condition=Q(manager_employee__isnull=True)
                | ~Q(manager_employee=F("employee")),
                name="emp_assign_not_self_mgr_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=("employee", "valid_from", "valid_to"),
                name="emp_assign_employee_idx",
            ),
            models.Index(
                fields=("department", "valid_from", "valid_to"),
                name="emp_assign_dept_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee} - {self.department}"


class EmployeePrimaryLocation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(
        Employee,
        verbose_name="الموظف",
        on_delete=models.PROTECT,
        related_name="primary_location_assignments",
        db_index=False,
    )
    location = models.ForeignKey(
        Location,
        verbose_name="الموقع الأساسي",
        on_delete=models.PROTECT,
        related_name="employee_assignments",
        db_index=False,
    )
    valid_from = models.DateField("صالح من")
    valid_to = models.DateField("صالح حتى", null=True, blank=True)
    assignment_reason = models.TextField("سبب الإسناد", blank=True)
    reference_number = models.CharField(
        "رقم المرجع",
        max_length=100,
        blank=True,
    )
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("تاريخ التحديث", auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="أنشأه",
        on_delete=models.SET_NULL,
        related_name="employee_locations_created",
        null=True,
        blank=True,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="آخر من عدله",
        on_delete=models.SET_NULL,
        related_name="employee_locations_updated",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "employee_primary_locations"
        verbose_name = "موقع موظف أساسي"
        verbose_name_plural = "مواقع الموظفين الأساسية"
        ordering = ("employee_id", "-valid_from")
        constraints = [
            models.UniqueConstraint(
                fields=("employee", "location", "valid_from"),
                name="emp_location_natural_uq",
            ),
            models.CheckConstraint(
                condition=Q(valid_to__isnull=True) | Q(valid_to__gt=F("valid_from")),
                name="emp_location_period_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=("employee", "valid_from", "valid_to"),
                name="emp_location_employee_idx",
            ),
            models.Index(
                fields=("location", "valid_from", "valid_to"),
                name="emp_location_location_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee} - {self.location}"


class EmployeeImportBatch(models.Model):
    class Status(models.TextChoices):
        UPLOADED = "uploaded", "مرفوع"
        PREVIEW_READY = "preview_ready", "جاهز للمعاينة"
        HAS_ERRORS = "has_errors", "به أخطاء"
        APPROVED = "approved", "معتمد"
        FAILED = "failed", "فشل"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    original_filename = models.CharField("اسم الملف الأصلي", max_length=255)
    storage_key = models.CharField("مفتاح التخزين", max_length=500, unique=True)
    file_sha256 = models.CharField("بصمة الملف", max_length=64, unique=True)
    file_size_bytes = models.PositiveBigIntegerField("حجم الملف بالبايت")
    mime_type = models.CharField("نوع الملف", max_length=100)
    encryption_key_version = models.CharField("إصدار مفتاح التشفير", max_length=30)
    status = models.CharField(
        "الحالة",
        max_length=20,
        choices=Status.choices,
        default=Status.UPLOADED,
    )
    total_rows = models.PositiveIntegerField("إجمالي الصفوف", default=0)
    new_rows = models.PositiveIntegerField("الموظفون الجدد", default=0)
    update_rows = models.PositiveIntegerField("الموظفون المحدثون", default=0)
    missing_department_rows = models.PositiveIntegerField(
        "الأقسام غير الموجودة",
        default=0,
    )
    missing_location_rows = models.PositiveIntegerField(
        "المواقع غير الموجودة",
        default=0,
    )
    unmatched_manager_rows = models.PositiveIntegerField(
        "المديرون غير المطابقين",
        default=0,
    )
    error_rows = models.PositiveIntegerField("الأخطاء المانعة", default=0)
    warning_rows = models.PositiveIntegerField("التحذيرات", default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="رافع الملف",
        on_delete=models.SET_NULL,
        related_name="employee_imports_uploaded",
        null=True,
        blank=True,
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="معتمد الملف",
        on_delete=models.SET_NULL,
        related_name="employee_imports_approved",
        null=True,
        blank=True,
    )
    approved_at = models.DateTimeField("تاريخ الاعتماد", null=True, blank=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)

    class Meta:
        db_table = "employee_import_batches"
        verbose_name = "دفعة استيراد موظفين"
        verbose_name_plural = "دفعات استيراد الموظفين"
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=Q(file_size_bytes__gt=0),
                name="emp_import_batch_size_ck",
            ),
            models.CheckConstraint(
                condition=(Q(status="approved") & Q(approved_at__isnull=False))
                | (~Q(status="approved") & Q(approved_at__isnull=True)),
                name="emp_import_approval_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=("status", "created_at"),
                name="emp_import_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"دفعة {self.id} - {self.get_status_display()}"


class EmployeeImportRow(models.Model):
    class ImportAction(models.TextChoices):
        CREATE = "create", "إنشاء"
        UPDATE = "update", "تحديث"
        SKIP = "skip", "تخطي"

    class ValidationStatus(models.TextChoices):
        VALID = "valid", "صالح"
        WARNING = "warning", "تحذير"
        ERROR = "error", "خطأ"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(
        EmployeeImportBatch,
        verbose_name="دفعة الاستيراد",
        on_delete=models.PROTECT,
        related_name="rows",
        db_index=False,
    )
    row_number = models.PositiveIntegerField("رقم الصف")
    raw_payload_encrypted = models.BinaryField("الحمولة الأصلية المشفرة")
    encryption_key_version = models.CharField("إصدار مفتاح التشفير", max_length=30)
    payload_sha256 = models.CharField("بصمة الحمولة", max_length=64)
    national_id_hash = models.CharField(
        "بصمة السجل المدني",
        max_length=64,
        null=True,
        blank=True,
    )
    national_id_last4 = models.CharField(
        "آخر أربعة أرقام",
        max_length=4,
        blank=True,
    )
    display_data_json = models.JSONField("بيانات العرض المنقحة", default=dict)
    import_action = models.CharField(
        "إجراء الاستيراد",
        max_length=20,
        choices=ImportAction.choices,
        default=ImportAction.SKIP,
    )
    validation_status = models.CharField(
        "حالة التحقق",
        max_length=20,
        choices=ValidationStatus.choices,
        default=ValidationStatus.VALID,
    )
    matched_employee = models.ForeignKey(
        Employee,
        verbose_name="الموظف المطابق",
        on_delete=models.PROTECT,
        related_name="import_rows",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField("تاريخ التسجيل", auto_now_add=True)

    class Meta:
        db_table = "employee_import_rows"
        verbose_name = "صف استيراد موظف"
        verbose_name_plural = "صفوف استيراد الموظفين"
        ordering = ("batch_id", "row_number")
        constraints = [
            models.UniqueConstraint(
                fields=("batch", "row_number"),
                name="emp_import_row_number_uq",
            ),
            models.CheckConstraint(
                condition=Q(row_number__gt=0),
                name="emp_import_row_number_ck",
            ),
            models.CheckConstraint(
                condition=Q(national_id_last4="")
                | Q(national_id_last4__regex=r"^[0-9]{4}$"),
                name="emp_import_row_last4_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=("batch", "validation_status"),
                name="emp_import_row_status_idx",
            ),
            models.Index(
                fields=("national_id_hash",),
                name="emp_import_row_nid_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.batch_id} - صف {self.row_number}"


class EmployeeImportError(models.Model):
    class Severity(models.TextChoices):
        WARNING = "warning", "تحذير"
        ERROR = "error", "خطأ مانع"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(
        EmployeeImportBatch,
        verbose_name="دفعة الاستيراد",
        on_delete=models.PROTECT,
        related_name="errors",
        db_index=False,
    )
    row = models.ForeignKey(
        EmployeeImportRow,
        verbose_name="صف الاستيراد",
        on_delete=models.PROTECT,
        related_name="errors",
        null=True,
        blank=True,
    )
    error_code = models.CharField("رمز الخطأ", max_length=80)
    severity = models.CharField(
        "درجة الخطأ",
        max_length=20,
        choices=Severity.choices,
    )
    field_name = models.CharField("اسم الحقل", max_length=100, blank=True)
    message_ar = models.CharField("رسالة الخطأ", max_length=500)
    masked_value = models.CharField("القيمة المقنعة", max_length=255, blank=True)
    created_at = models.DateTimeField("تاريخ التسجيل", auto_now_add=True)

    class Meta:
        db_table = "employee_import_errors"
        verbose_name = "خطأ استيراد موظف"
        verbose_name_plural = "أخطاء استيراد الموظفين"
        ordering = ("batch_id", "row_id", "created_at")
        indexes = [
            models.Index(
                fields=("batch", "severity"),
                name="emp_import_error_sev_idx",
            ),
            models.Index(fields=("error_code",), name="emp_import_error_code_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.error_code}: {self.message_ar}"
