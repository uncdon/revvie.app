"""
Health check endpoint for monitoring the API status.
Used by load balancers, container orchestration, and monitoring tools.
"""

import os
from flask import Blueprint, jsonify

# Create a Blueprint - a way to organize related routes
health_bp = Blueprint('health', __name__)


@health_bp.route('/health', methods=['GET'])
def health_check():
    """
    Health check endpoint.

    Returns:
        JSON response with status "ok" and HTTP 200
    """
    return jsonify({
        'status': 'ok',
        'message': 'Revvie API is running'
    }), 200


@health_bp.route('/health/auth-debug', methods=['GET'])
def auth_debug():
    """Debug endpoint to check auth flow."""
    from flask import request as flask_request
    from app.services.supabase_service import supabase

    auth_header = flask_request.headers.get('Authorization')
    if not auth_header:
        return jsonify({"error": "No auth header", "hint": "Send Authorization: Bearer <token>"}), 401

    try:
        parts = auth_header.split()
        token = parts[1] if len(parts) == 2 else auth_header

        # Get user from token
        user_response = supabase.auth.get_user(token)
        if not user_response.user:
            return jsonify({"error": "Invalid token", "user_response": str(user_response)}), 401

        user_id = user_response.user.id
        user_email = user_response.user.email

        # Query business
        business_response = supabase.table("businesses").select("*").eq("id", user_id).execute()

        return jsonify({
            "user_id": user_id,
            "user_email": user_email,
            "business_query_id": user_id,
            "business_found": len(business_response.data) > 0 if business_response.data else False,
            "business_count": len(business_response.data) if business_response.data else 0,
            "business_data": business_response.data[0] if business_response.data else None
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@health_bp.route('/health/services', methods=['GET'])
def services_status():
    """
    Check status of all external services.
    Useful for debugging configuration issues.
    """
    status = {
        'supabase': {
            'url_set': bool(os.environ.get('SUPABASE_URL')),
            'key_set': bool(os.environ.get('SUPABASE_KEY')),
        },
        'telnyx': {
            'api_key_set': bool(os.environ.get('TELNYX_API_KEY')),
            'phone_set': bool(os.environ.get('TELNYX_PHONE_NUMBER')),
            'public_key_set': bool(os.environ.get('TELNYX_PUBLIC_KEY')),
        },
        'sendgrid': {
            'api_key_set': bool(os.environ.get('SENDGRID_API_KEY')),
            'from_email_set': bool(os.environ.get('SENDGRID_FROM_EMAIL')),
        },
        'square': {
            'app_id_set': bool(os.environ.get('SQUARE_PRODUCTION_APP_ID') or os.environ.get('SQUARE_SANDBOX_APP_ID')),
        }
    }

    # Check if Telnyx SDK initialized properly
    try:
        from app.services.sms_service import telnyx_configured, get_sms_status
        status['telnyx']['sdk_configured'] = telnyx_configured
        status['telnyx']['details'] = get_sms_status()
    except Exception as e:
        status['telnyx']['sdk_error'] = str(e)

    return jsonify(status), 200
