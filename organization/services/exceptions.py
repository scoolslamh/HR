from __future__ import annotations


class EmployeeImportServiceError(Exception):
    """Base exception whose Arabic message is safe to show to an end user."""

    default_message_ar = "تعذر إكمال عملية استيراد الموظفين."

    def __init__(self, message_ar: str | None = None, *, code: str = "import_error"):
        self.message_ar = message_ar or self.default_message_ar
        self.code = code
        super().__init__(self.message_ar)


class SecurityConfigurationError(EmployeeImportServiceError):
    default_message_ar = (
        "إعدادات حماية البيانات غير مكتملة. تواصل مع مسؤول النظام."
    )


class ImportFileValidationError(EmployeeImportServiceError):
    default_message_ar = "ملف الاستيراد غير صالح أو غير مدعوم."


class DuplicateImportFileError(EmployeeImportServiceError):
    default_message_ar = "سبق رفع هذا الملف، ولا يمكن استيراده مرة أخرى."


class ImportPreviewError(EmployeeImportServiceError):
    default_message_ar = "تعذر تجهيز معاينة ملف الموظفين."


class ImportApprovalError(EmployeeImportServiceError):
    default_message_ar = "تعذر اعتماد دفعة استيراد الموظفين."


class ImportDeletionError(EmployeeImportServiceError):
    default_message_ar = "تعذر حذف دفعة استيراد الموظفين."
