from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import (
    AuthenticationForm,
    BaseUserCreationForm,
    ReadOnlyPasswordHashField,
)
from django.core.exceptions import ValidationError
from django.db.models import Q

from organization.models import Department, Employee, UserDepartmentScope
from organization.services.identity import normalize_national_id

from .models import Permission, Role, User


class ArabicAuthenticationForm(AuthenticationForm):
    """Username/password authentication with Arabic, user-safe errors."""

    remember_me = forms.BooleanField(
        label="تذكرني",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    error_messages = {
        "invalid_login": "اسم المستخدم أو كلمة المرور غير صحيحة.",
        "inactive": "هذا الحساب غير نشط. تواصل مع مسؤول النظام.",
    }

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request=request, *args, **kwargs)
        self.fields["username"].label = "اسم المستخدم"
        self.fields["username"].widget.attrs.update(
            {
                "class": "form-control",
                "id": "username",
                "placeholder": "أدخل اسم المستخدم",
                "autocomplete": "username",
                "autofocus": True,
            }
        )
        self.fields["password"].label = "كلمة المرور"
        self.fields["password"].widget.attrs.update(
            {
                "class": "form-control",
                "id": "password",
                "placeholder": "أدخل كلمة المرور",
                "autocomplete": "current-password",
            }
        )

    def clean(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")

        if username is not None and password:
            self.user_cache = authenticate(
                self.request,
                username=username,
                password=password,
            )
            if self.user_cache is None:
                candidate = (
                    get_user_model()._default_manager.filter(
                        username__iexact=username
                    ).first()
                )
                if (
                    candidate is not None
                    and candidate.check_password(password)
                    and not candidate.is_active
                ):
                    raise ValidationError(
                        self.error_messages["inactive"],
                        code="inactive",
                    )
                raise ValidationError(
                    self.error_messages["invalid_login"],
                    code="invalid_login",
                )
            self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data


class NationalIdLoginForm(forms.Form):
    national_id = forms.CharField(
        label="السجل المدني",
        max_length=20,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-lg",
                "inputmode": "numeric",
                "autocomplete": "off",
                "placeholder": "أدخل السجل المدني",
                "dir": "ltr",
            }
        ),
    )

    def clean_national_id(self):
        try:
            return normalize_national_id(self.cleaned_data["national_id"])
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc


class UserCreationAdminForm(BaseUserCreationForm):
    class Meta:
        model = User
        fields = ("username", "email", "first_name", "last_name")


class UserChangeAdminForm(forms.ModelForm):
    password = ReadOnlyPasswordHashField(label="كلمة المرور")

    class Meta:
        model = User
        fields = "__all__"

    def clean_password(self):
        return self.initial["password"]


def _style_fields(form) -> None:
    for field in form.fields.values():
        if isinstance(field.widget, forms.CheckboxInput):
            field.widget.attrs.setdefault("class", "form-check-input")
        else:
            field.widget.attrs.setdefault("class", "form-control")


class UserAccessForm(forms.ModelForm):
    employee = forms.ModelChoiceField(
        label="الموظف المرتبط", queryset=Employee.objects.none(), required=False
    )
    roles = forms.ModelMultipleChoiceField(
        label="الأدوار",
        queryset=Role.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 6}),
    )
    departments = forms.ModelMultipleChoiceField(
        label="نطاق الأقسام",
        queryset=Department.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 8}),
    )
    access_level = forms.ChoiceField(
        label="مستوى الوصول", choices=UserDepartmentScope.AccessLevel.choices
    )
    include_descendants = forms.BooleanField(
        label="يشمل الأقسام الفرعية", required=False
    )
    password1 = forms.CharField(
        label="كلمة المرور",
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="مطلوبة للحساب الجديد، واتركها فارغة عند التعديل للإبقاء عليها.",
    )
    password2 = forms.CharField(
        label="تأكيد كلمة المرور",
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    class Meta:
        model = User
        fields = ("username", "email", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        employee_filter = Q(user__isnull=True)
        if not self.instance._state.adding:
            employee_filter |= Q(user_id=self.instance.pk)
        self.fields["employee"].queryset = Employee.objects.filter(
            archived_at__isnull=True
        ).filter(employee_filter).order_by("full_name_ar")
        self.fields["roles"].queryset = Role.objects.filter(is_active=True).order_by(
            "name_ar"
        )
        self.fields["departments"].queryset = Department.objects.filter(
            is_active=True, archived_at__isnull=True
        ).order_by("name_ar")
        if not self.instance._state.adding:
            self.fields["employee"].initial = getattr(self.instance, "employee", None)
            self.fields["roles"].initial = self.instance.role_assignments.filter(
                is_active=True, valid_to__isnull=True
            ).values_list("role_id", flat=True)
            scopes = self.instance.department_scopes.filter(valid_to__isnull=True)
            self.fields["departments"].initial = scopes.values_list(
                "department_id", flat=True
            )
            first_scope = scopes.first()
            if first_scope:
                self.fields["access_level"].initial = first_scope.access_level
                self.fields["include_descendants"].initial = (
                    first_scope.include_descendants
                )
        _style_fields(self)

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1") or ""
        password2 = cleaned.get("password2") or ""
        if self.instance._state.adding and not password1:
            self.add_error("password1", "كلمة المرور مطلوبة للحساب الجديد.")
        if password1 != password2:
            self.add_error("password2", "كلمتا المرور غير متطابقتين.")
        return cleaned


class RoleAccessForm(forms.ModelForm):
    MODULE_LABELS = {
        "employees": "الموظفون",
        "attendance": "الحضور والتقارير",
        "organization": "الهيكل التنظيمي",
        "accounts": "المستخدمون والصلاحيات",
        "audit": "سجل التدقيق",
        "violations": "المخالفات والمعالجات",
        "clarifications": "الإفادات والاعتمادات",
    }

    permissions = forms.ModelMultipleChoiceField(
        label="الصلاحيات",
        queryset=Permission.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Role
        fields = ("code", "name_ar", "description_ar", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["permissions"].queryset = Permission.objects.filter(
            is_active=True
        ).order_by("module", "name_ar")
        if not self.instance._state.adding:
            self.fields["permissions"].initial = self.instance.permission_assignments.filter(
                revoked_at__isnull=True
            ).values_list("permission_id", flat=True)
        _style_fields(self)

        selected_ids = {
            str(value) for value in (self["permissions"].value() or ())
        }
        grouped = {}
        for permission in self.fields["permissions"].queryset:
            permission.ui_selected = str(permission.id) in selected_ids
            grouped.setdefault(permission.module, []).append(permission)
        self.permission_groups = tuple(
            {
                "code": module,
                "label": self.MODULE_LABELS.get(module, module),
                "permissions": tuple(permissions),
            }
            for module, permissions in grouped.items()
        )
