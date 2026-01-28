"""
Dashboard API endpoints.

Endpoints:
- GET /api/dashboard/stats - Get dashboard statistics
"""

from flask import Blueprint, jsonify, request
from app.services.auth_service import require_auth
from app.services.supabase_service import supabase

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/dashboard/stats', methods=['GET'])
@require_auth
def get_dashboard_stats():
    """
    Get dashboard statistics for the authenticated business.

    Returns counts and metrics for the dashboard stat cards.

    Response:
        {
            "total_requests_sent": 15,
            "reviews_received": 0,
            "conversion_rate": 0
        }
    """
    try:
        business_id = request.business.get('id')

        # Count total review requests for this business
        result = supabase.table("review_requests") \
            .select("id", count="exact") \
            .eq("business_id", business_id) \
            .execute()

        total_requests_sent = result.count if result.count is not None else 0

        # Reviews received - placeholder for now (will implement tracking later)
        reviews_received = 0

        # Conversion rate - placeholder for now
        # Formula: (reviews_received / total_requests_sent) * 100
        conversion_rate = 0

        return jsonify({
            "total_requests_sent": total_requests_sent,
            "reviews_received": reviews_received,
            "conversion_rate": conversion_rate
        }), 200

    except Exception as e:
        return jsonify({
            "error": f"Failed to fetch dashboard stats: {str(e)}"
        }), 500
