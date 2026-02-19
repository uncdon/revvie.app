from functools import wraps
from flask import request, jsonify


def require_verified_email(f):
    """
    Decorator to require a verified email address.

    Must be applied AFTER @require_auth, which sets request.business:

        @route(...)
        @require_auth
        @require_verified_email
        def my_route():
            ...
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        business = getattr(request, 'business', None)

        if not business:
            return jsonify({"error": "Unauthorized"}), 401

        if not business.get('email_verified', False):
            return jsonify({
                "error": "Email not verified",
                "message": "Please verify your email to access this feature",
                "redirect": "/verify-email-sent"
            }), 403

        return f(*args, **kwargs)

    return decorated_function
