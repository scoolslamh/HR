from django.db import migrations


PERMISSIONS = (
    (
        "clarifications.approve_department",
        "clarifications",
        "approve_department",
        "اعتماد إفادات موظفي القسم",
    ),
    (
        "clarifications.view_all",
        "clarifications",
        "view_all",
        "عرض المؤشر العام للإفادات",
    ),
)


def create_clarification_permissions(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    Role = apps.get_model("accounts", "Role")
    RolePermission = apps.get_model("accounts", "RolePermission")

    permissions = {}
    for code, module, action, name_ar in PERMISSIONS:
        permission, _ = Permission.objects.update_or_create(
            code=code,
            defaults={
                "module": module,
                "action": action,
                "name_ar": name_ar,
                "is_active": True,
            },
        )
        permissions[code] = permission

    department_head = Role.objects.get(code="department_head")
    RolePermission.objects.get_or_create(
        role=department_head,
        permission=permissions["clarifications.approve_department"],
        revoked_at__isnull=True,
    )

    general_manager, _ = Role.objects.get_or_create(
        code="general_manager",
        defaults={
            "name_ar": "المدير العام",
            "description_ar": "عرض المؤشر العام والتفاصيل المجمعة للإفادات حسب الأقسام.",
            "is_system": True,
            "is_active": True,
        },
    )
    RolePermission.objects.get_or_create(
        role=general_manager,
        permission=permissions["clarifications.view_all"],
        revoked_at__isnull=True,
    )


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_access_permission_catalog")]

    operations = [
        migrations.RunPython(create_clarification_permissions, migrations.RunPython.noop)
    ]
