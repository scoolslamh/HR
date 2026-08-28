import uuid
from datetime import date

from django.contrib.auth import authenticate, get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from organization.access import user_has_business_permission
from organization.models import Department, Employee, EmploymentAssignment, UserDepartmentScope

from .models import Permission, Role, RolePermission, UserRole


class UserModelTests(TestCase):
    def test_create_user_with_uuid_and_hashed_password(self):
        user = get_user_model().objects.create_user(
            username="operator",
            password="Strong-Test-Pass-2026",
            email="operator@example.com",
        )

        self.assertIsInstance(user.id, uuid.UUID)
        self.assertEqual(user.id.version, 4)
        self.assertTrue(user.check_password("Strong-Test-Pass-2026"))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_username_is_unique_case_insensitively(self):
        get_user_model().objects.create_user("DepartmentAdmin", "Strong-Test-Pass-2026")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                get_user_model().objects.create_user(
                    "departmentadmin",
                    "Another-Strong-Pass-2026",
                )

    def test_authentication_uses_username_and_password_case_insensitively(self):
        user = get_user_model().objects.create_user(
            "AttendanceAdmin",
            "Strong-Test-Pass-2026",
        )

        authenticated = authenticate(
            username="attendanceadmin",
            password="Strong-Test-Pass-2026",
        )

        self.assertEqual(authenticated, user)


class RolePermissionTests(TestCase):
    def setUp(self):
        self.role = Role.objects.create(code="department_manager", name_ar="مسؤول القسم")
        self.permission = Permission.objects.get(code="employees.view_department")

    def test_create_role_and_permission(self):
        self.assertEqual(self.role.id.version, 4)
        self.assertEqual(self.permission.id.version, 4)
        self.assertTrue(self.role.is_active)
        self.assertTrue(self.permission.is_active)

    def test_prevent_duplicate_active_role_permission(self):
        RolePermission.objects.create(role=self.role, permission=self.permission)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RolePermission.objects.create(role=self.role, permission=self.permission)


class UserAccessManagementTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="access-admin", password="Strong-Test-Pass-2026"
        )
        self.department_a = Department.objects.create(
            code="ACCESS-A",
            name_ar="قسم صلاحيات أول",
            unit_type=Department.UnitType.DEPARTMENT,
            valid_from=date(2026, 1, 1),
        )
        self.department_b = Department.objects.create(
            code="ACCESS-B",
            name_ar="قسم صلاحيات ثانٍ",
            unit_type=Department.UnitType.DEPARTMENT,
            valid_from=date(2026, 1, 1),
        )
        self.employee_a = Employee.objects.create(full_name_ar="رئيس القسم الأول")
        self.employee_b = Employee.objects.create(full_name_ar="موظف القسم الثاني")
        EmploymentAssignment.objects.create(
            employee=self.employee_a,
            department=self.department_a,
            valid_from=date(2026, 1, 1),
            is_primary=True,
        )
        EmploymentAssignment.objects.create(
            employee=self.employee_b,
            department=self.department_b,
            valid_from=date(2026, 1, 1),
            is_primary=True,
        )
        self.role = Role.objects.get(code="department_head")
        self.client.force_login(self.admin)

    def test_admin_creates_department_head_with_role_and_department_scope(self):
        response = self.client.post(
            reverse("accounts:user_create"),
            {
                "username": "department-head",
                "email": "head@example.com",
                "is_active": "on",
                "employee": str(self.employee_a.id),
                "roles": [str(self.role.id)],
                "departments": [str(self.department_a.id)],
                "access_level": UserDepartmentScope.AccessLevel.VIEW,
                "include_descendants": "",
                "password1": "Strong-Test-Pass-2026",
                "password2": "Strong-Test-Pass-2026",
            },
        )

        self.assertRedirects(response, reverse("accounts:user_list"))
        user = get_user_model().objects.get(username="department-head")
        self.employee_a.refresh_from_db()
        self.assertEqual(self.employee_a.user, user)
        self.assertTrue(UserRole.objects.filter(user=user, role=self.role, is_active=True).exists())
        self.assertTrue(
            UserDepartmentScope.objects.filter(
                user=user, department=self.department_a, valid_to__isnull=True
            ).exists()
        )
        self.assertTrue(user_has_business_permission(user, "attendance.view"))
        self.assertTrue(AuditLog.objects.filter(action="user.create", object_id=user.id).exists())

        self.client.force_login(user)
        employee_page = self.client.get(reverse("organization:employee_list"))
        self.assertEqual(employee_page.status_code, 200)
        self.assertContains(employee_page, self.employee_a.full_name_ar)
        self.assertNotContains(employee_page, self.employee_b.full_name_ar)
        self.assertEqual(self.client.get(reverse("accounts:user_list")).status_code, 403)

    def test_admin_can_change_permissions_assigned_to_role(self):
        permission = Permission.objects.get(code="attendance.calculate")

        response = self.client.post(
            reverse("accounts:role_edit", args=(self.role.id,)),
            {
                "code": self.role.code,
                "name_ar": self.role.name_ar,
                "description_ar": self.role.description_ar,
                "is_active": "on",
                "permissions": [str(permission.id)],
            },
        )

        self.assertRedirects(response, reverse("accounts:role_list"))
        self.assertTrue(
            RolePermission.objects.filter(
                role=self.role,
                permission=permission,
                revoked_at__isnull=True,
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="role.update_permissions", object_id=self.role.id
            ).exists()
        )

    def test_role_permission_checkboxes_add_once_and_remove_when_unchecked(self):
        permission = Permission.objects.get(code="attendance.calculate")
        payload = {
            "code": self.role.code,
            "name_ar": self.role.name_ar,
            "description_ar": self.role.description_ar,
            "is_active": "on",
            "permissions": [str(permission.id)],
        }

        self.client.post(reverse("accounts:role_edit", args=(self.role.id,)), payload)
        self.client.post(reverse("accounts:role_edit", args=(self.role.id,)), payload)

        self.assertEqual(
            RolePermission.objects.filter(
                role=self.role,
                permission=permission,
                revoked_at__isnull=True,
            ).count(),
            1,
        )

        payload.pop("permissions")
        response = self.client.post(
            reverse("accounts:role_edit", args=(self.role.id,)), payload
        )

        self.assertRedirects(response, reverse("accounts:role_list"))
        self.assertFalse(
            RolePermission.objects.filter(
                role=self.role,
                permission=permission,
                revoked_at__isnull=True,
            ).exists()
        )
