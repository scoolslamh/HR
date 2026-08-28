import json
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from accounts.models import Permission, Role, RolePermission, User, UserRole


class UserTransferCommandTests(TestCase):
    def test_export_and_import_preserve_login_hash_and_access(self):
        user = User.objects.create_user(
            username="transfer-user",
            password="Strong-Transfer-Pass-2026",
            email="transfer@example.com",
            is_staff=True,
        )
        password_hash = user.password
        role = Role.objects.create(code="transfer-role", name_ar="دور النقل")
        permission = Permission.objects.get(code="attendance.import")
        RolePermission.objects.create(role=role, permission=permission)
        UserRole.objects.create(user=user, role=role)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.json"
            call_command("export_users", output=str(path))
            payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(payload["format"], "attendance_portal.users")
            self.assertNotIn("employees", payload)
            self.assertNotIn("sessions", payload)
            self.assertNotIn("audit_logs", payload)

            UserRole.objects.filter(user=user).delete()
            user.delete()
            call_command("import_users", input=str(path))

        imported = User.objects.get(username="transfer-user")
        self.assertEqual(imported.password, password_hash)
        self.assertTrue(imported.check_password("Strong-Transfer-Pass-2026"))
        self.assertTrue(imported.is_staff)
        self.assertTrue(
            UserRole.objects.filter(
                user=imported, role__code="transfer-role", is_active=True
            ).exists()
        )

    def test_reimport_updates_without_creating_duplicate_user(self):
        user = User.objects.create_user(username="existing-user", password="Pass-2026")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.json"
            call_command("export_users", output=str(path))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["users"][0]["email"] = "updated@example.com"
            path.write_text(json.dumps(payload), encoding="utf-8")
            call_command("import_users", input=str(path))

        self.assertEqual(User.objects.filter(username="existing-user").count(), 1)
        user.refresh_from_db()
        self.assertEqual(user.email, "updated@example.com")
