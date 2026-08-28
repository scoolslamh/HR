import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class AuditLogImmutableError(RuntimeError):
    """Raised when application code attempts to mutate an audit event."""


class AuditLogQuerySet(models.QuerySet):
    """Application-facing queryset that preserves the append-only contract."""

    _IMMUTABLE_MESSAGE = "سجل التدقيق غير قابل للتعديل أو الحذف."

    def update(self, **kwargs):
        raise AuditLogImmutableError(self._IMMUTABLE_MESSAGE)

    def delete(self):
        raise AuditLogImmutableError(self._IMMUTABLE_MESSAGE)

    def bulk_update(self, objs, fields, batch_size=None):
        raise AuditLogImmutableError(self._IMMUTABLE_MESSAGE)

    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        if update_conflicts:
            raise AuditLogImmutableError(self._IMMUTABLE_MESSAGE)
        return super().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )


class AuditLog(models.Model):
    """An immutable application audit event containing masked data only."""

    class Outcome(models.TextChoices):
        SUCCESS = "success", "نجاح"
        FAILURE = "failure", "فشل"
        PERMISSION_DENIED = "permission_denied", "رفض صلاحية"

    id = models.UUIDField(
        "المعرّف",
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    occurred_at = models.DateTimeField(
        "وقت الحدث",
        default=timezone.now,
        editable=False,
    )
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="المستخدم المنفذ",
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        null=True,
        blank=True,
        db_index=False,
    )
    actor_username_snapshot = models.CharField(
        "لقطة اسم المستخدم",
        max_length=150,
        null=True,
        blank=True,
    )
    action = models.CharField("الإجراء", max_length=100)
    module = models.CharField("الوحدة", max_length=50)
    object_type = models.CharField(
        "نوع الكيان",
        max_length=100,
        null=True,
        blank=True,
    )
    object_id = models.UUIDField("معرّف الكيان", null=True, blank=True)
    object_repr_masked = models.CharField(
        "وصف الكيان المقنّع",
        max_length=255,
        null=True,
        blank=True,
    )
    department_scope = models.ForeignKey(
        "organization.Department",
        verbose_name="نطاق القسم",
        on_delete=models.PROTECT,
        related_name="audit_logs",
        null=True,
        blank=True,
        db_index=False,
    )
    before_json = models.JSONField("القيم السابقة", null=True, blank=True)
    after_json = models.JSONField("القيم اللاحقة", null=True, blank=True)
    reason = models.TextField("السبب", null=True, blank=True)
    outcome = models.CharField("النتيجة", max_length=20, choices=Outcome.choices)
    request_id = models.UUIDField("معرّف الطلب", null=True, blank=True)
    session_key_hash = models.CharField(
        "بصمة الجلسة",
        max_length=64,
        null=True,
        blank=True,
    )
    ip_address = models.GenericIPAddressField(
        "عنوان الشبكة",
        protocol="both",
        unpack_ipv4=True,
        null=True,
        blank=True,
    )
    user_agent = models.CharField(
        "وكيل المستخدم",
        max_length=500,
        null=True,
        blank=True,
    )
    integrity_hash = models.CharField(
        "بصمة السلامة",
        max_length=64,
        null=True,
        blank=True,
    )

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        db_table = "audit_logs"
        ordering = ("-occurred_at",)
        verbose_name = "سجل تدقيق"
        verbose_name_plural = "سجلات التدقيق"
        indexes = [
            models.Index(fields=["-occurred_at"], name="audit_occurred_desc_idx"),
            models.Index(
                fields=["actor_user", "-occurred_at"],
                name="audit_actor_occur_idx",
            ),
            models.Index(
                fields=["object_type", "object_id", "occurred_at"],
                name="audit_object_occur_idx",
            ),
            models.Index(
                fields=["department_scope", "occurred_at"],
                name="audit_dept_occur_idx",
            ),
            models.Index(
                fields=["action", "outcome", "occurred_at"],
                name="audit_action_outcome_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AuditLogImmutableError(AuditLogQuerySet._IMMUTABLE_MESSAGE)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditLogImmutableError(AuditLogQuerySet._IMMUTABLE_MESSAGE)

    def __str__(self):
        return f"{self.action} @ {self.occurred_at.isoformat()}"
