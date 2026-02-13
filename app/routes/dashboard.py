"""
Dashboard API endpoints.

Endpoints:
- GET /api/dashboard/stats - Get dashboard statistics
"""

from flask import Blueprint, jsonify, request
from app.services.auth_service import require_auth
from app.services.supabase_service import supabase
from app.services import link_tracker

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/dashboard/stats', methods=['GET'])
@require_auth
def get_dashboard_stats():
    """
    Get dashboard statistics for the authenticated business.

    Returns counts and metrics for the dashboard stat cards.

    Response:
        {
            "total_requests_sent": 156,
            "total_clicked": 78,
            "conversion_rate": 50
        }
    """
    try:
        business_id = request.business.get('id')

        # Count total review requests (excluding failed)
        sent_result = supabase.table("review_requests") \
            .select("id", count="exact") \
            .eq("business_id", business_id) \
            .neq("status", "failed") \
            .execute()

        total_requests_sent = sent_result.count if sent_result.count is not None else 0

        # Count clicked review requests
        clicked_result = supabase.table("review_requests") \
            .select("id", count="exact") \
            .eq("business_id", business_id) \
            .eq("status", "clicked") \
            .execute()

        total_clicked = clicked_result.count if clicked_result.count is not None else 0

        # Conversion rate: (clicked / sent) * 100
        conversion_rate = round((total_clicked / total_requests_sent) * 100) if total_requests_sent > 0 else 0

        return jsonify({
            "total_requests_sent": total_requests_sent,
            "total_clicked": total_clicked,
            "conversion_rate": conversion_rate
        }), 200

    except Exception as e:
        return jsonify({
            "error": f"Failed to fetch dashboard stats: {str(e)}"
        }), 500
