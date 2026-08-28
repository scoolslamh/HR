from __future__ import annotations

import json
import uuid
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime

from accounts.models import Permission, Role, RolePermission, User, UserRole


MAX_IMPORT_BYTES = 10 * 1024 * 1024
USER_FIELDS = (
    "email",
    "password",
    "is_active",
    "is_staff",
    "is_superuser",
    "must_change_password",
    "failed_login_count",
    "locale",
)


def _load_payload(path: Path) -> dict:
    if not path.is_file():
        raise CommandError("ملف الاستيراد غير موجود.")
    if path.stat().st_size > MAX_IMPORT_BYTES:
        raise CommandError("ملف الاستيراد أكبر من الحد المسموح (10 MiB).")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CommandError("ملف الاستيراد ليس JSON صالحًا.") from exc
    if payload.get("format") != "attendance_portal.users" or payload.get("version") != 1:
        raise CommandError("صيغة ملف حسابات المستخدمين أو إصدارها غير مدعوم.")
    for key in ("users", "roles", "permissions", "role_permissions", "user_roles"):
        if not isinstance(payload.get(key), list):
            raise CommandError(f"القسم {key} مفقود أو غير صالح.")
    return payload


def _parsed_datetime(value, field_name):
    if value in (None, ""):
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise CommandError(f"قيمة التاريخ في {field_name} غير صالحة.")
    return parsed


class Command(BaseCommand):
    help = "استيراد حسابات المستخدمين وأدوارهم دون حذف أي حساب موجود."

    def add_arguments(self, parser):
        parser.add_argument("--input", required=True, help="مسار ملف JSON المصدر.")

    @transaction.atomic
    def handle(self, *args, **options):
        payload = _load_payload(Path(options["input"]).expanduser().resolve())
        created = updated = skipped = 0

        try:
            permission_map = self._import_permissions(payload["permissions"])
            role_map = self._import_roles(payload["roles"])
            user_map = {}
            for item in payload["users"]:
                outcome, user = self._import_user(item)
                user_map[str(item["id"])] = user
                if outcome == "created":
                    created += 1
                elif outcome == "updated":
                    updated += 1
                else:
                    skipped += 1
            self._import_role_permissions(
                payload["role_permissions"], role_map, permission_map
            )
            self._import_user_roles(payload["user_roles"], user_map, role_map)
        except (KeyError, TypeError, ValueError, ValidationError, IntegrityError) as exc:
            raise CommandError(f"فشل التحقق من ملف المستخدمين: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"اكتمل الاستيراد: تم إنشاء {created}، تحديث {updated}، "
                f"تخطي {skipped}. لم يُحذف أي مستخدم."
            )
        )

    def _import_user(self, item):
        source_id = uuid.UUID(item["id"])
        username = User.normalize_username(item["username"]).strip()
        if not username or len(username) > 150:
            raise CommandError("اسم مستخدم فارغ أو أطول من الحد المسموح.")
        email = item.get("email", "").strip()
        if email:
            validate_email(email)

        by_id = User.objects.filter(pk=source_id).first()
        by_username = User.objects.filter(username__iexact=username).first()
        if by_id and by_username and by_id.pk != by_username.pk:
            raise CommandError(f"تعارض بين المعرّف واسم المستخدم: {username}")
        user = by_id or by_username
        is_new = user is None
        if is_new:
            user = User(id=source_id, username=username)
        elif user.username.casefold() != username.casefold():
            raise CommandError(f"المعرّف مستخدم بواسطة حساب آخر: {username}")

        changed = is_new
        if user.username != username:
            user.username = username
            changed = True
        item = {**item, "email": email}
        for field in USER_FIELDS:
            value = item[field]
            if getattr(user, field) != value:
                setattr(user, field, value)
                changed = True
        for field in ("locked_until", "password_changed_at", "archived_at"):
            value = _parsed_datetime(item.get(field), field)
            if getattr(user, field) != value:
                setattr(user, field, value)
                changed = True
        if changed:
            user.save()
            return ("created" if is_new else "updated"), user
        return "skipped", user

    def _import_permissions(self, items):
        result = {}
        for item in items:
            conflict = Permission.objects.filter(
                module=item["module"], action=item["action"]
            ).exclude(code=item["code"]).first()
            if conflict:
                raise CommandError(f"تعارض رمز الصلاحية: {item['code']}")
            permission, _ = Permission.objects.update_or_create(
                code=item["code"],
                defaults={
                    key: item[key]
                    for key in (
                        "module", "action", "name_ar", "description_ar", "is_active"
                    )
                },
            )
            result[item["code"]] = permission
        return result

    def _import_roles(self, items):
        result = {}
        for item in items:
            role, _ = Role.objects.update_or_create(
                code=item["code"],
                defaults={
                    key: item[key]
                    for key in (
                        "name_ar", "description_ar", "is_system", "is_active"
                    )
                },
            )
            result[item["code"]] = role
        return result

    def _import_role_permissions(self, items, roles, permissions):
        for item in items:
            role = roles[item["role"]]
            permission = permissions[item["permission"]]
            if not RolePermission.objects.filter(
                role=role, permission=permission, revoked_at__isnull=True
            ).exists():
                RolePermission.objects.create(
                    role=role,
                    permission=permission,
                    granted_at=_parsed_datetime(item["granted_at"], "granted_at"),
                )

    def _import_user_roles(self, items, users, roles):
        for item in items:
            user = users[item["user"]]
            role = roles[item["role"]]
            valid_from = _parsed_datetime(item["valid_from"], "valid_from")
            UserRole.objects.update_or_create(
                user=user,
                role=role,
                valid_from=valid_from,
                defaults={
                    "valid_to": _parsed_datetime(item.get("valid_to"), "valid_to"),
                    "is_active": item["is_active"],
                },
            )
