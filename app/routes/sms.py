"""
SMS API endpoints.

Endpoints:
- POST /api/sms/send - Send an SMS message
"""

from flask import Blueprint, jsonify, request
from app.services.sms_service import send_sms
from app.services.auth_service import require_auth

sms_bp = Blueprint('sms', __name__)


@sms_bp.route('/sms/send', methods=['POST'])
@require_auth  # Require authentication to prevent abuse
def send_sms_endpoint():
    """
    Send an SMS message.

    Headers:
        Authorization: Bearer <token>

    Request body:
    {
        "recipient_phone": "+14155551234",
        "message": "Hello from Revvie!"
    }

    Response (success):
    {
        "success": true,
        "message_sid": "SM1234567890abcdef",
        "status": "queued"
    }

    Response (error):
    {
        "success": false,
        "error": "Error description"
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({
                'success': False,
                'error': 'No data provided'
            }), 400

        recipient_phone = data.get('recipient_phone')
        message = data.get('message')

        # Validate required fields
        if not recipient_phone:
            return jsonify({
                'success': False,
                'error': 'recipient_phone is required'
            }), 400

        if not message:
            return jsonify({
                'success': False,
                'error': 'message is required'
            }), 400

        # Send the SMS
        result = send_sms(recipient_phone, message)

        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 400

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
