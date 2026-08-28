from django.shortcuts import redirect


class EmployeePortalModeMiddleware:
    """Prevent a national-ID session from inheriting administrative access."""

    ALLOWED_PATH_PREFIXES = (
        "/my/",
        "/clarifications/evidence/",
        "/logout/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.session.get("employee_portal_mode") and not request.path.startswith(
            self.ALLOWED_PATH_PREFIXES
        ):
            return redirect("violations:employee_portal")
        return self.get_response(request)
