from django.contrib import admin

from .models import ClarificationEvidence, ClarificationRequest


@admin.register(ClarificationRequest)
class ClarificationRequestAdmin(admin.ModelAdmin):
    list_display = ("employee", "attendance_date", "kind", "status", "department")
    list_filter = ("kind", "status", "department")
    search_fields = ("employee__full_name_ar",)
    readonly_fields = (
        "employee",
        "department",
        "attendance_result",
        "attendance_date",
        "kind",
        "created_at",
        "updated_at",
    )


@admin.register(ClarificationEvidence)
class ClarificationEvidenceAdmin(admin.ModelAdmin):
    list_display = ("clarification", "original_filename", "file_size", "created_at")
    search_fields = ("clarification__employee__full_name_ar", "original_filename")
    readonly_fields = ("clarification", "file", "original_filename", "content_type", "file_size", "uploaded_by", "created_at")
