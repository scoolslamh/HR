from django.core.management.base import BaseCommand, CommandError

from attendance.models import ImportBatch
from attendance.services.calculation import (
    AttendanceCalculationError,
    calculate_all,
    calculate_batch,
)


class Command(BaseCommand):
    help = "احتساب النتائج اليومية من سجلات الحضور الخام"

    def add_arguments(self, parser):
        parser.add_argument("--batch", help="UUID لدفعة حضور معتمدة")
        parser.add_argument("--all", action="store_true", help="احتساب جميع السجلات الخام")

    def handle(self, *args, **options):
        if bool(options.get("batch")) == bool(options.get("all")):
            raise CommandError("استخدم أحد الخيارين فقط: --batch أو --all")
        try:
            if options["batch"]:
                batch = ImportBatch.objects.get(
                    pk=options["batch"],
                    status=ImportBatch.Status.APPROVED,
                    archived_at__isnull=True,
                )
                summary = calculate_batch(batch)
            else:
                summary = calculate_all()
        except ImportBatch.DoesNotExist as exc:
            raise CommandError("لم يتم العثور على دفعة حضور معتمدة بهذا المعرف.") from exc
        except AttendanceCalculationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"تم إنشاء {summary.created} نتيجة يومية بنجاح."))
