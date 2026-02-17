"""
Referral API endpoints.

Endpoints:
- GET  /api/referrals/stats                  - Get referral stats for current business
- GET  /api/referrals/link                   - Get referral code and link
- POST /api/referrals/complete/<referral_id> - Admin: mark referral complete
- POST /api/referrals/cancel/<referral_id>   - Admin: cancel a referral
- GET  /api/referrals/all                    - Admin: all referrals across businesses
"""

import os
import logging

from flask import Blueprint, jsonify, request
from app.services.auth_service import require_auth
from app.services import referral_service
from app.services.supabase_service import supabase_admin

logger = logging.getLogger(__name__)

referrals_bp = Blueprint('referrals', __name__)


def is_admin(business):
    """Check if the authenticated business is the admin account."""
    admin_email = os.getenv('ADMIN_EMAIL', 'daniel@revvie.app')
    return business.get('email') == admin_email


# ============================================================================
# ROUTE 1: GET /stats
# ============================================================================

@referrals_bp.route('/stats', methods=['GET'])
@require_auth
def get_stats():
    """Get referral stats for the current business."""
    try:
        business = request.business
        if not business:
            return jsonify({'error': 'Business not found'}), 404

        stats = referral_service.get_referral_stats(business['id'])
        if stats is None:
            return jsonify({'error': 'Failed to get referral stats'}), 500

        return jsonify(stats), 200

    except Exception as e:
        logger.exception(f"Error getting referral stats: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ROUTE 2: GET /link
# ============================================================================

@referrals_bp.route('/link', methods=['GET'])
@require_auth
def get_link():
    """Get or create referral code and link for the current business."""
    try:
        business = request.business
        if not business:
            return jsonify({'error': 'Business not found'}), 404

        result = referral_service.get_referral_link(business['id'])
        if result is None:
            return jsonify({'error': 'Failed to get referral link'}), 500

        return jsonify(result), 200

    except Exception as e:
        logger.exception(f"Error getting referral link: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ROUTE 3: POST /complete/<referral_id>
# ============================================================================

@referrals_bp.route('/complete/<referral_id>', methods=['POST'])
@require_auth
def complete_referral(referral_id):
    """Admin only: manually mark a referral as complete."""
    try:
        business = request.business
        if not business or not is_admin(business):
            return jsonify({'error': 'Admin access required'}), 403

        result = referral_service.complete_referral(referral_id)
        if result is None:
            return jsonify({'error': 'Referral not found or not pending'}), 404

        return jsonify(result), 200

    except Exception as e:
        logger.exception(f"Error completing referral {referral_id}: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ROUTE 4: POST /cancel/<referral_id>
# ============================================================================

@referrals_bp.route('/cancel/<referral_id>', methods=['POST'])
@require_auth
def cancel_referral(referral_id):
    """Admin only: cancel a referral."""
    try:
        business = request.business
        if not business or not is_admin(business):
            return jsonify({'error': 'Admin access required'}), 403

        result = referral_service.cancel_referral(referral_id)
        if result is None:
            return jsonify({'error': 'Referral not found'}), 404

        return jsonify(result), 200

    except Exception as e:
        logger.exception(f"Error cancelling referral {referral_id}: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ROUTE 5: GET /all
# ============================================================================

@referrals_bp.route('/all', methods=['GET'])
@require_auth
def get_all_referrals():
    """Admin only: get all referrals across all businesses."""
    try:
        business = request.business
        if not business or not is_admin(business):
            return jsonify({'error': 'Admin access required'}), 403

        # Get all referrals
        ref_result = supabase_admin.table('referrals') \
            .select('*') \
            .order('created_at', desc=True) \
            .execute()

        referrals_raw = ref_result.data or []

        # Collect all business IDs (referrers + referred)
        business_ids = set()
        for r in referrals_raw:
            business_ids.add(r['referrer_business_id'])
            business_ids.add(r['referred_business_id'])

        # Look up business names in one query
        names_map = {}
        if business_ids:
            try:
                names_result = supabase_admin.table('businesses') \
                    .select('id, business_name, email') \
                    .in_('id', list(business_ids)) \
                    .execute()
                for biz in (names_result.data or []):
                    names_map[biz['id']] = {
                        'name': biz.get('business_name', 'Unknown'),
                        'email': biz.get('email', ''),
                    }
            except Exception as e:
                logger.error(f"Failed to look up business names: {e}")

        # Build response with summary
        pending = 0
        completed = 0
        cancelled = 0
        total_credits = 0.0

        referrals = []
        for r in referrals_raw:
            status = r['status']
            if status == 'pending':
                pending += 1
            elif status == 'completed':
                completed += 1
                total_credits += float(r.get('referrer_credit') or 0)
                total_credits += float(r.get('referred_credit') or 0)
            elif status == 'cancelled':
                cancelled += 1

            referrer_info = names_map.get(r['referrer_business_id'], {})
            referred_info = names_map.get(r['referred_business_id'], {})

            referrals.append({
                'id': r['id'],
                'referrer_business_id': r['referrer_business_id'],
                'referrer_name': referrer_info.get('name', 'Unknown'),
                'referrer_email': referrer_info.get('email', ''),
                'referred_business_id': r['referred_business_id'],
                'referred_name': referred_info.get('name', 'Unknown'),
                'referred_email': referred_info.get('email', ''),
                'referral_code': r.get('referral_code'),
                'status': status,
                'referrer_credit': float(r.get('referrer_credit') or 0),
                'referred_credit': float(r.get('referred_credit') or 0),
                'created_at': r.get('created_at'),
                'completed_at': r.get('completed_at'),
            })

        return jsonify({
            'referrals': referrals,
            'summary': {
                'total': len(referrals_raw),
                'pending': pending,
                'completed': completed,
                'cancelled': cancelled,
                'total_credits_issued': total_credits,
            }
        }), 200

    except Exception as e:
        logger.exception(f"Error getting all referrals: {e}")
        return jsonify({'error': str(e)}), 500
