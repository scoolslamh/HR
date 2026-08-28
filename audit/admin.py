from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "actor_username_snapshot",
        "action",
        "module",
        "object_type",
        "outcome",
        "department_scope",
    )
    list_filter = ("outcome", "module", "occurred_at")
    search_fields = (
        "actor_username_snapshot",
        "action",
        "object_type",
        "object_repr_masked",
        "request_id",
    )
    raw_id_fields = ("actor_user", "department_scope")
    date_hierarchy = "occurred_at"
    ordering = ("-occurred_at",)
    list_select_related = ("actor_user", "department_scope")

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
