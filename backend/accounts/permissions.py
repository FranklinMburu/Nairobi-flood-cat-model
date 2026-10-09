from functools import wraps

from django.http import JsonResponse


def admin_required(view):
    """JSON API guard. Staff status is checked on the server for every call."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return JsonResponse({"error": "Authentication required."}, status=401)
        if not user.is_staff:
            return JsonResponse({"error": "Administrator access required."}, status=403)
        return view(request, *args, **kwargs)

    return wrapped
