from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.shortcuts import render
from django.shortcuts import redirect
from django.http import HttpRequest, HttpResponse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
import uuid
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from accounts.forms import ArabicAuthenticationForm
from .selectors import dashboard_context_for_user
from .periods import (
    ATTENDANCE_PERIOD_SESSION_KEY,
    available_attendance_periods,
    selected_attendance_period,
    user_can_select_attendance_period,
)


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
                "ranking_columns": ("الموظف", "عدد الأيام"),
            }
        )
        context.update(
            dashboard_context_for_user(
                self.request.user,
                attendance_period=selected_attendance_period(self.request),
            )
        )
        return context


@require_POST
def select_attendance_period(request: HttpRequest) -> HttpResponse:
    if not request.user.is_authenticated or not user_can_select_attendance_period(
        request.user
    ):
        return render(request, "core/errors/403.html", status=403)
    try:
        period_id = uuid.UUID(request.POST.get("attendance_period", ""))
    except ValueError:
        period_id = None
    period = (
        available_attendance_periods().filter(pk=period_id).first()
        if period_id
        else None
    )
    if period is not None:
        request.session[ATTENDANCE_PERIOD_SESSION_KEY] = str(period.id)
    target = request.POST.get("next") or "core:dashboard"
    if not url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        target = "core:dashboard"
    return redirect(target)


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
