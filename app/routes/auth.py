"""
Authentication API endpoints.

Endpoints:
- POST /api/auth/signup  - Create new account
- POST /api/auth/login   - Login to existing account
- GET  /api/auth/me      - Get current user (requires token)
"""

from flask import Blueprint, jsonify, request
from app.services.auth_service import signup_user, login_user, get_current_user, require_auth

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/auth/signup', methods=['POST'])
def signup():
    """
    Create a new user account and business.

    Request body:
    {
        "email": "owner@business.com",
        "password": "securepassword123",
        "business_name": "Joe's Coffee Shop"
    }

    Response:
    {
        "message": "Account created successfully",
        "user": {"id": "...", "email": "..."},
        "business": {"id": "...", "business_name": "..."},
        "session": {"access_token": "...", "refresh_token": "..."}
    }
    """
    try:
        # Get JSON data from request
        data = request.get_json()

        # Validate required fields
        if not data:
            return jsonify({"error": "No data provided"}), 400

        email = data.get('email')
        password = data.get('password')
        business_name = data.get('business_name')

        if not email:
            return jsonify({"error": "Email is required"}), 400
        if not password:
            return jsonify({"error": "Password is required"}), 400
        if not business_name:
            return jsonify({"error": "Business name is required"}), 400

        if len(password) < 6:
            return jsonify({"error": "Password must be at least 6 characters"}), 400

        # Create the account
        result = signup_user(email, password, business_name)

        return jsonify({
            "message": "Account created successfully",
            **result
        }), 201

    except Exception as e:
        error_message = str(e)

        # Handle common errors with friendly messages
        if "already registered" in error_message.lower():
            return jsonify({"error": "An account with this email already exists"}), 409

        return jsonify({"error": error_message}), 500


@auth_bp.route('/auth/login', methods=['POST'])
def login():
    """
    Login to an existing account.

    Request body:
    {
        "email": "owner@business.com",
        "password": "securepassword123"
    }

    Response:
    {
        "message": "Login successful",
        "user": {"id": "...", "email": "..."},
        "session": {"access_token": "...", "refresh_token": "...", "expires_in": 3600}
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "No data provided"}), 400

        email = data.get('email')
        password = data.get('password')

        if not email:
            return jsonify({"error": "Email is required"}), 400
        if not password:
            return jsonify({"error": "Password is required"}), 400

        # Authenticate
        result = login_user(email, password)

        return jsonify({
            "message": "Login successful",
            **result
        }), 200

    except Exception as e:
        error_message = str(e)

        if "invalid" in error_message.lower():
            return jsonify({"error": "Invalid email or password"}), 401

        return jsonify({"error": error_message}), 500


@auth_bp.route('/auth/me', methods=['GET'])
@require_auth  # This decorator requires a valid JWT token
def get_me():
    """
    Get the current authenticated user's info.

    Headers required:
        Authorization: Bearer <access_token>

    Response:
    {
        "user": {"id": "...", "email": "..."},
        "business": {"id": "...", "business_name": "...", ...}
    }
    """
    # request.user and request.business are set by @require_auth decorator
    return jsonify({
        "user": request.user,
        "business": request.business
    }), 200
