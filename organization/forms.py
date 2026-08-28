from pathlib import Path

from django import forms
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .models import (
    Department,
    Employee,
    EmployeeImportBatch,
    JobTitle,
    Location,
    UserDepartmentScope,
)
from .selectors import (
    current_assignment_for,
    current_primary_location_for,
    department_ids_in_user_scope,
    employees_in_user_department_scope,
)
from .services.identity import normalize_national_id, normalize_saudi_mobile


def _style_fields(form) -> None:
    for field in form.fields.values():
        if isinstance(field.widget, forms.CheckboxInput):
            field.widget.attrs.setdefault("class", "form-check-input")
        else:
            field.widget.attrs.setdefault("class", "form-control")


class EmployeeImportUploadForm(forms.Form):
    workbook = forms.FileField(
        label="ملف بيانات الموظفين",
        help_text="ملف Excel بصيغة xlsx فقط.",
        widget=forms.ClearableFileInput(
            attrs={
                "class": "form-control",
                "accept": ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "aria-describedby": "workbook-help",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        max_megabytes = settings.EMPLOYEE_IMPORT_MAX_BYTES / (1024 * 1024)
        self.fields["workbook"].help_text = (
            f"ملف Excel بصيغة xlsx فقط، وبحد أقصى {max_megabytes:g} ميجابايت."
        )

    def clean_workbook(self):
        workbook = self.cleaned_data["workbook"]
        if Path(workbook.name).suffix.lower() != ".xlsx":
            raise forms.ValidationError("يُسمح برفع ملفات Excel بصيغة xlsx فقط.")

        max_bytes = settings.EMPLOYEE_IMPORT_MAX_BYTES
        if workbook.size > max_bytes:
            max_megabytes = max_bytes / (1024 * 1024)
            raise forms.ValidationError(
                f"حجم الملف يتجاوز الحد المسموح ({max_megabytes:g} ميجابايت)."
            )
        if workbook.size <= 0:
            raise forms.ValidationError("الملف المرفوع فارغ.")
        return workbook


class EmployeeImportApprovalForm(forms.Form):
    confirm_approval = forms.BooleanField(
        label="أؤكد مراجعة نتائج المعاينة وأرغب في اعتماد الدفعة.",
        error_messages={"required": "يجب تأكيد مراجعة نتائج المعاينة قبل الاعتماد."},
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    create_missing_references = forms.BooleanField(
        label="إنشاء الأقسام والمواقع غير الموجودة أثناء الاعتماد.",
        help_text=(
            "خيار صريح: لن ينشئ النظام أي قسم أو موقع جديد ما لم تحدده."
        ),
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, requires_reference_creation=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.requires_reference_creation = requires_reference_creation

    def clean_create_missing_references(self):
        create_missing_references = self.cleaned_data["create_missing_references"]
        if self.requires_reference_creation and not create_missing_references:
            raise forms.ValidationError(
                "تتضمن الدفعة أقسامًا أو مواقع غير موجودة؛ اختر إنشاءها لإتمام الاعتماد."
            )
        return create_missing_references


class ManualEmployeeCreateForm(forms.Form):
    national_id = forms.CharField(label="السجل المدني", max_length=20)
    full_name_ar = forms.CharField(label="اسم الموظف", max_length=250)
    employee_number = forms.CharField(label="الرقم الوظيفي", max_length=50, required=False)
    mobile = forms.CharField(label="رقم الجوال", max_length=30, required=False)
    department = forms.ModelChoiceField(label="القسم", queryset=Department.objects.none())
    location = forms.ModelChoiceField(label="مكان التوقيع", queryset=Location.objects.none())
    manager_employee = forms.ModelChoiceField(
        label="الرئيس المباشر", queryset=Employee.objects.none(), required=False
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].queryset = Department.objects.filter(
            is_active=True, archived_at__isnull=True
        ).order_by("name_ar")
        self.fields["location"].queryset = Location.objects.filter(
            is_active=True
        ).order_by("name_ar")
        self.fields["manager_employee"].queryset = Employee.objects.filter(
            employment_status=Employee.EmploymentStatus.ACTIVE,
            archived_at__isnull=True,
        ).order_by("full_name_ar")
        _style_fields(self)
        self.fields["national_id"].widget.attrs.update(
            {"inputmode": "numeric", "autocomplete": "off", "dir": "ltr"}
        )

    def clean_national_id(self):
        try:
            return normalize_national_id(self.cleaned_data["national_id"])
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc

    def clean_employee_number(self):
        return (self.cleaned_data.get("employee_number") or "").strip() or None

    def clean_mobile(self):
        value = (self.cleaned_data.get("mobile") or "").strip()
        if not value:
            return None
        try:
            return normalize_saudi_mobile(value)
        except ValueError as exc:
            raise forms.ValidationError("رقم الجوال السعودي غير صحيح.") from exc


class BulkEmployeeManagementForm(forms.Form):
    ACTIONS = (("archive", "أرشفة"), ("restore", "استعادة"))
    SCOPES = (("selected", "الموظفون المحددون"), ("batch", "دفعة استيراد"), ("all", "جميع الموظفين"))

    action = forms.ChoiceField(label="الإجراء", choices=ACTIONS)
    scope = forms.ChoiceField(label="النطاق", choices=SCOPES)
    employee_ids = forms.ModelMultipleChoiceField(
        label="الموظفون", queryset=Employee.objects.all(), required=False
    )
    batch = forms.ModelChoiceField(
        label="دفعة الاستيراد",
        queryset=EmployeeImportBatch.objects.filter(status=EmployeeImportBatch.Status.APPROVED),
        required=False,
    )
    confirmation = forms.CharField(label="عبارة التأكيد", max_length=100)
    reason = forms.CharField(label="السبب", min_length=5, max_length=1000)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style_fields(self)

    def clean(self):
        cleaned = super().clean()
        scope = cleaned.get("scope")
        action = cleaned.get("action")
        if scope == "selected" and not cleaned.get("employee_ids"):
            self.add_error("employee_ids", "حدد موظفًا واحدًا على الأقل.")
        if scope == "batch" and not cleaned.get("batch"):
            self.add_error("batch", "اختر دفعة استيراد معتمدة.")
        expected = {
            ("archive", "selected"): "أرشفة المحدد",
            ("restore", "selected"): "استعادة المحدد",
            ("archive", "batch"): "أرشفة موظفي الدفعة",
            ("restore", "batch"): "استعادة موظفي الدفعة",
            ("archive", "all"): "أرشفة جميع الموظفين",
            ("restore", "all"): "استعادة جميع الموظفين",
        }.get((action, scope))
        if expected and (cleaned.get("confirmation") or "").strip() != expected:
            self.add_error("confirmation", f"اكتب العبارة التالية كما هي: {expected}")
        return cleaned


class EmployeeDirectoryFilterForm(forms.Form):
    search = forms.CharField(label="بحث", required=False, max_length=250)
    department = forms.ModelChoiceField(
        label="القسم", required=False, queryset=Department.objects.none()
    )
    location = forms.ModelChoiceField(
        label="الموقع", required=False, queryset=Location.objects.none()
    )
    status = forms.ChoiceField(
        label="الحالة",
        required=False,
        choices=(("", "جميع الحالات"), *Employee.EmploymentStatus.choices),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        department_ids = department_ids_in_user_scope(user)
        self.fields["department"].queryset = Department.objects.filter(
            id__in=department_ids
        ).order_by("name_ar")
        self.fields["location"].queryset = Location.objects.filter(
            employee_assignments__employee__in=employees_in_user_department_scope(user)
        ).distinct().order_by("name_ar")
        _style_fields(self)
        self.fields["search"].widget.attrs["placeholder"] = "الاسم أو الرقم الوظيفي"


class EmployeeEditForm(forms.ModelForm):
    mobile = forms.CharField(
        label="رقم الجوال",
        required=False,
        help_text="اتركه فارغًا للإبقاء على الرقم المقنّع الحالي.",
    )
    department = forms.ModelChoiceField(
        label="القسم", queryset=Department.objects.none()
    )
    location = forms.ModelChoiceField(
        label="الموقع الأساسي", queryset=Location.objects.none()
    )
    location_effective_date = forms.DateField(
        label="تاريخ سريان مقر التوقيع",
        help_text="ستُعاد مطابقة سجلات الحضور من هذا التاريخ عند تغيير الموقع.",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    manager_employee = forms.ModelChoiceField(
        label="المدير المباشر",
        queryset=Employee.objects.none(),
        required=False,
    )

    class Meta:
        model = Employee
        fields = (
            "full_name_ar",
            "employee_number",
            "mobile",
            "department",
            "location",
            "location_effective_date",
            "manager_employee",
            "employment_status",
        )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        access_levels = (
            UserDepartmentScope.AccessLevel.MANAGE,
            UserDepartmentScope.AccessLevel.APPROVE,
        )
        department_ids = department_ids_in_user_scope(
            user, access_levels=None if user.is_superuser else access_levels
        )
        self.fields["department"].queryset = Department.objects.filter(
            id__in=department_ids,
            is_active=True,
            archived_at__isnull=True,
        ).order_by("name_ar")
        self.fields["location"].queryset = Location.objects.filter(
            is_active=True
        ).order_by("name_ar")
        department_head_ids = self.fields["department"].queryset.exclude(
            department_head_id__isnull=True
        ).values_list("department_head_id", flat=True)
        scoped_employee_ids = employees_in_user_department_scope(user).values_list(
            "id", flat=True
        )
        manager_candidates = Employee.objects.filter(
            Q(id__in=scoped_employee_ids) | Q(id__in=department_head_ids)
        )
        self.fields["manager_employee"].queryset = (
            manager_candidates.exclude(pk=self.instance.pk)
            .distinct()
            .order_by("full_name_ar")
        )
        assignment = current_assignment_for(self.instance)
        primary_location = current_primary_location_for(self.instance)
        if assignment:
            self.fields["department"].initial = assignment.department_id
            self.fields["manager_employee"].initial = assignment.manager_employee_id
        if primary_location:
            self.fields["location"].initial = primary_location.location_id
        self.fields["location_effective_date"].initial = timezone.localdate()
        self.fields["location_effective_date"].widget.attrs["max"] = (
            timezone.localdate().isoformat()
        )
        earliest_location_date = (
            self.instance.primary_location_assignments.order_by("valid_from")
            .values_list("valid_from", flat=True)
            .first()
        )
        if earliest_location_date:
            self.fields["location_effective_date"].widget.attrs["min"] = (
                earliest_location_date.isoformat()
            )
        _style_fields(self)

    def clean_employee_number(self):
        value = (self.cleaned_data.get("employee_number") or "").strip()
        return value or None

    def clean_mobile(self):
        value = (self.cleaned_data.get("mobile") or "").strip()
        if not value:
            return None
        try:
            return normalize_saudi_mobile(value)
        except ValueError as exc:
            raise forms.ValidationError("رقم الجوال السعودي غير صحيح.") from exc

    def clean(self):
        cleaned = super().clean()
        location = cleaned.get("location")
        manager = cleaned.get("manager_employee")
        location_effective_date = cleaned.get("location_effective_date")
        primary_location = current_primary_location_for(self.instance)
        earliest_location_date = (
            self.instance.primary_location_assignments.order_by("valid_from")
            .values_list("valid_from", flat=True)
            .first()
        )
        if manager and manager.pk == self.instance.pk:
            self.add_error("manager_employee", "لا يمكن أن يكون الموظف مديرًا لنفسه.")
        if location_effective_date:
            if location_effective_date > timezone.localdate():
                self.add_error(
                    "location_effective_date",
                    "لا يمكن أن يكون تاريخ سريان مقر التوقيع في المستقبل.",
                )
            elif earliest_location_date and location_effective_date < earliest_location_date:
                self.add_error(
                    "location_effective_date",
                    "تاريخ السريان يسبق أول إسناد مسجل لمقر توقيع الموظف.",
                )
            elif (
                primary_location
                and location_effective_date < primary_location.valid_from
                and location
                and location.id != primary_location.location_id
            ):
                self.add_error(
                    "location_effective_date",
                    "لا يمكن تقديم تاريخ موقع مختلف قبل بداية إسناد الموقع الحالي.",
                )
            elif (
                primary_location
                and location_effective_date < primary_location.valid_from
                and self.instance.primary_location_assignments.filter(
                    valid_from=location_effective_date
                ).exists()
            ):
                self.add_error(
                    "location_effective_date",
                    "اختر تاريخًا بعد بداية إسناد الموقع السابق للحفاظ على سجله التاريخي.",
                )
        return cleaned


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = (
            "code",
            "name_ar",
            "unit_type",
            "parent",
            "signing_location",
            "department_head",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parent"].queryset = Department.objects.filter(
            is_active=True, archived_at__isnull=True
        ).exclude(pk=self.instance.pk)
        self.fields["signing_location"].queryset = Location.objects.filter(
            is_active=True
        ).order_by("name_ar")
        self.fields["department_head"].queryset = Employee.objects.filter(
            employment_status=Employee.EmploymentStatus.ACTIVE,
            archived_at__isnull=True,
        ).order_by("full_name_ar")
        self.fields["signing_location"].required = False
        self.fields["department_head"].required = False
        _style_fields(self)

    def save(self, commit=True):
        department = super().save(commit=False)
        if not department.valid_from:
            department.valid_from = timezone.localdate()
        if commit:
            department.save()
            self.save_m2m()
        return department

    def clean_parent(self):
        parent = self.cleaned_data.get("parent")
        cursor = parent
        while cursor:
            if cursor.pk == self.instance.pk:
                raise forms.ValidationError("لا يمكن إنشاء دورة في الهيكل التنظيمي.")
            cursor = cursor.parent
        return parent


class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = (
            "code",
            "name_ar",
            "location_type",
            "department",
            "address_ar",
            "latitude",
            "longitude",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].queryset = Department.objects.filter(
            is_active=True, archived_at__isnull=True
        ).order_by("name_ar")
        _style_fields(self)


class JobTitleForm(forms.ModelForm):
    class Meta:
        model = JobTitle
        fields = ("code", "name_ar")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style_fields(self)
