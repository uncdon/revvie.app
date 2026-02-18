"""
Admin API endpoints - PROTECTED + ADMIN ONLY.

All routes here require authentication AND admin privileges.
Admin is determined by matching the authenticated user's email
against the ADMIN_EMAIL environment variable.

Endpoints:
- GET /api/admin/analytics - Get comprehensive business metrics
"""

import logging
from datetime import datetime, timedelta, timezone
from flask import Blueprint, jsonify, request
from app.services.supabase_service import supabase_admin as supabase
from app.services.auth_service import require_auth, is_admin

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__)

# Price per month (used for MRR/ARR calculations)
PRICE_PER_MONTH = 79


@admin_bp.route('/analytics', methods=['GET'])
@require_auth
def get_analytics():
    """
    Get comprehensive admin analytics.

    Returns overview, growth, revenue, referrals, recent signups,
    recent payments, and alerts.
    """
    if not is_admin(request.business):
        return jsonify({"error": "Unauthorized"}), 403

    try:
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        three_days_from_now = (now + timedelta(days=3)).isoformat()
        now_iso = now.isoformat()

        # --- Overview metrics ---

        # All businesses with relevant fields
        all_biz = supabase.table('businesses').select(
            'id, business_name, email, subscription_status, '
            'trial_ends_at, subscription_ends_at, created_at'
        ).execute()

        businesses = all_biz.data or []
        total_users = len(businesses)

        # Count by status
        status_counts = {}
        for b in businesses:
            status = b.get('subscription_status') or 'none'
            status_counts[status] = status_counts.get(status, 0) + 1

        trial_users = status_counts.get('trialing', 0)
        paid_users = status_counts.get('active', 0)
        canceled_users = status_counts.get('canceled', 0)
        past_due_users = status_counts.get('past_due', 0)

        # MRR / ARR
        mrr = paid_users * PRICE_PER_MONTH
        arr = mrr * 12

        # --- Growth metrics ---

        # New signups this month
        new_signups = sum(
            1 for b in businesses
            if b.get('created_at') and b['created_at'] >= month_start
        )

        # Trial conversion rate
        # Total who ever trialed = currently active + canceled + currently trialing
        ever_trialed = sum(
            1 for b in businesses
            if b.get('subscription_status') in ('active', 'canceled', 'trialing')
            or b.get('trial_ends_at')
        )
        converted = paid_users
        conversion_rate = round((converted / ever_trialed * 100), 1) if ever_trialed > 0 else 0

        # Churned this month
        churned_this_month = sum(
            1 for b in businesses
            if b.get('subscription_status') == 'canceled'
            and b.get('subscription_ends_at')
            and b['subscription_ends_at'] >= month_start
        )

        # Churn rate (churned / (paid + churned) to avoid division issues)
        churn_denominator = paid_users + churned_this_month
        churn_rate = round((churned_this_month / churn_denominator * 100), 1) if churn_denominator > 0 else 0

        # --- Referral stats ---

        referrals_result = supabase.table('referrals').select(
            'id, status, referrer_credit'
        ).execute()

        referrals = referrals_result.data or []
        referral_stats = {
            'total': len(referrals),
            'pending': 0,
            'completed': 0,
            'pending_credit': 0,
            'paid_credit': 0
        }
        for r in referrals:
            if r.get('status') == 'pending':
                referral_stats['pending'] += 1
                referral_stats['pending_credit'] += (r.get('referrer_credit') or 0)
            elif r.get('status') == 'completed':
                referral_stats['completed'] += 1
                referral_stats['paid_credit'] += (r.get('referrer_credit') or 0)

        # --- Revenue this month ---

        payments_this_month = supabase.table('billing_events').select(
            'amount'
        ).eq(
            'event_type', 'payment_succeeded'
        ).gte(
            'created_at', month_start
        ).execute()

        revenue_this_month = sum(
            (p.get('amount') or 0) for p in (payments_this_month.data or [])
        )

        # --- Recent signups (last 10) ---

        recent_signups_result = supabase.table('businesses').select(
            'id, business_name, email, subscription_status, created_at, trial_ends_at'
        ).order(
            'created_at', desc=True
        ).limit(10).execute()

        recent_signups = [
            {
                'id': b['id'],
                'business_name': b.get('business_name', ''),
                'email': b.get('email', ''),
                'status': b.get('subscription_status') or 'none',
                'created_at': b.get('created_at'),
                'trial_ends_at': b.get('trial_ends_at')
            }
            for b in (recent_signups_result.data or [])
        ]

        # --- Recent payments (last 10) ---

        recent_payments_result = supabase.table('billing_events').select(
            'business_id, amount, description, created_at'
        ).eq(
            'event_type', 'payment_succeeded'
        ).order(
            'created_at', desc=True
        ).limit(10).execute()

        recent_payments = []
        if recent_payments_result.data:
            # Get business names for payment records
            payment_biz_ids = list({p['business_id'] for p in recent_payments_result.data if p.get('business_id')})
            biz_names = {}
            if payment_biz_ids:
                biz_lookup = supabase.table('businesses').select(
                    'id, business_name'
                ).in_('id', payment_biz_ids).execute()
                biz_names = {b['id']: b.get('business_name', '') for b in (biz_lookup.data or [])}

            recent_payments = [
                {
                    'business_name': biz_names.get(p.get('business_id'), 'Unknown'),
                    'amount': p.get('amount') or 0,
                    'description': p.get('description', ''),
                    'created_at': p.get('created_at')
                }
                for p in recent_payments_result.data
            ]

        # --- Usage across all businesses ---

        usage_result = supabase.table('businesses').select(
            'sms_sent_this_month, email_sent_this_month, sms_monthly_cap, email_monthly_cap'
        ).execute()

        usage_rows = usage_result.data or []
        total_sms = sum(b.get('sms_sent_this_month') or 0 for b in usage_rows)
        total_emails = sum(b.get('email_sent_this_month') or 0 for b in usage_rows)

        businesses_near_limit = 0
        for b in usage_rows:
            sms_sent = b.get('sms_sent_this_month') or 0
            sms_cap = b.get('sms_monthly_cap') or 750
            email_sent = b.get('email_sent_this_month') or 0
            email_cap = b.get('email_monthly_cap') or 1000
            if (sms_cap > 0 and sms_sent / sms_cap >= 0.8) or (email_cap > 0 and email_sent / email_cap >= 0.8):
                businesses_near_limit += 1

        # --- Alerts ---

        trials_ending_soon = sum(
            1 for b in businesses
            if b.get('subscription_status') == 'trialing'
            and b.get('trial_ends_at')
            and now_iso <= b['trial_ends_at'] <= three_days_from_now
        )

        return jsonify({
            'overview': {
                'total_users': total_users,
                'trial_users': trial_users,
                'paid_users': paid_users,
                'canceled_users': canceled_users,
                'mrr': mrr,
                'arr': arr,
                'status_breakdown': status_counts
            },
            'growth': {
                'new_signups_this_month': new_signups,
                'trial_conversion_rate': conversion_rate,
                'churn_rate': churn_rate,
                'churned_this_month': churned_this_month,
                'average_ltv': round(PRICE_PER_MONTH / (churn_rate / 100), 0) if churn_rate > 0 else 0
            },
            'revenue': {
                'mrr': mrr,
                'arr': arr,
                'revenue_this_month': revenue_this_month
            },
            'referrals': referral_stats,
            'recent_signups': recent_signups,
            'recent_payments': recent_payments,
            'usage': {
                'total_sms_this_month': total_sms,
                'total_emails_this_month': total_emails,
                'businesses_near_limit': businesses_near_limit,
                'estimated_sms_cost': round(total_sms * 0.0095, 2)
            },
            'alerts': {
                'trials_ending_soon': trials_ending_soon,
                'failed_payments': past_due_users
            }
        }), 200

    except Exception as e:
        logger.exception("Admin analytics error")
        return jsonify({"error": f"Failed to load analytics: {str(e)}"}), 500
