from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog

from .models import (
    Department,
    Employee,
    EmployeeIdentity,
    EmployeePrimaryLocation,
    EmploymentAssignment,
    JobTitle,
    Location,
    UserDepartmentScope,
)


class EmployeeDirectoryTests(TestCase):
    full_national_id = "1023456789"

    def setUp(self):
        self.today = timezone.localdate()
        self.admin = get_user_model().objects.create_superuser(
            username="directory-admin",
            password="Strong-Test-Pass-2026",
        )
        self.manager = get_user_model().objects.create_user(
            username="department-manager",
            password="Strong-Test-Pass-2026",
        )
        self.department_a = self.create_department("A", "القسم الأول")
        self.department_b = self.create_department("B", "القسم الثاني")
        self.location_a = self.create_location("A", "الموقع الأول", self.department_a)
        self.location_a2 = self.create_location("A2", "الموقع البديل", self.department_a)
        self.location_b = self.create_location("B", "الموقع الثاني", self.department_b)
        self.employee_a = self.create_employee(
            "EMP-A",
            "أحمد داخل النطاق",
            self.department_a,
            self.location_a,
            last4="6789",
        )
        self.employee_b = self.create_employee(
            "EMP-B",
            "بدر خارج النطاق",
            self.department_b,
            self.location_b,
            last4="4321",
        )
        UserDepartmentScope.objects.create(
            user=self.manager,
            department=self.department_a,
            access_level=UserDepartmentScope.AccessLevel.MANAGE,
            valid_from=timezone.now() - timedelta(days=1),
            created_by=self.admin,
        )

    def create_department(self, code, name):
        return Department.objects.create(
            code=code,
            name_ar=name,
            unit_type=Department.UnitType.DEPARTMENT,
            valid_from=self.today - timedelta(days=365),
            created_by=self.admin,
            updated_by=self.admin,
        )

    def create_location(self, code, name, department):
        return Location.objects.create(
            code=code,
            name_ar=name,
            location_type=Location.LocationType.BRANCH,
            department=department,
            created_by=self.admin,
            updated_by=self.admin,
        )

    def create_employee(self, number, name, department, location, *, last4):
        employee = Employee.objects.create(
            employee_number=number,
            full_name_ar=name,
            employment_status=Employee.EmploymentStatus.ACTIVE,
            mobile_masked="05****0000",
            created_by=self.admin,
            updated_by=self.admin,
        )
        EmployeeIdentity.objects.create(
            employee=employee,
            national_id_hash=(last4 * 16)[:64],
            national_id_encrypted=b"encrypted-test-value",
            encryption_key_version="test",
            national_id_last4=last4,
            normalized_length=10,
            created_by=self.admin,
            updated_by=self.admin,
        )
        EmploymentAssignment.objects.create(
            employee=employee,
            department=department,
            valid_from=self.today - timedelta(days=30),
            is_primary=True,
            created_by=self.admin,
            updated_by=self.admin,
        )
        EmployeePrimaryLocation.objects.create(
            employee=employee,
            location=location,
            valid_from=self.today - timedelta(days=30),
            created_by=self.admin,
            updated_by=self.admin,
        )
        return employee

    def employee_update_payload(self, *, department=None, location=None, **overrides):
        payload = {
            "full_name_ar": self.employee_a.full_name_ar,
            "employee_number": self.employee_a.employee_number,
            "mobile": "",
            "department": str((department or self.department_a).id),
            "location": str((location or self.location_a).id),
            "location_effective_date": self.today.isoformat(),
            "manager_employee": "",
            "employment_status": Employee.EmploymentStatus.ACTIVE,
        }
        payload.update(overrides)
        return payload

    def test_employee_list_is_scoped_searchable_and_masks_national_id(self):
        self.client.force_login(self.manager)

        response = self.client.get(reverse("organization:employee_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.employee_a.full_name_ar)
        self.assertNotContains(response, self.employee_b.full_name_ar)
        self.assertContains(response, "******6789")
        self.assertNotContains(response, self.full_national_id)

        searched = self.client.get(
            reverse("organization:employee_list"), {"search": "EMP-A"}
        )
        self.assertContains(searched, self.employee_a.full_name_ar)

    def test_employee_list_filters_by_department(self):
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("organization:employee_list"),
            {"department": str(self.department_b.id)},
        )

        self.assertContains(response, self.employee_b.full_name_ar)
        self.assertNotContains(response, self.employee_a.full_name_ar)

    def test_direct_employee_url_outside_scope_returns_404(self):
        self.client.force_login(self.manager)

        response = self.client.get(
            reverse("organization:employee_detail", args=(self.employee_b.id,))
        )

        self.assertEqual(response.status_code, 404)

    def test_employee_update_changes_allowed_fields_and_audits(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("organization:employee_edit", args=(self.employee_a.id,)),
            self.employee_update_payload(
                full_name_ar="أحمد بعد التحديث",
                employee_number="EMP-A-NEW",
                mobile="0501234567",
                employment_status=Employee.EmploymentStatus.SUSPENDED,
            ),
        )

        self.assertRedirects(
            response,
            reverse("organization:employee_detail", args=(self.employee_a.id,)),
        )
        self.employee_a.refresh_from_db()
        self.assertEqual(self.employee_a.full_name_ar, "أحمد بعد التحديث")
        self.assertEqual(self.employee_a.employee_number, "EMP-A-NEW")
        self.assertEqual(self.employee_a.mobile_masked, "05****4567")
        self.assertEqual(
            self.employee_a.employment_status, Employee.EmploymentStatus.SUSPENDED
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="employee.update", object_id=self.employee_a.id
            ).exists()
        )

    def test_department_change_closes_old_assignment_and_keeps_history(self):
        old_assignment = self.employee_a.employment_assignments.get(valid_to__isnull=True)
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("organization:employee_edit", args=(self.employee_a.id,)),
            self.employee_update_payload(
                department=self.department_b,
                location=self.location_b,
            ),
        )

        self.assertEqual(response.status_code, 302)
        old_assignment.refresh_from_db()
        self.assertEqual(old_assignment.valid_to, self.today)
        current = self.employee_a.employment_assignments.get(valid_to__isnull=True)
        self.assertEqual(current.department, self.department_b)
        self.assertEqual(self.employee_a.employment_assignments.count(), 2)

    def test_location_change_closes_old_location_and_keeps_history(self):
        old_location = self.employee_a.primary_location_assignments.get(
            valid_to__isnull=True
        )
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("organization:employee_edit", args=(self.employee_a.id,)),
            self.employee_update_payload(location=self.location_a2),
        )

        self.assertEqual(response.status_code, 302)
        old_location.refresh_from_db()
        self.assertEqual(old_location.valid_to, self.today)
        current = self.employee_a.primary_location_assignments.get(valid_to__isnull=True)
        self.assertEqual(current.location, self.location_a2)
        self.assertEqual(self.employee_a.primary_location_assignments.count(), 2)

    def test_department_signing_location_is_suggested_but_another_location_is_allowed(self):
        self.department_a.signing_location = self.location_a2
        self.department_a.department_head = self.employee_b
        self.department_a.save(
            update_fields=("signing_location", "department_head", "updated_at")
        )
        self.client.force_login(self.manager)

        form_response = self.client.get(
            reverse("organization:employee_edit", args=(self.employee_a.id,))
        )

        self.assertContains(form_response, "department-signing-locations")
        self.assertContains(form_response, "department-heads")
        self.assertContains(form_response, str(self.location_a2.id))
        self.assertContains(form_response, self.location_b.name_ar)
        self.assertContains(form_response, self.employee_b.full_name_ar)

        update_response = self.client.post(
            reverse("organization:employee_edit", args=(self.employee_a.id,)),
            self.employee_update_payload(
                location=self.location_b,
                manager_employee=str(self.employee_b.id),
            ),
        )

        self.assertEqual(update_response.status_code, 302)
        current_location = self.employee_a.primary_location_assignments.get(
            valid_to__isnull=True
        )
        self.assertEqual(current_location.location, self.location_b)
        current_assignment = self.employee_a.employment_assignments.get(
            valid_to__isnull=True
        )
        self.assertEqual(current_assignment.manager_employee, self.employee_b)

    def test_current_location_effective_date_can_be_backdated_without_overlap(self):
        self.client.force_login(self.manager)
        self.client.post(
            reverse("organization:employee_edit", args=(self.employee_a.id,)),
            self.employee_update_payload(location=self.location_a2),
        )
        corrected_date = self.today - timedelta(days=10)

        response = self.client.post(
            reverse("organization:employee_edit", args=(self.employee_a.id,)),
            self.employee_update_payload(
                location=self.location_a2,
                location_effective_date=corrected_date.isoformat(),
            ),
        )

        self.assertEqual(response.status_code, 302)
        locations = self.employee_a.primary_location_assignments.order_by("valid_from")
        previous, current = locations
        self.assertEqual(previous.valid_to, corrected_date)
        self.assertEqual(current.valid_from, corrected_date)
        self.assertEqual(current.location, self.location_a2)

    def test_employee_pages_never_show_full_national_id(self):
        self.client.force_login(self.admin)
        for url in (
            reverse("organization:employee_list"),
            reverse("organization:employee_detail", args=(self.employee_a.id,)),
            reverse("organization:employee_edit", args=(self.employee_a.id,)),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertNotContains(response, self.full_national_id)
        self.assertNotContains(
            self.client.get(
                reverse("organization:employee_edit", args=(self.employee_a.id,))
            ),
            "national_id_encrypted",
        )


class OrganizationReferenceCrudTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.admin = get_user_model().objects.create_superuser(
            username="reference-admin",
            password="Strong-Test-Pass-2026",
        )
        self.department = Department.objects.create(
            code="REF-DEPT",
            name_ar="قسم مرجعي",
            unit_type=Department.UnitType.DEPARTMENT,
            valid_from=self.today - timedelta(days=30),
            created_by=self.admin,
            updated_by=self.admin,
        )
        self.location = Location.objects.create(
            code="REF-LOC",
            name_ar="موقع مرجعي",
            location_type=Location.LocationType.BRANCH,
            department=self.department,
            created_by=self.admin,
            updated_by=self.admin,
        )
        self.client.force_login(self.admin)

    def test_department_form_uses_registered_location_and_optional_head(self):
        head = Employee.objects.create(
            full_name_ar="رئيس القسم التجريبي",
            created_by=self.admin,
            updated_by=self.admin,
        )
        form_response = self.client.get(reverse("organization:department_create"))

        self.assertNotContains(form_response, "صالح من")
        self.assertNotContains(form_response, "صالح حتى")
        self.assertContains(form_response, "مكان التوقيع")
        self.assertContains(form_response, self.location.name_ar)
        self.assertContains(form_response, "رئيس القسم")
        self.assertContains(form_response, head.full_name_ar)

        create_response = self.client.post(
            reverse("organization:department_create"),
            {
                "code": "NEW-DEPT",
                "name_ar": "قسم جديد",
                "unit_type": Department.UnitType.DEPARTMENT,
                "parent": "",
                "signing_location": str(self.location.id),
                "department_head": str(head.id),
            },
        )

        self.assertRedirects(
            create_response, reverse("organization:department_list")
        )
        department = Department.objects.get(code="NEW-DEPT")
        self.assertEqual(department.valid_from, self.today)
        self.assertIsNone(department.valid_to)
        self.assertEqual(department.signing_location, self.location)
        self.assertEqual(department.department_head, head)

    def test_department_and_location_are_disabled_without_historical_deletion(self):
        employee = Employee.objects.create(
            full_name_ar="موظف تاريخي",
            created_by=self.admin,
            updated_by=self.admin,
        )
        assignment = EmploymentAssignment.objects.create(
            employee=employee,
            department=self.department,
            valid_from=self.today - timedelta(days=10),
            created_by=self.admin,
            updated_by=self.admin,
        )
        primary_location = EmployeePrimaryLocation.objects.create(
            employee=employee,
            location=self.location,
            valid_from=self.today - timedelta(days=10),
            created_by=self.admin,
            updated_by=self.admin,
        )

        self.client.post(
            reverse("organization:department_disable", args=(self.department.id,))
        )
        self.client.post(
            reverse("organization:location_disable", args=(self.location.id,))
        )

        self.department.refresh_from_db()
        self.location.refresh_from_db()
        self.assertFalse(self.department.is_active)
        self.assertFalse(self.location.is_active)
        self.assertTrue(EmploymentAssignment.objects.filter(id=assignment.id).exists())
        self.assertTrue(
            EmployeePrimaryLocation.objects.filter(id=primary_location.id).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(action="department.disable").exists()
        )
        self.assertTrue(AuditLog.objects.filter(action="location.disable").exists())

    def test_reference_create_edit_and_job_title_disable(self):
        create_response = self.client.post(
            reverse("organization:job_title_create"),
            {"code": "ANL", "name_ar": "محلل"},
        )
        self.assertRedirects(create_response, reverse("organization:job_title_list"))
        job_title = JobTitle.objects.get(code="ANL")

        edit_response = self.client.post(
            reverse("organization:job_title_edit", args=(job_title.id,)),
            {"code": "ANL", "name_ar": "محلل بيانات"},
        )
        self.assertEqual(edit_response.status_code, 302)
        job_title.refresh_from_db()
        self.assertEqual(job_title.name_ar, "محلل بيانات")

        self.client.post(
            reverse("organization:job_title_disable", args=(job_title.id,))
        )
        job_title.refresh_from_db()
        self.assertFalse(job_title.is_active)
        self.assertTrue(AuditLog.objects.filter(action="jobtitle.disable").exists())
