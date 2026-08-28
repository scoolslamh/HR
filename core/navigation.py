"""Static navigation and placeholder-page definitions for the application shell."""

NAVIGATION_GROUPS = (
    {
        "label": "الرئيسية",
        "items": (
            {
                "name": "dashboard",
                "url_name": "core:dashboard",
                "label": "لوحة المعلومات",
                "icon": "layout-dashboard",
            },
        ),
    },
    {
        "label": "الإدارة والتنظيم",
        "items": (
            {
                "name": "users",
                "url_name": "accounts:user_list",
                "label": "المستخدمون",
                "icon": "users",
            },
            {
                "name": "roles",
                "url_name": "accounts:role_list",
                "label": "الأدوار والصلاحيات",
                "icon": "shield-check",
            },
            {
                "name": "organization",
                "url_name": "organization:department_list",
                "label": "الهيكل التنظيمي",
                "icon": "network",
                "active_url_names": (
                    "department_list",
                    "department_create",
                    "department_edit",
                    "department_disable",
                ),
            },
            {
                "name": "locations",
                "url_name": "organization:location_list",
                "label": "المواقع والفروع",
                "icon": "map-pin",
                "active_url_names": (
                    "location_list",
                    "location_create",
                    "location_edit",
                    "location_disable",
                ),
            },
            {
                "name": "employees",
                "url_name": "organization:employee_list",
                "label": "الموظفون",
                "icon": "contact",
                "active_url_names": (
                    "employee_list",
                    "employee_detail",
                    "employee_edit",
                ),
            },
            {
                "name": "employee_import_list",
                "url_name": "organization:employee_import_list",
                "label": "استيراد بيانات الموظفين",
                "icon": "file-up",
                "active_url_names": (
                    "employee_import_list",
                    "employee_import_upload",
                    "employee_import_detail",
                    "employee_import_preview",
                    "employee_import_errors",
                    "employee_import_approve",
                    "employee_import_template",
                ),
            },
        ),
    },
    {
        "label": "الحضور والانصراف",
        "items": (
            {
                "name": "policies",
                "url_name": "core:policies",
                "label": "سياسات الدوام",
                "icon": "scroll-text",
            },
            {
                "name": "imports",
                "url_name": "attendance:import_list",
                "label": "استيراد الحضور",
                "icon": "file-spreadsheet",
                "active_url_names": (
                    "import_list",
                    "import_upload",
                    "import_detail",
                    "import_preview",
                    "import_errors",
                    "import_approve",
                    "import_delete",
                ),
            },
            {
                "name": "attendance",
                "url_name": "attendance:record_list",
                "label": "سجل الحضور",
                "icon": "clock-3",
                "active_url_names": ("record_list",),
            },
        ),
    },
    {
        "label": "المخالفات والمعالجات",
        "items": (
            {
                "name": "resolutions",
                "url_name": "core:resolutions",
                "label": "طلبات المعالجة",
                "icon": "file-pen-line",
            },
            {
                "name": "approvals",
                "url_name": "core:approvals",
                "label": "الاعتمادات",
                "icon": "badge-check",
            },
            {
                "name": "manager_dashboard",
                "url_name": "violations:manager_dashboard",
                "label": "إفادات موظفي القسم",
                "icon": "clipboard-check",
                "required_permission": "clarifications.approve_department",
            },
            {
                "name": "executive_dashboard",
                "url_name": "violations:executive_dashboard",
                "label": "المؤشر العام للإفادات",
                "icon": "chart-column-big",
                "required_permission": "clarifications.view_all",
            },
        ),
    },
    {
        "label": "التقارير",
        "items": (
            {
                "name": "reports",
                "url_name": "attendance:report_overview",
                "label": "التقارير",
                "icon": "chart-no-axes-combined",
                "active_url_names": ("report_overview", "outside_location_report"),
            },
        ),
    },
    {
        "label": "إدارة النظام",
        "items": (
            {
                "name": "settings",
                "url_name": "core:settings",
                "label": "إعدادات النظام",
                "icon": "settings",
            },
        ),
    },
)


PLACEHOLDER_PAGES = (
    {
        "name": "policies",
        "path": "attendance/policies/",
        "title": "سياسات الدوام",
        "description": "عرض سياسات الدوام وإصداراتها وفترات نفاذها.",
    },
    {
        "name": "resolutions",
        "path": "violations/resolutions/",
        "title": "طلبات المعالجة",
        "description": "متابعة طلبات معالجة المخالفات والمبررات المرفقة.",
    },
    {
        "name": "approvals",
        "path": "approvals/",
        "title": "الاعتمادات",
        "description": "عرض الطلبات الواردة ومسارات الاعتماد والقرارات.",
    },
    {
        "name": "settings",
        "path": "settings/",
        "title": "إعدادات النظام",
        "description": "عرض الإعدادات العامة والقواميس المرجعية للنظام.",
    },
)


def get_navigation_groups():
    """Return immutable navigation metadata for templates."""

    return NAVIGATION_GROUPS
