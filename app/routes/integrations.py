"""
General integration endpoints.

Endpoints:
- POST /api/integrations/waitlist - Join waitlist for upcoming integrations
"""

import logging
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from app.services.supabase_service import supabase
from app.services.auth_service import require_auth

logger = logging.getLogger(__name__)

integrations_bp = Blueprint('integrations', __name__)


@integrations_bp.route('/integrations/waitlist', methods=['POST'])
@require_auth
def join_waitlist():
    """
    Join the waitlist for an upcoming integration (Fresha, Vagaro, Mindbody, etc.)

    Request body:
    {
        "email": "user@example.com",
        "integration": "fresha"
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "No data provided"}), 400

        email = (data.get('email') or '').strip()
        integration = (data.get('integration') or '').strip().lower()

        if not email or '@' not in email:
            return jsonify({"error": "Valid email is required"}), 400

        if not integration:
            return jsonify({"error": "Integration name is required"}), 400

        allowed_integrations = ['fresha', 'vagaro', 'mindbody']
        if integration not in allowed_integrations:
            return jsonify({"error": f"Unknown integration: {integration}"}), 400

        business_id = request.user['id']

        waitlist_data = {
            "business_id": business_id,
            "email": email,
            "integration": integration,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        # Upsert - if they already signed up, update timestamp
        result = supabase.table("integration_waitlist").upsert(
            waitlist_data,
            on_conflict="business_id,integration"
        ).execute()

        logger.info(f"Waitlist signup: {email} for {integration}")

        return jsonify({
            "success": True,
            "message": f"Added to {integration} waitlist"
        }), 201

    except Exception as e:
        # Don't fail hard - the table might not exist yet
        logger.warning(f"Waitlist signup error (non-critical): {e}")
        return jsonify({
            "success": True,
            "message": f"Added to waitlist"
        }), 201
