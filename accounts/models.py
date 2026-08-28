import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class UserManager(BaseUserManager):
    """Create users through the project's username-based identity model."""

    use_in_migrations = True

    def _create_user(self, username, password, **extra_fields):
        if not username:
            raise ValueError("اسم المستخدم مطلوب.")
        if not password:
            raise ValueError("كلمة المرور مطلوبة.")

        username = self.model.normalize_username(username).strip()
        if not username:
            raise ValueError("اسم المستخدم مطلوب.")

        extra_fields["email"] = self.normalize_email(extra_fields.get("email", ""))
        user = self.model(username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, username, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(username, password, **extra_fields)

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("يجب أن يكون مدير النظام ضمن طاقم الإدارة.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("يجب منح مدير النظام صلاحية superuser.")

        return self._create_user(username, password, **extra_fields)

    def get_by_natural_key(self, username):
        return self.get(**{f"{self.model.USERNAME_FIELD}__iexact": username})


class User(AbstractBaseUser):
    """Authentication identity kept separate from the future employee model."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField("اسم المستخدم", max_length=150, unique=True)
    email = models.EmailField("البريد الإلكتروني", blank=True)
    first_name = models.CharField("الاسم الأول", max_length=150, blank=True)
    last_name = models.CharField("اسم العائلة", max_length=150, blank=True)
    is_active = models.BooleanField("نشط", default=True)
    is_staff = models.BooleanField("دخول الإدارة", default=False)
    is_superuser = models.BooleanField("مدير نظام", default=False)
    must_change_password = models.BooleanField("يلزم تغيير كلمة المرور", default=False)
    failed_login_count = models.PositiveSmallIntegerField("محاولات الدخول الفاشلة", default=0)
    locked_until = models.DateTimeField("القفل حتى", null=True, blank=True)
    password_changed_at = models.DateTimeField("آخر تغيير لكلمة المرور", null=True, blank=True)
    locale = models.CharField("اللغة", max_length=10, default="ar")
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("تاريخ التحديث", auto_now=True)
    created_by = models.ForeignKey(
        "self",
        verbose_name="أنشأه",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_users",
    )
    updated_by = models.ForeignKey(
        "self",
        verbose_name="حدّثه",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_users",
    )
    archived_at = models.DateTimeField("تاريخ الأرشفة", null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "users"
        ordering = ("username",)
        verbose_name = "مستخدم"
        verbose_name_plural = "المستخدمون"
        constraints = [
            models.UniqueConstraint(
                Lower("username"),
                name="acct_user_username_ci_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(locale="ar"),
                name="acct_user_locale_ar_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=("is_active", "archived_at"),
                name="acct_user_active_arch_ix",
            ),
            models.Index(
                Lower("email"),
                name="acct_user_email_lower_ix",
            ),
        ]

    def __str__(self):
        return self.username

    def get_full_name(self):
        return " ".join(part for part in (self.first_name, self.last_name) if part).strip()

    def get_short_name(self):
        return self.first_name or self.username

    def get_user_permissions(self, obj=None):
        return set()

    def get_group_permissions(self, obj=None):
        return set()

    def get_all_permissions(self, obj=None):
        return set()

    def has_perm(self, perm, obj=None):
        # Full project role evaluation is intentionally deferred beyond batch one.
        return bool(self.is_active and self.is_superuser)

    def has_module_perms(self, app_label):
        return bool(self.is_active and self.is_superuser)


class Role(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField("الرمز", max_length=80, unique=True)
    name_ar = models.CharField("الاسم العربي", max_length=150)
    description_ar = models.TextField("الوصف", blank=True)
    is_system = models.BooleanField("دور نظام", default=False)
    is_active = models.BooleanField("نشط", default=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("تاريخ التحديث", auto_now=True)
    created_by = models.ForeignKey(
        User,
        verbose_name="أنشأه",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_roles",
    )
    updated_by = models.ForeignKey(
        User,
        verbose_name="حدّثه",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_roles",
    )

    class Meta:
        db_table = "roles"
        ordering = ("name_ar",)
        verbose_name = "دور"
        verbose_name_plural = "الأدوار"

    def __str__(self):
        return self.name_ar


class Permission(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField("الرمز", max_length=120, unique=True)
    module = models.CharField("الوحدة", max_length=50)
    action = models.CharField("الإجراء", max_length=50)
    name_ar = models.CharField("الاسم العربي", max_length=150)
    description_ar = models.TextField("الوصف", blank=True)
    is_active = models.BooleanField("نشطة", default=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("تاريخ التحديث", auto_now=True)

    class Meta:
        db_table = "permissions"
        ordering = ("module", "action")
        verbose_name = "صلاحية"
        verbose_name_plural = "الصلاحيات"
        constraints = [
            models.UniqueConstraint(
                fields=("module", "action"),
                name="acct_perm_module_action_uq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("module", "is_active"),
                name="acct_perm_module_active_ix",
            ),
        ]

    def __str__(self):
        return self.name_ar


class RolePermission(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.ForeignKey(
        Role,
        verbose_name="الدور",
        on_delete=models.PROTECT,
        related_name="permission_assignments",
        db_index=False,
    )
    permission = models.ForeignKey(
        Permission,
        verbose_name="الصلاحية",
        on_delete=models.PROTECT,
        related_name="role_assignments",
        db_index=False,
    )
    granted_at = models.DateTimeField("تاريخ المنح", default=timezone.now)
    granted_by = models.ForeignKey(
        User,
        verbose_name="منحها",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="granted_role_permissions",
    )
    revoked_at = models.DateTimeField("تاريخ الإلغاء", null=True, blank=True)
    revoked_by = models.ForeignKey(
        User,
        verbose_name="ألغاها",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="revoked_role_permissions",
    )

    class Meta:
        db_table = "role_permissions"
        ordering = ("role__name_ar", "permission__module", "permission__action")
        verbose_name = "صلاحية دور"
        verbose_name_plural = "صلاحيات الأدوار"
        constraints = [
            models.UniqueConstraint(
                fields=("role", "permission"),
                condition=models.Q(revoked_at__isnull=True),
                name="acct_rp_active_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(revoked_by__isnull=True)
                | models.Q(revoked_at__isnull=False),
                name="acct_rp_revoke_data_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(revoked_at__isnull=True)
                | models.Q(revoked_at__gte=models.F("granted_at")),
                name="acct_rp_revoke_time_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=("role", "revoked_at"),
                name="acct_rp_role_rev_ix",
            ),
            models.Index(
                fields=("permission", "revoked_at"),
                name="acct_rp_perm_rev_ix",
            ),
        ]

    def __str__(self):
        return f"{self.role} - {self.permission}"


class UserRole(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        verbose_name="المستخدم",
        on_delete=models.PROTECT,
        related_name="role_assignments",
        db_index=False,
    )
    role = models.ForeignKey(
        Role,
        verbose_name="الدور",
        on_delete=models.PROTECT,
        related_name="user_assignments",
    )
    valid_from = models.DateTimeField("ساري من", default=timezone.now)
    valid_to = models.DateTimeField("ساري إلى", null=True, blank=True)
    is_active = models.BooleanField("نشط", default=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        verbose_name="أنشأه",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_user_roles",
    )

    class Meta:
        db_table = "user_roles"
        ordering = ("user__username", "role__name_ar", "-valid_from")
        verbose_name = "دور مستخدم"
        verbose_name_plural = "أدوار المستخدمين"
        constraints = [
            models.UniqueConstraint(
                fields=("user", "role", "valid_from"),
                name="acct_ur_user_role_from_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(valid_to__isnull=True)
                | models.Q(valid_to__gt=models.F("valid_from")),
                name="acct_ur_dates_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=("user", "is_active", "valid_from", "valid_to"),
                name="acct_ur_user_dates_ix",
            ),
        ]

    def __str__(self):
        return f"{self.user} - {self.role}"
