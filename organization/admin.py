from django.contrib import admin

from .models import Department, UserDepartmentScope


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name_ar",
        "unit_type",
        "parent",
        "signing_location",
        "department_head",
        "level",
        "is_active",
        "valid_from",
        "valid_to",
        "archived_at",
    )
    list_filter = ("unit_type", "is_active", "level")
    search_fields = ("code", "name_ar", "path_cache")
    ordering = ("code",)
    list_select_related = ("parent", "signing_location", "department_head")
    autocomplete_fields = ("parent",)
    raw_id_fields = (
        "signing_location",
        "department_head",
        "created_by",
        "updated_by",
    )
    readonly_fields = ("path_cache", "created_at", "updated_at")
    date_hierarchy = "created_at"
    fieldsets = (
        (
            "بيانات الوحدة",
            {
                "fields": (
                    "code",
                    "name_ar",
                    "unit_type",
                    "parent",
                    "signing_location",
                    "department_head",
                    "level",
                    "path_cache",
                )
            },
        ),
        (
            "النفاذ والأرشفة",
            {"fields": ("is_active", "valid_from", "valid_to", "archived_at")},
        ),
        (
            "التدقيق",
            {
                "fields": ("created_at", "updated_at", "created_by", "updated_by"),
                "classes": ("collapse",),
            },
        ),
    )

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UserDepartmentScope)
class UserDepartmentScopeAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "department",
        "role",
        "access_level",
        "include_descendants",
        "valid_from",
        "valid_to",
    )
    list_filter = ("access_level", "include_descendants")
    search_fields = (
        "user__username",
        "department__code",
        "department__name_ar",
        "role__code",
    )
    ordering = ("user__username", "department__code", "valid_from")
    list_select_related = ("user", "department", "role", "created_by")
    raw_id_fields = ("user", "department", "role", "created_by")
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"
    fieldsets = (
        (
            "النطاق",
            {
                "fields": (
                    "user",
                    "department",
                    "role",
                    "access_level",
                    "include_descendants",
                )
            },
        ),
        ("مدة النفاذ", {"fields": ("valid_from", "valid_to")}),
        (
            "التدقيق",
            {"fields": ("created_at", "created_by"), "classes": ("collapse",)},
        ),
    )

    def has_delete_permission(self, request, obj=None):
        return False
