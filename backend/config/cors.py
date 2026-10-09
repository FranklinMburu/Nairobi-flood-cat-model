"""Allow the React dev server to call the API with credentials. Origins are an allowlist."""

from django.conf import settings
from django.http import HttpResponse


class CorsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        origin = request.META.get("HTTP_ORIGIN")
        allowed = bool(origin) and origin in settings.CORS_ALLOWED_ORIGINS
        if request.method == "OPTIONS" and allowed:
            response = HttpResponse(status=204)
            apply_cors(response, origin)
            return response
        response = self.get_response(request)
        if allowed:
            apply_cors(response, origin)
        return response


def apply_cors(response, origin):
    response["Access-Control-Allow-Origin"] = origin
    response["Access-Control-Allow-Credentials"] = "true"
    response["Access-Control-Allow-Headers"] = "Content-Type, X-CSRFToken"
    response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    vary = response.get("Vary")
    response["Vary"] = "Origin" if not vary else f"{vary}, Origin"
