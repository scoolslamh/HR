from django.contrib import admin

from .models import (
    CalculationRun,
    DailyAttendanceResult,
    ImportBatch,
    ImportError,
    ImportRow,
    RawAttendanceRecord,
)


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "display_name", "original_filename", "status", "archived_at", "period_start", "period_end", "daily_record_count", "created_at")
    list_filter = ("status", "archived_at", "created_at")
    search_fields = ("display_name", "original_filename", "source_period_title", "file_sha256")
    readonly_fields = tuple(field.name for field in ImportBatch._meta.fields)


@admin.register(ImportRow)
class ImportRowAdmin(admin.ModelAdmin):
    list_display = ("batch", "row_number", "matched_employee", "attendance_date", "match_status", "validation_status")
    list_filter = ("match_status", "validation_status", "location_match_status")
    search_fields = ("national_id_last4", "matched_employee__full_name_ar")
    readonly_fields = tuple(field.name for field in ImportRow._meta.fields)


@admin.register(ImportError)
class ImportErrorAdmin(admin.ModelAdmin):
    list_display = ("batch", "row", "severity", "error_code", "message_ar", "created_at")
    list_filter = ("severity", "error_code")
    readonly_fields = tuple(field.name for field in ImportError._meta.fields)


@admin.register(RawAttendanceRecord)
class RawAttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ("employee", "attendance_date", "source_check_in_at", "source_check_out_at", "location_match_status")
    list_filter = ("location_match_status", "attendance_date")
    search_fields = ("employee__full_name_ar", "national_id_hash")
    readonly_fields = tuple(field.name for field in RawAttendanceRecord._meta.fields)


@admin.register(CalculationRun)
class CalculationRunAdmin(admin.ModelAdmin):
    list_display = ("period_start", "period_end", "run_type", "status", "result_count", "created_at")
    list_filter = ("run_type", "status", "created_at")
    readonly_fields = tuple(field.name for field in CalculationRun._meta.fields)


@admin.register(DailyAttendanceResult)
class DailyAttendanceResultAdmin(admin.ModelAdmin):
    list_display = ("employee", "attendance_date", "attendance_status", "worked_minutes", "late_minutes", "location_status")
    list_filter = ("attendance_status", "location_status", "attendance_date", "is_current")
    search_fields = ("employee__full_name_ar", "check_in_location", "check_out_location")
    readonly_fields = tuple(field.name for field in DailyAttendanceResult._meta.fields)
