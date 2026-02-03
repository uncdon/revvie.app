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
