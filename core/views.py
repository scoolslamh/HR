from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.shortcuts import render
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from accounts.forms import ArabicAuthenticationForm
from .selectors import dashboard_context_for_user


class PortalLoginView(LoginView):
    template_name = "core/auth/login.html"
    authentication_form = ArabicAuthenticationForm
    redirect_authenticated_user = True
    extra_context = {"page_title": "تسجيل الدخول"}

    def form_valid(self, form):
        response = super().form_valid(form)
        self.request.session.pop("employee_portal_mode", None)
        if form.cleaned_data.get("remember_me"):
            self.request.session.set_expiry(settings.LOGIN_REMEMBER_SECONDS)
        else:
            self.request.session.set_expiry(0)
        return response

    def get_success_url(self):
        return str(reverse_lazy("core:dashboard"))


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "core/pages/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "page_title": "لوحة المعلومات",
                "page_description": "ملخص فعلي لمؤشرات الحضور والانضباط الوظيفي ضمن نطاقك التنظيمي.",
                "breadcrumb_items": ({"label": "لوحة المعلومات"},),
                "import_columns": ("الملف", "الفترة", "الحالة", "وقت الرفع"),
                "violation_columns": ("الموظف", "نوع المخالفة", "الحالة", "التاريخ"),
            }
        )
        context.update(dashboard_context_for_user(self.request.user))
        return context


class PlaceholderView(LoginRequiredMixin, TemplateView):
    template_name = "core/pages/placeholder.html"
    page_config = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page_config = self.page_config or {}
        context.update(
            {
                "page_title": page_config.get("title", "صفحة قيد الإعداد"),
                "page_description": page_config.get("description", ""),
                "breadcrumb_items": (
                    {"label": "الرئيسية", "url_name": "core:dashboard"},
                    {"label": page_config.get("title", "صفحة قيد الإعداد")},
                ),
            }
        )
        return context


def error_403(request, exception=None):
    return render(
        request,
        "core/errors/403.html",
        {"page_title": "غير مصرح بالوصول"},
        status=403,
    )


def error_404(request, exception=None):
    return render(
        request,
        "core/errors/404.html",
        {"page_title": "الصفحة غير موجودة"},
        status=404,
    )
