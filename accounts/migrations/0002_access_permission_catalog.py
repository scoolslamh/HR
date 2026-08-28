from django.db import migrations


PERMISSIONS = (
    ("employees.view_department", "employees", "view_department", "عرض موظفي القسم"),
    ("employees.manage", "employees", "manage", "تعديل بيانات الموظفين"),
    ("employees.import", "employees", "import", "استيراد بيانات الموظفين"),
    ("attendance.view", "attendance", "view", "عرض سجل الحضور"),
    ("attendance.reports", "attendance", "reports", "عرض تقارير الحضور"),
    ("attendance.import", "attendance", "import", "استيراد سجل الحضور"),
    ("attendance.approve", "attendance", "approve", "اعتماد استيراد الحضور"),
    ("attendance.calculate", "attendance", "calculate", "تشغيل إعادة الاحتساب"),
    ("organization.manage", "organization", "manage", "إدارة الهيكل التنظيمي"),
)


def create_permission_catalog(apps, schema_editor):
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

    role, _ = Role.objects.get_or_create(
        code="department_head",
        defaults={
            "name_ar": "رئيس قسم",
            "description_ar": "عرض موظفي القسم وسجلات الحضور والتقارير ضمن النطاق المحدد.",
            "is_system": True,
            "is_active": True,
        },
    )
    for code in (
        "employees.view_department",
        "attendance.view",
        "attendance.reports",
    ):
        RolePermission.objects.get_or_create(
            role=role,
            permission=permissions[code],
            revoked_at__isnull=True,
        )


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]

    operations = [migrations.RunPython(create_permission_catalog, migrations.RunPython.noop)]
