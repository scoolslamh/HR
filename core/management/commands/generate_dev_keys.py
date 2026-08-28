import base64
import secrets
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


KEY_NAMES = ("PII_ENCRYPTION_KEY", "NATIONAL_ID_HMAC_KEY")


class Command(BaseCommand):
    help = "إنشاء مفاتيح تطوير محلية مفقودة دون عرض قيمها."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("هذا الأمر مخصص لبيئة التطوير المحلية فقط.")
        env_path = Path(settings.BASE_DIR) / ".env"
        if not env_path.exists():
            raise CommandError("ملف .env غير موجود. أنشئه من .env.example أولًا.")

        content = env_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        existing = self._parse_environment(lines)
        generated_names: list[str] = []

        for name in KEY_NAMES:
            if existing.get(name):
                continue
            encoded_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
            lines = self._set_value(lines, name, encoded_key)
            generated_names.append(name)

        if "PII_ENCRYPTION_KEY_VERSION" not in existing:
            lines.append("PII_ENCRYPTION_KEY_VERSION=v1")

        env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

        if generated_names:
            self.stdout.write(
                self.style.SUCCESS(
                    "تم إنشاء مفاتيح التطوير المحلية المطلوبة دون عرض قيمها."
                )
            )
        else:
            self.stdout.write("مفاتيح التطوير المطلوبة موجودة مسبقًا؛ لم تتغير قيمها.")

    @staticmethod
    def _parse_environment(lines: list[str]) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            name, value = stripped.split("=", 1)
            values[name.strip()] = value.strip()
        return values

    @staticmethod
    def _set_value(lines: list[str], name: str, value: str) -> list[str]:
        prefix = f"{name}="
        updated = list(lines)
        for index, line in enumerate(updated):
            if line.strip().startswith(prefix):
                updated[index] = f"{name}={value}"
                return updated
        updated.append(f"{name}={value}")
        return updated
