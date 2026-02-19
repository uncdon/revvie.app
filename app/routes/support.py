"""
Support request tracking.

Endpoints:
- POST /api/support/track  - Log a support click (fire-and-forget)
- GET  /api/support/stats  - Admin: counts by type this month
"""

import logging
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify

from app.services.supabase_service import supabase_admin
from app.services.auth_service import require_auth
from app.routes.admin import is_admin

logger = logging.getLogger(__name__)

support_bp = Blueprint('support', __name__)

ALLOWED_TYPES = {'support', 'feature_request', 'bug'}


@support_bp.route('/support/track', methods=['POST'])
@require_auth
def track_support():
    """
    Log a support button click.

    Body: { "type": "support" | "feature_request" | "bug" }

    Always returns 200 — this is fire-and-forget analytics.
    """
    data = request.get_json() or {}
    req_type = data.get('type', 'support').strip().lower()

    if req_type not in ALLOWED_TYPES:
        req_type = 'support'

    business_id = request.business.get('id')

    try:
        supabase_admin.table('support_requests').insert({
            'business_id': business_id,
            'type': req_type,
            'created_at': datetime.now(timezone.utc).isoformat(),
        }).execute()
        logger.info(f"Support click tracked: type={req_type} business={business_id}")
    except Exception as e:
        logger.warning(f"Support tracking insert failed (non-critical): {e}")

    return jsonify({"ok": True}), 200


@support_bp.route('/support/stats', methods=['GET'])
@require_auth
def support_stats():
    """Admin: support request counts for this month, broken down by type."""
    if not is_admin(request.business):
        return jsonify({"error": "Admin access required"}), 403

    # First day of current month in UTC
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    try:
        result = supabase_admin.table('support_requests') \
            .select('type') \
            .gte('created_at', month_start) \
            .execute()

        counts = {'support': 0, 'feature_request': 0, 'bug': 0, 'total': 0}
        for row in (result.data or []):
            t = row.get('type', 'support')
            if t in counts:
                counts[t] += 1
            counts['total'] += 1

        return jsonify(counts), 200

    except Exception as e:
        logger.error(f"Support stats fetch failed: {e}")
        return jsonify({"error": "Failed to fetch stats"}), 500
