"""
Business API endpoints - PROTECTED ROUTES.

All routes here require authentication (valid JWT token).

Endpoints:
- GET  /api/business             - Get business details
- PUT  /api/business             - Update business details
- PUT  /api/business/profile     - Update individual profile fields
- GET  /api/business/settings    - Get business settings (cooldown, etc.)
- PUT  /api/business/settings    - Update business settings
- PUT  /api/business/preferences - Update email notification preferences
"""

import logging
from flask import Blueprint, jsonify, request
from app.services.supabase_service import supabase
from app.services.auth_service import require_auth

logger = logging.getLogger(__name__)

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


@businesses_bp.route('/business/profile', methods=['PUT'])
@require_auth
def update_profile():
    """
    Update a single profile field (business_name, email, or phone).

    Request body:
    {
        "business_name": "New Name"
    }
    or
    {
        "email": "new@email.com"
    }
    or
    {
        "phone": "+15551234567"
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "No data provided"}), 400

        allowed_fields = ['business_name', 'email', 'phone', 'review_request_cooldown_days']
        update_data = {k: v.strip() if isinstance(v, str) else v
                       for k, v in data.items() if k in allowed_fields}

        # Validate cooldown value
        if 'review_request_cooldown_days' in update_data:
            try:
                cooldown = int(update_data['review_request_cooldown_days'])
                if cooldown < 0 or cooldown > 90:
                    return jsonify({"error": "Cooldown days must be between 0 and 90"}), 400
                update_data['review_request_cooldown_days'] = cooldown
            except (ValueError, TypeError):
                return jsonify({"error": "Cooldown days must be a number"}), 400

        if not update_data:
            return jsonify({"error": "No valid fields to update"}), 400

        # Validate non-empty values
        for key, val in update_data.items():
            if not val and key not in ('phone', 'review_request_cooldown_days'):  # phone can be cleared, cooldown can be 0
                return jsonify({"error": f"{key} cannot be empty"}), 400

        business_id = request.user['id']

        # If email is being changed, update Supabase Auth email too
        if 'email' in update_data:
            try:
                token = request.headers.get('Authorization').split()[1]
                supabase.auth._headers = {"Authorization": f"Bearer {token}"}
                supabase.auth.update_user({"email": update_data['email']})
            except Exception as e:
                logger.warning(f"Could not update auth email: {e}")
                # Continue with business table update even if auth email fails

        response = supabase.table("businesses").update(update_data).eq("id", business_id).execute()

        if not response.data:
            return jsonify({"error": "Business not found"}), 404

        return jsonify({
            "success": True,
            "message": "Profile updated",
            "data": response.data[0]
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@businesses_bp.route('/business/settings', methods=['GET'])
@require_auth
def get_settings():
    """
    Get current business settings.

    Returns review_request_cooldown_days and basic business info.
    """
    try:
        business_id = request.user['id']

        result = supabase.table("businesses") \
            .select("review_request_cooldown_days, business_name, email") \
            .eq("id", business_id) \
            .limit(1) \
            .execute()

        if not result.data:
            return jsonify({"error": "Business not found"}), 404

        biz = result.data[0]

        return jsonify({
            "review_request_cooldown_days": biz.get("review_request_cooldown_days") if biz.get("review_request_cooldown_days") is not None else 30,
            "business_name": biz.get("business_name", ""),
            "email": biz.get("email", ""),
        }), 200

    except Exception as e:
        logger.error(f"Error fetching business settings: {e}")
        return jsonify({"error": "Failed to load settings"}), 500


@businesses_bp.route('/business/settings', methods=['PUT'])
@require_auth
def update_settings():
    """
    Update business settings.

    Request body:
    {
        "review_request_cooldown_days": 30
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "No data provided"}), 400

        update_data = {}

        if "review_request_cooldown_days" in data:
            try:
                cooldown = int(data["review_request_cooldown_days"])
            except (ValueError, TypeError):
                return jsonify({"error": "Cooldown days must be a number"}), 400

            if cooldown < 0 or cooldown > 90:
                return jsonify({"error": "Cooldown days must be between 0 and 90"}), 400

            update_data["review_request_cooldown_days"] = cooldown

        if not update_data:
            return jsonify({"error": "No valid settings to update"}), 400

        business_id = request.user['id']

        response = supabase.table("businesses") \
            .update(update_data) \
            .eq("id", business_id) \
            .execute()

        if not response.data:
            return jsonify({"error": "Business not found"}), 404

        cooldown_val = response.data[0].get("review_request_cooldown_days", 30)

        return jsonify({
            "success": True,
            "cooldown_days": cooldown_val,
            "message": "Settings updated successfully"
        }), 200

    except Exception as e:
        logger.error(f"Error updating business settings: {e}")
        return jsonify({"error": "Failed to save settings"}), 500


@businesses_bp.route('/business/preferences', methods=['PUT'])
@require_auth
def update_preferences():
    """
    Update email notification preferences.

    Request body:
    {
        "weekly_summary": true,
        "click_notifications": true,
        "referral_notifications": false
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "No data provided"}), 400

        allowed_fields = ['weekly_summary', 'click_notifications', 'referral_notifications']
        prefs = {k: bool(v) for k, v in data.items() if k in allowed_fields}

        if not prefs:
            return jsonify({"error": "No valid preferences provided"}), 400

        business_id = request.user['id']

        # Store preferences as JSON in the preferences column
        # First get existing preferences to merge
        biz = supabase.table("businesses").select("preferences").eq("id", business_id).execute()
        existing_prefs = {}
        if biz.data and biz.data[0].get('preferences'):
            existing_prefs = biz.data[0]['preferences']

        merged = {**existing_prefs, **prefs}

        response = supabase.table("businesses").update(
            {"preferences": merged}
        ).eq("id", business_id).execute()

        if not response.data:
            return jsonify({"error": "Business not found"}), 404

        return jsonify({
            "success": True,
            "message": "Preferences saved",
            "data": merged
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@businesses_bp.route('/business/usage', methods=['GET'])
@require_auth
def get_usage():
    """
    Get current month's SMS/email usage stats.

    Returns usage counts, caps, remaining, percentage, and reset date.
    """
    try:
        from app.services import usage_tracker
        business_id = request.user['id']
        stats = usage_tracker.get_usage_stats(business_id)
        return jsonify(stats), 200
    except Exception as e:
        logger.error(f"Error fetching usage stats: {e}")
        return jsonify({"error": "Failed to load usage stats"}), 500
