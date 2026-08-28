from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from accounts.models import Role

from .models import Department, Employee, EmploymentAssignment, UserDepartmentScope
from .selectors import employees_in_user_department_scope


class OrganizationModelsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="department.manager",
            password="Strong-Test-Password-123",
        )
        cls.role = Role.objects.create(
            code="department_manager",
            name_ar="مسؤول القسم",
        )
        cls.root = Department.objects.create(
            code="HQ",
            name_ar="الإدارة العامة",
            unit_type=Department.UnitType.DIRECTORATE,
            level=0,
            valid_from=date(2026, 1, 1),
            created_by=cls.user,
        )

    def test_department_tree_uses_protected_self_relation(self):
        child = Department.objects.create(
            code="HR",
            name_ar="قسم الموارد البشرية",
            unit_type=Department.UnitType.DEPARTMENT,
            parent=self.root,
            level=1,
            valid_from=date(2026, 1, 1),
            created_by=self.user,
        )

        self.assertEqual(child.parent, self.root)
        self.assertQuerySetEqual(self.root.children.all(), [child])

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.root.delete()

    def test_scope_defaults_to_current_department_only(self):
        scope = UserDepartmentScope.objects.create(
            user=self.user,
            department=self.root,
            role=self.role,
            access_level=UserDepartmentScope.AccessLevel.MANAGE,
            valid_from=timezone.now(),
            created_by=self.user,
        )

        self.assertFalse(scope.include_descendants)
        self.assertEqual(scope.user, self.user)
        self.assertEqual(scope.department, self.root)
        self.assertEqual(scope.role, self.role)

    def test_duplicate_scopes_are_rejected_with_and_without_role(self):
        valid_from = timezone.now()
        common_fields = {
            "user": self.user,
            "department": self.root,
            "access_level": UserDepartmentScope.AccessLevel.VIEW,
            "valid_from": valid_from,
        }

        UserDepartmentScope.objects.create(**common_fields)
        with self.assertRaises(IntegrityError), transaction.atomic():
            UserDepartmentScope.objects.create(**common_fields)

        fields_with_role = {
            **common_fields,
            "role": self.role,
            "access_level": UserDepartmentScope.AccessLevel.APPROVE,
        }
        UserDepartmentScope.objects.create(**fields_with_role)
        with self.assertRaises(IntegrityError), transaction.atomic():
            UserDepartmentScope.objects.create(**fields_with_role)

    def test_invalid_scope_period_is_rejected(self):
        valid_from = timezone.now()

        with self.assertRaises(IntegrityError), transaction.atomic():
            UserDepartmentScope.objects.create(
                user=self.user,
                department=self.root,
                access_level=UserDepartmentScope.AccessLevel.VIEW,
                valid_from=valid_from,
                valid_to=valid_from - timedelta(seconds=1),
            )

    def test_department_manager_scope_uses_department_not_direct_manager(self):
        outside_department = Department.objects.create(
            code="OUTSIDE",
            name_ar="قسم خارج النطاق",
            unit_type=Department.UnitType.DEPARTMENT,
            valid_from=date(2026, 1, 1),
            created_by=self.user,
        )
        outside_manager = Employee.objects.create(
            full_name_ar="مدير من قسم آخر",
            created_by=self.user,
            updated_by=self.user,
        )
        scoped_employee = Employee.objects.create(
            full_name_ar="موظف داخل النطاق",
            created_by=self.user,
            updated_by=self.user,
        )
        EmploymentAssignment.objects.create(
            employee=outside_manager,
            department=outside_department,
            valid_from=date(2026, 1, 1),
            is_primary=True,
            created_by=self.user,
            updated_by=self.user,
        )
        EmploymentAssignment.objects.create(
            employee=scoped_employee,
            department=self.root,
            manager_employee=outside_manager,
            valid_from=date(2026, 1, 1),
            is_primary=True,
            created_by=self.user,
            updated_by=self.user,
        )
        UserDepartmentScope.objects.create(
            user=self.user,
            department=self.root,
            role=self.role,
            access_level=UserDepartmentScope.AccessLevel.VIEW,
            valid_from=timezone.now() - timedelta(days=1),
            created_by=self.user,
        )

        visible = employees_in_user_department_scope(self.user)

        self.assertQuerySetEqual(visible, [scoped_employee])
