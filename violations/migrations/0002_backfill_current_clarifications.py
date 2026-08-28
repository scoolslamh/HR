from django.db import migrations


OUTSIDE_STATUSES = {
    "check_in_outside",
    "check_out_outside",
    "both_outside",
}


def backfill_current_clarifications(apps, schema_editor):
    DailyAttendanceResult = apps.get_model("attendance", "DailyAttendanceResult")
    ClarificationRequest = apps.get_model("violations", "ClarificationRequest")

    for result in DailyAttendanceResult.objects.filter(is_current=True).iterator():
        kinds = set()
        if result.attendance_status == "absent":
            kinds.add("absence")
        if result.location_status in OUTSIDE_STATUSES:
            kinds.add("outside_location")
        if "انصراف تلقائي" in (result.source_status or ""):
            kinds.add("automatic_checkout")

        for kind in kinds:
            ClarificationRequest.objects.update_or_create(
                employee_id=result.employee_id,
                attendance_date=result.attendance_date,
                kind=kind,
                defaults={
                    "attendance_result_id": result.id,
                    "department_id": result.department_id,
                    "status": "awaiting_employee",
                },
            )


class Migration(migrations.Migration):
    dependencies = [("violations", "0001_initial")]

    operations = [
        migrations.RunPython(backfill_current_clarifications, migrations.RunPython.noop)
    ]
