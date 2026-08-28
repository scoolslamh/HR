from __future__ import annotations

import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import Permission, Role, RolePermission, User, UserRole


def _datetime(value):
    return value.isoformat() if value else None


class Command(BaseCommand):
    help = "تصدير حسابات المستخدمين والأدوار والصلاحيات فقط إلى ملف JSON آمن للنقل."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True, help="مسار ملف JSON الناتج.")
        parser.add_argument(
            "--force", action="store_true", help="السماح باستبدال ملف موجود."
        )

    def handle(self, *args, **options):
        output_path = Path(options["output"]).expanduser().resolve()
        if output_path.exists() and not options["force"]:
            raise CommandError("ملف التصدير موجود مسبقًا. استخدم --force لاستبداله.")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        users = list(User.objects.order_by("username"))
        payload = {
            "format": "attendance_portal.users",
            "version": 1,
            "exported_at": timezone.now().isoformat(),
            "users": [
                {
                    "id": str(user.id),
                    "username": user.username,
                    "email": user.email,
                    "password": user.password,
                    "is_active": user.is_active,
                    "is_staff": user.is_staff,
                    "is_superuser": user.is_superuser,
                    "must_change_password": user.must_change_password,
                    "failed_login_count": user.failed_login_count,
                    "locked_until": _datetime(user.locked_until),
                    "password_changed_at": _datetime(user.password_changed_at),
                    "locale": user.locale,
                    "archived_at": _datetime(user.archived_at),
                }
                for user in users
            ],
            "roles": [
                {
                    "code": role.code,
                    "name_ar": role.name_ar,
                    "description_ar": role.description_ar,
                    "is_system": role.is_system,
                    "is_active": role.is_active,
                }
                for role in Role.objects.order_by("code")
            ],
            "permissions": [
                {
                    "code": permission.code,
                    "module": permission.module,
                    "action": permission.action,
                    "name_ar": permission.name_ar,
                    "description_ar": permission.description_ar,
                    "is_active": permission.is_active,
                }
                for permission in Permission.objects.order_by("code")
            ],
            "role_permissions": [
                {
                    "role": assignment.role.code,
                    "permission": assignment.permission.code,
                    "granted_at": _datetime(assignment.granted_at),
                }
                for assignment in RolePermission.objects.select_related(
                    "role", "permission"
                ).filter(revoked_at__isnull=True)
            ],
            "user_roles": [
                {
                    "id": str(assignment.id),
                    "user": str(assignment.user_id),
                    "role": assignment.role.code,
                    "valid_from": _datetime(assignment.valid_from),
                    "valid_to": _datetime(assignment.valid_to),
                    "is_active": assignment.is_active,
                }
                for assignment in UserRole.objects.select_related("role").filter(
                    is_active=True
                )
            ],
        }
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        descriptor = os.open(output_path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output_file:
            json.dump(payload, output_file, ensure_ascii=False, indent=2)
        self.stdout.write(
            self.style.SUCCESS(
                f"تم تصدير {len(users)} مستخدمًا إلى {output_path}. "
                "الملف يحتوي تجزئات كلمات المرور ويجب حمايته."
            )
        )
