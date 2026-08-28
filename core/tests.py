from django.conf import settings
from django.contrib.auth import SESSION_KEY, get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Permission, Role, RolePermission, UserRole

from .navigation import PLACEHOLDER_PAGES
from .views import error_403


class ApplicationShellTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="shell-user",
            password="Strong-Test-Pass-2026",
            first_name="علي",
            last_name="الشهري",
        )
        self.client.force_login(self.user)

    def test_dashboard_uses_arabic_rtl_shell(self):
        response = self.client.get(reverse("core:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<html lang="ar" dir="rtl"', html=False)
        self.assertContains(response, "لوحة المعلومات")
        self.assertContains(response, "tabler.rtl.min.css")
        self.assertContains(response, "Cairo")
        self.assertContains(response, "منصة الحضور والانضباط الوظيفي")
        self.assertContains(response, "ابحث عن موظف أو قسم أو مخالفة...")
        self.assertContains(response, "مرحبًا، علي الشهري")
        self.assertContains(response, "إجمالي الموظفين")
        self.assertContains(response, "ضمن نطاقك التنظيمي")
        self.assertContains(response, "رسم الحضور الأسبوعي")
        self.assertNotContains(response, "بيانات توضيحية")
        self.assertContains(response, "lucide.min.js")
        self.assertNotContains(response, "tabler-icons.min.css")

    def test_navigation_uses_requested_groups_and_lucide_icons(self):
        response = self.client.get(reverse("core:dashboard"))

        for group_name in (
            "الرئيسية",
            "الإدارة والتنظيم",
            "الحضور والانصراف",
            "المخالفات والمعالجات",
            "التقارير",
            "إدارة النظام",
        ):
            self.assertContains(response, group_name)
        self.assertContains(response, 'data-lucide="layout-dashboard"')
        self.assertNotContains(response, 'class="ti ')

    def test_login_is_available_without_authentication(self):
        self.client.logout()
        response = self.client.get(reverse("core:login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تسجيل الدخول")
        self.assertContains(response, "اسم المستخدم")
        self.assertContains(response, "csrfmiddlewaretoken")

    def test_all_sidebar_placeholder_pages_are_available(self):
        for page in PLACEHOLDER_PAGES:
            with self.subTest(page=page["name"]):
                response = self.client.get(reverse(f"core:{page['name']}"))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, page["title"])
                self.assertContains(response, "هذه الصفحة جاهزة للمرحلة الوظيفية القادمة")

    def test_forbidden_page_returns_403(self):
        request = RequestFactory().get("/forbidden/")

        response = error_403(request)

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "غير مصرح بالوصول", status_code=403)

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
    def test_unknown_path_uses_custom_404_page(self):
        response = self.client.get("/صفحة-غير-موجودة/")

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "الصفحة غير موجودة", status_code=404)


class AuthenticationFlowTests(TestCase):
    password = "Strong-Test-Pass-2026"

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="attendance-operator",
            password=self.password,
        )
        self.inactive_user = get_user_model().objects.create_user(
            username="inactive-operator",
            password=self.password,
            is_active=False,
        )

    def login(self, user=None, **extra):
        user = user or self.user
        payload = {
            "username": user.username,
            "password": self.password,
            **extra,
        }
        return self.client.post(reverse("core:login"), payload)

    def grant_import_permission(self):
        permission = Permission.objects.get(code="employees.import")
        role = Role.objects.create(
            code="employee_importer",
            name_ar="مستورد بيانات الموظفين",
        )
        RolePermission.objects.create(
            role=role,
            permission=permission,
            granted_at=timezone.now(),
        )
        UserRole.objects.create(
            user=self.user,
            role=role,
            valid_from=timezone.now(),
        )

    def test_successful_login_redirects_to_dashboard(self):
        response = self.login()

        self.assertRedirects(response, reverse("core:dashboard"))
        self.assertEqual(self.client.session[SESSION_KEY], str(self.user.pk))

    def test_invalid_password_shows_clear_arabic_error(self):
        response = self.client.post(
            reverse("core:login"),
            {"username": self.user.username, "password": "Wrong-Test-Pass-2026"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "اسم المستخدم أو كلمة المرور غير صحيحة.")
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_inactive_account_is_rejected_with_arabic_error(self):
        response = self.login(self.inactive_user)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "هذا الحساب غير نشط")
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_logout_ends_session_and_redirects_to_login(self):
        self.login()

        response = self.client.post(reverse("core:logout"))

        self.assertRedirects(response, reverse("core:login"))
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_anonymous_user_is_redirected_from_protected_page(self):
        response = self.client.get(reverse("core:dashboard"))

        self.assertRedirects(
            response,
            f"{reverse('core:login')}?next={reverse('core:dashboard')}",
        )

    def test_remember_me_uses_persistent_safe_expiry(self):
        self.login(remember_me="on")

        session = self.client.session
        self.assertFalse(session.get_expire_at_browser_close())
        self.assertGreaterEqual(
            session.get_expiry_age(),
            settings.LOGIN_REMEMBER_SECONDS - 5,
        )
        self.assertLessEqual(
            session.get_expiry_age(),
            settings.LOGIN_REMEMBER_SECONDS,
        )

    def test_without_remember_me_expires_at_browser_close(self):
        self.login()

        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_authorized_user_can_open_employee_import_page_after_login(self):
        self.grant_import_permission()
        self.login()

        response = self.client.get(reverse("organization:employee_import_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "دفعات الاستيراد")

    def test_unauthorized_user_is_denied_employee_import_page_after_login(self):
        self.login()

        response = self.client.get(reverse("organization:employee_import_list"))

        self.assertEqual(response.status_code, 403)
