"""
Business API endpoints - PROTECTED ROUTES EXAMPLE.

All routes here require authentication (valid JWT token).
This shows how to use the @require_auth decorator.
"""

from flask import Blueprint, jsonify, request
from app.services.supabase_service import supabase
from app.services.auth_service import require_auth

businesses_bp = Blueprint('businesses', __name__)


@businesses_bp.route('/business', methods=['GET'])
@require_auth  # <-- This decorator requires a valid JWT token
def get_my_business():
    """
    Get the current user's business details.

    This is a PROTECTED route - requires Authorization header.

    Headers:
        Authorization: Bearer <access_token>

    The @require_auth decorator:
    1. Checks for Authorization header
    2. Validates the JWT token
    3. Attaches user/business info to request object
    4. Returns 401 if token is missing or invalid
    """
    # request.business is set by @require_auth decorator
    return jsonify(request.business), 200


@businesses_bp.route('/business', methods=['PUT'])
@require_auth
def update_my_business():
    """
    Update the current user's business details.

    Headers:
        Authorization: Bearer <access_token>

    Request body (all fields optional):
    {
        "business_name": "New Name",
        "phone": "555-1234",
        "google_review_url": "https://g.page/..."
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "No data provided"}), 400

        # Only allow updating these fields
        allowed_fields = ['business_name', 'phone', 'google_review_url']
        update_data = {k: v for k, v in data.items() if k in allowed_fields}

        if not update_data:
            return jsonify({"error": "No valid fields to update"}), 400

        # Update the business (request.user.id comes from @require_auth)
        response = supabase.table("businesses").update(update_data).eq("id", request.user['id']).execute()

        if not response.data:
            return jsonify({"error": "Business not found"}), 404

        return jsonify(response.data[0]), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
