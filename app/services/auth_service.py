"""
Authentication Service - handles user signup, login, and token verification.

HOW JWT AUTHENTICATION WORKS:
============================
1. User signs up or logs in with email/password
2. Supabase verifies credentials and returns a JWT (JSON Web Token)
3. The JWT is a long encoded string that contains:
   - User ID
   - Email
   - Expiration time
   - Other metadata
4. Frontend stores this token (usually in localStorage)
5. For protected routes, frontend sends token in the header:
   Authorization: Bearer <token>
6. Backend verifies the token is valid before allowing access

JWT Structure (3 parts separated by dots):
   xxxxx.yyyyy.zzzzz
   ^header ^payload ^signature

The token is signed by Supabase's secret key, so we can trust it's authentic.
"""

from functools import wraps
from flask import request, jsonify
from app.services.supabase_service import supabase


def signup_user(email: str, password: str, business_name: str) -> dict:
    """
    Create a new user account and their business.

    Steps:
    1. Create user in Supabase Auth
    2. Create business record linked to that user
    3. Return the session (contains JWT token)
    """
    # Step 1: Create user in Supabase Auth
    auth_response = supabase.auth.sign_up({
        "email": email,
        "password": password
    })

    # Check if signup was successful
    if not auth_response.user:
        raise Exception("Failed to create user account")

    user_id = auth_response.user.id

    # Step 2: Create business record linked to this user
    business_data = {
        "id": user_id,  # Use same ID as auth user for easy linking
        "email": email,
        "business_name": business_name
    }

    business_response = supabase.table("businesses").insert(business_data).execute()

    if not business_response.data:
        raise Exception("Failed to create business record")

    # Step 3: Return success with token
    return {
        "user": {
            "id": user_id,
            "email": email
        },
        "business": business_response.data[0],
        "session": {
            "access_token": auth_response.session.access_token if auth_response.session else None,
            "refresh_token": auth_response.session.refresh_token if auth_response.session else None
        }
    }


def login_user(email: str, password: str) -> dict:
    """
    Authenticate a user and return their session.
    """
    auth_response = supabase.auth.sign_in_with_password({
        "email": email,
        "password": password
    })

    if not auth_response.user:
        raise Exception("Invalid email or password")

    return {
        "user": {
            "id": auth_response.user.id,
            "email": auth_response.user.email
        },
        "session": {
            "access_token": auth_response.session.access_token,
            "refresh_token": auth_response.session.refresh_token,
            "expires_in": auth_response.session.expires_in
        }
    }


def get_current_user(access_token: str) -> dict:
    """
    Verify a JWT token and return the user's info.
    """
    import logging
    logger = logging.getLogger(__name__)

    # Verify token with Supabase
    user_response = supabase.auth.get_user(access_token)

    if not user_response.user:
        raise Exception("Invalid or expired token")

    user_id = user_response.user.id
    logger.info(f"Auth: Looking up business for user_id={user_id}")

    # Get the user's business details
    business_response = supabase.table("businesses").select("*").eq("id", user_id).execute()

    logger.info(f"Auth: Business query returned {len(business_response.data) if business_response.data else 0} records")
    if business_response.data:
        logger.info(f"Auth: Found business: {business_response.data[0].get('business_name')}")
    else:
        logger.warning(f"Auth: No business found for user_id={user_id}")

    return {
        "user": {
            "id": user_id,
            "email": user_response.user.email
        },
        "business": business_response.data[0] if business_response.data else None
    }


def require_auth(f):
    """
    Decorator to protect routes - requires valid JWT token.

    Usage:
        @app.route('/protected')
        @require_auth
        def protected_route():
            # request.user is available here
            return jsonify({"user": request.user})

    The frontend must send the token in the Authorization header:
        Authorization: Bearer <token>
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Get the Authorization header
        auth_header = request.headers.get('Authorization')

        if not auth_header:
            return jsonify({"error": "Missing Authorization header"}), 401

        # Check format: "Bearer <token>"
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            return jsonify({"error": "Invalid Authorization header format. Use: Bearer <token>"}), 401

        token = parts[1]

        try:
            # Verify token and get user
            user_data = get_current_user(token)
            # Attach user info to request object so route can access it
            request.user = user_data['user']
            request.business = user_data['business']
        except Exception as e:
            return jsonify({"error": "Invalid or expired token"}), 401

        return f(*args, **kwargs)

    return decorated_function


def is_admin(business_or_email) -> bool:
    """
    Check if a business/email is an admin.

    Args:
        business_or_email: Either a business dict with 'email' key,
                          or an email string

    Returns:
        bool: True if admin, False otherwise
    """
    import os

    admin_email = os.getenv('ADMIN_EMAIL', 'daniel@revvie.app')

    if business_or_email is None:
        return False

    if isinstance(business_or_email, dict):
        email = business_or_email.get('email', '')
    else:
        email = str(business_or_email)

    return email.lower() == admin_email.lower()
