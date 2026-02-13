"""
Analytics API endpoints for link tracking data.

Endpoints:
- GET /api/analytics/review-requests - Get click analytics for review requests
"""

import logging
from flask import Blueprint, jsonify, request
from app.services.auth_service import require_auth
from app.services import link_tracker

logger = logging.getLogger(__name__)

analytics_bp = Blueprint('analytics', __name__)


@analytics_bp.route('/review-requests', methods=['GET'])
@require_auth
def get_review_request_analytics():
    """
    Get click analytics for the authenticated business's review requests.

    Query parameters:
        days (int, optional): Number of days to look back. Default 30.

    Response:
        {
          "summary": {
            "total_sent": 156,
            "total_clicked": 78,
            "click_rate": 0.50,
            "mobile_clicks": 65,
            "desktop_clicks": 13,
            "tablet_clicks": 0
          },
          "recent_requests": [ ... ]
        }
    """
    try:
        business_id = request.business.get('id')

        # Parse optional 'days' query parameter
        days = request.args.get('days', 30, type=int)
        if days < 1 or days > 365:
            return jsonify({"error": "days must be between 1 and 365"}), 400

        stats = link_tracker.get_stats_for_business(business_id, days=days)

        return jsonify(stats), 200

    except Exception as e:
        logger.error(f"Failed to fetch review request analytics: {e}")
        return jsonify({
            "error": f"Failed to fetch analytics: {str(e)}"
        }), 500
