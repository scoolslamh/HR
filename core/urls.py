from django.contrib.auth.views import LogoutView
from django.urls import path

from .navigation import PLACEHOLDER_PAGES
from .views import DashboardView, PlaceholderView, PortalLoginView

app_name = "core"

urlpatterns = [
    path("", DashboardView.as_view(), name="dashboard"),
    path("login/", PortalLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
]

urlpatterns += [
    path(
        page["path"],
        PlaceholderView.as_view(page_config=page),
        name=page["name"],
    )
    for page in PLACEHOLDER_PAGES
]
