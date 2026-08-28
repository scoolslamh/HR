from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .forms import UserChangeAdminForm, UserCreationAdminForm
from .models import Permission, Role, RolePermission, User, UserRole


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    add_form = UserCreationAdminForm
    form = UserChangeAdminForm
    model = User
    list_display = ("username", "email", "is_active", "is_staff", "is_superuser")
    list_filter = ("is_active", "is_staff", "is_superuser", "must_change_password")
    search_fields = ("username", "email", "first_name", "last_name")
    ordering = ("username",)
    filter_horizontal = ()
    readonly_fields = (
        "last_login",
        "created_at",
        "updated_at",
        "created_by",
        "updated_by",
    )
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("البيانات الشخصية", {"fields": ("first_name", "last_name", "email", "locale")}),
        (
            "حالة الحساب",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "must_change_password",
                    "failed_login_count",
                    "locked_until",
                    "password_changed_at",
                    "archived_at",
                )
            },
        ),
        ("التتبع", {"fields": ("last_login", "created_at", "updated_at", "created_by", "updated_by")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "email",
                    "first_name",
                    "last_name",
                    "password1",
                    "password2",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                ),
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        if not change and obj.created_by_id is None:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("code", "name_ar", "is_system", "is_active", "updated_at")
    list_filter = ("is_system", "is_active")
    search_fields = ("code", "name_ar")
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")

    def save_model(self, request, obj, form, change):
        if not change and obj.created_by_id is None:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("code", "name_ar", "module", "action", "is_active")
    list_filter = ("module", "is_active")
    search_fields = ("code", "name_ar", "module", "action")
    readonly_fields = ("created_at", "updated_at")


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = ("role", "permission", "granted_at", "revoked_at")
    list_filter = ("role", "permission__module", "revoked_at")
    search_fields = ("role__code", "role__name_ar", "permission__code", "permission__name_ar")
    autocomplete_fields = ("role", "permission", "granted_by", "revoked_by")
    readonly_fields = ("granted_at", "granted_by", "revoked_by")

    def save_model(self, request, obj, form, change):
        if not change and obj.granted_by_id is None:
            obj.granted_by = request.user
        if change and obj.revoked_at and obj.revoked_by_id is None:
            obj.revoked_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "valid_from", "valid_to", "is_active")
    list_filter = ("role", "is_active")
    search_fields = ("user__username", "role__code", "role__name_ar")
    autocomplete_fields = ("user", "role")
    readonly_fields = ("created_at", "created_by")

    def save_model(self, request, obj, form, change):
        if not change and obj.created_by_id is None:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
