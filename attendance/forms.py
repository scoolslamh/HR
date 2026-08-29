from pathlib import Path

from django import forms
from django.conf import settings

from organization.models import Department
from attendance.services.report_export import REPORT_TYPES


def _maximum_upload_bytes() -> int:
    """Return the attendance upload limit without weakening service validation."""

    configured = getattr(
        settings,
        "ATTENDANCE_IMPORT_MAX_BYTES",
        getattr(settings, "EMPLOYEE_IMPORT_MAX_BYTES", 5 * 1024 * 1024),
    )
    try:
        maximum = int(configured)
    except (TypeError, ValueError):
        return 5 * 1024 * 1024
    return maximum if maximum > 0 else 5 * 1024 * 1024


class AttendanceImportUploadForm(forms.Form):
    workbook = forms.FileField(
        label="ملف تقرير الحضور",
        help_text="ملف Excel بصيغة xlsx فقط.",
        widget=forms.ClearableFileInput(
            attrs={
                "class": "form-control",
                "accept": (
                    ".xlsx,application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                "aria-describedby": "workbook-help",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        max_megabytes = _maximum_upload_bytes() / (1024 * 1024)
        self.fields["workbook"].help_text = (
            "ملف Excel بصيغة xlsx فقط، وبحد أقصى "
            f"{max_megabytes:g} ميجابايت."
        )

    def clean_workbook(self):
        workbook = self.cleaned_data["workbook"]
        if Path(workbook.name).suffix.lower() != ".xlsx":
            raise forms.ValidationError(
                "يُسمح برفع ملفات Excel بصيغة xlsx فقط."
            )

        maximum = _maximum_upload_bytes()
        if workbook.size > maximum:
            max_megabytes = maximum / (1024 * 1024)
            raise forms.ValidationError(
                f"حجم الملف يتجاوز الحد المسموح ({max_megabytes:g} ميجابايت)."
            )
        if workbook.size <= 0:
            raise forms.ValidationError("الملف المرفوع فارغ.")
        return workbook


class AttendanceImportApprovalForm(forms.Form):
    confirm_approval = forms.BooleanField(
        label="أؤكد مراجعة المعاينة وأرغب في اعتماد سجلات الحضور المطابقة.",
        error_messages={
            "required": "يجب تأكيد مراجعة المعاينة قبل الاعتماد."
        },
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )


class UnmatchedEmployeeResolutionForm(forms.Form):
    class Action:
        IGNORE = "ignore"
        ADD = "add"

    national_id_hash = forms.RegexField(regex=r"^[0-9a-f]{64}$", widget=forms.HiddenInput)
    action = forms.ChoiceField(
        choices=((Action.IGNORE, "تجاهل"), (Action.ADD, "إضافة إلى الموظفين")),
        widget=forms.HiddenInput,
    )
    department = forms.ModelChoiceField(
        label="القسم",
        queryset=Department.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].queryset = Department.objects.filter(is_active=True).order_by("name_ar")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("action") == self.Action.ADD:
            department = cleaned.get("department")
            if department is None:
                self.add_error("department", "اختر قسم الموظف قبل إضافته.")
        return cleaned


class AttendanceImportMetadataForm(forms.Form):
    display_name = forms.CharField(
        label="الاسم الظاهر للملف",
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    source_period_title = forms.CharField(
        label="عنوان الفترة",
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    reason = forms.CharField(
        label="سبب التعديل",
        min_length=5,
        max_length=1000,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )


class AttendanceImportArchiveForm(forms.Form):
    reason = forms.CharField(
        label="السبب",
        min_length=5,
        max_length=1000,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )


class AttendanceImportDeleteForm(forms.Form):
    confirmation = forms.CharField(
        label="تأكيد اسم الملف",
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
    )
    reason = forms.CharField(
        label="سبب الحذف",
        min_length=5,
        max_length=1000,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )

    def __init__(self, *args, expected_name: str, **kwargs):
        self.expected_name = expected_name
        super().__init__(*args, **kwargs)
        self.fields["confirmation"].help_text = f"اكتب: {expected_name}"

    def clean_confirmation(self):
        value = self.cleaned_data["confirmation"].strip()
        if value != self.expected_name:
            raise forms.ValidationError("اسم التأكيد لا يطابق اسم الملف.")
        return value


class ReportBuilderForm(forms.Form):
    report_type = forms.ChoiceField(
        label="نوع التقرير",
        choices=tuple(REPORT_TYPES.items()),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    department = forms.ModelChoiceField(
        label="القسم",
        queryset=Department.objects.none(),
        required=False,
        empty_label="جميع الأقسام المتاحة",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    date_from = forms.DateField(
        label="من تاريخ", required=False,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    date_to = forms.DateField(
        label="إلى تاريخ", required=False,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    limit = forms.TypedChoiceField(
        label="عدد النتائج",
        choices=((10, "10"), (20, "20"), (50, "50"), (100, "100")),
        coerce=int,
        initial=10,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    output_format = forms.ChoiceField(
        label="طريقة الإخراج",
        choices=(("preview", "معاينة"), ("xlsx", "Excel"), ("pdf", "PDF / طباعة")),
        initial="preview",
        widget=forms.RadioSelect,
    )

    def __init__(self, *args, departments=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].queryset = (
            departments if departments is not None else Department.objects.none()
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("date_from") and cleaned.get("date_to") and cleaned["date_from"] > cleaned["date_to"]:
            self.add_error("date_to", "تاريخ النهاية يجب ألا يسبق تاريخ البداية.")
        return cleaned
