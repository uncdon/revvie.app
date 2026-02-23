"""
Admin API endpoints - PROTECTED + ADMIN ONLY.

All routes here require authentication AND admin privileges.
Admin is determined by matching the authenticated user's email
against the ADMIN_EMAIL environment variable.

Endpoints:
- GET  /api/admin/analytics                    - Get comprehensive business metrics
- GET  /api/admin/email-preview/<email_type>   - Dev only: render email HTML in browser
- GET  /api/admin/search-account               - Search for an account by email or name
- POST /api/admin/block-account                - Block an account (cancels Stripe sub)
- POST /api/admin/unblock-account              - Unblock a previously blocked account
- POST /api/admin/delete-account               - Soft or hard delete an account
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from flask import Blueprint, jsonify, request, Response
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
            },
            'support': _get_support_counts(month_start),
        }), 200

    except Exception as e:
        logger.exception("Admin analytics error")
        return jsonify({"error": f"Failed to load analytics: {str(e)}"}), 500


def _get_support_counts(month_start: str) -> dict:
    """Return support_requests counts for the current month. Fails silently."""
    try:
        rows = supabase.table('support_requests') \
            .select('type') \
            .gte('created_at', month_start) \
            .execute().data or []
        return {
            'total': len(rows),
            'feature_requests': sum(1 for r in rows if r.get('type') == 'feature_request'),
            'bugs': sum(1 for r in rows if r.get('type') == 'bug'),
        }
    except Exception:
        return {'total': 0, 'feature_requests': 0, 'bugs': 0}


# =============================================================================
# EMAIL PREVIEW  (development only)
# =============================================================================

# Sample data used across all previews
_SAMPLE = {
    'business_name': 'Bella Hair Studio',
    'email': 'owner@bellahair.com',
    'customer_name': 'Jessica',
    'customer_email': 'jessica@example.com',
    'trial_end_date': 'March 15, 2026',
    'days_remaining': 3,
    'credit_amount': 40,
    'referred_name': 'Glam Nails & Spa',
    'referral_link': 'https://revvie.app/signup?ref=REVABCD12',
    'pending_count': 2,
    'review_url': 'https://search.google.com/local/writereview?placeid=sample123',
}


def _build_preview_html(email_type: str) -> tuple[str, str] | None:
    """
    Build (subject, html) for the given email_type using sample data.
    Returns None if email_type is unrecognised.
    """
    from app.services.email_service import render_email_template, generate_unsubscribe_url

    APP_BASE_URL = os.environ.get('APP_BASE_URL', 'http://localhost:5000')
    s = _SAMPLE

    if email_type == 'review_request':
        subject = f"How was your visit to {s['business_name']}?"
        unsubscribe_url = generate_unsubscribe_url('sample-business-id', s['customer_email'])
        content = f"""
        <h2 style="margin: 0 0 16px; font-size: 24px; color: #111827; font-weight: 600;">
          Hi {s['customer_name']}!
        </h2>
        <p style="margin: 0 0 16px; font-size: 16px; color: #374151; line-height: 24px;">
          Thanks for visiting <strong>{s['business_name']}</strong>!
        </p>
        <p style="margin: 0 0 24px; font-size: 16px; color: #374151; line-height: 24px;">
          We'd love to hear about your experience. Your feedback helps us improve
          and helps others find great service.
        </p>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td style="padding: 8px 0 24px;">
              <a href="{s['review_url']}"
                 style="display: inline-block; padding: 14px 32px; background-color: #07B5F5;
                        color: #ffffff; text-decoration: none; border-radius: 8px;
                        font-size: 16px; font-weight: 600;">
                Leave a Review
              </a>
            </td>
          </tr>
        </table>
        <p style="margin: 0; font-size: 14px; color: #6b7280; line-height: 20px;">
          Takes less than 2 minutes. Thank you!
        </p>
        """
        footer = (
            f"Don't want review requests from {s['business_name']}? "
            f'<a href="{unsubscribe_url}" style="color: #07B5F5; text-decoration: underline;">Unsubscribe</a>'
        )

    elif email_type == 'trial_welcome':
        subject = "Welcome to Revvie \u2014 Your free trial has started! \U0001f389"
        content = f"""
        <h2 style="margin: 0 0 16px; font-size: 24px; color: #111827; font-weight: 600;">
          Welcome to Revvie!
        </h2>
        <p style="margin: 0 0 16px; font-size: 16px; color: #374151; line-height: 24px;">
          Your 14-day free trial has started today.
        </p>
        <p style="margin: 0 0 4px; font-size: 16px; color: #374151; line-height: 24px;">
          <strong>Trial ends:</strong> {s['trial_end_date']}
        </p>
        <p style="margin: 0 0 24px; font-size: 16px; color: #374151; line-height: 24px;">
          You won't be charged until {s['trial_end_date']}.
        </p>
        <div style="background-color: #EBF8FF; border-left: 4px solid #07B5F5; padding: 16px; margin: 0 0 24px 0;">
          <p style="margin: 0 0 8px; font-size: 15px; font-weight: 600; color: #07B5F5;">
            What you can do during your trial:
          </p>
          <ul style="margin: 0; padding-left: 20px; color: #374151; font-size: 15px; line-height: 26px;">
            <li>Send unlimited review requests via SMS &amp; email</li>
            <li>Import customers from any CSV</li>
            <li>Connect Square integration</li>
            <li>Track who opens your review links</li>
          </ul>
        </div>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td style="padding: 8px 0 8px;">
              <a href="{APP_BASE_URL}/dashboard"
                 style="display: inline-block; padding: 14px 32px; background-color: #07B5F5;
                        color: #ffffff; text-decoration: none; border-radius: 8px;
                        font-size: 16px; font-weight: 600;">
                Go to Dashboard &rarr;
              </a>
            </td>
          </tr>
        </table>
        """
        footer = "Questions? Just reply to this email."

    elif email_type == 'trial_ending':
        subject = f"Your Revvie trial ends in {s['days_remaining']} days"
        content = f"""
        <h2 style="margin: 0 0 16px; font-size: 24px; color: #111827; font-weight: 600;">
          Your free trial is ending soon
        </h2>
        <p style="margin: 0 0 16px; font-size: 16px; color: #374151; line-height: 24px;">
          Your trial ends <strong>{s['trial_end_date']}</strong> ({s['days_remaining']} days from now).
        </p>
        <p style="margin: 0 0 24px; font-size: 16px; color: #374151; line-height: 24px;">
          After that, you'll be charged $79/month.
        </p>
        <div style="background-color: #F3F4F6; border-radius: 8px; padding: 20px; margin: 0 0 24px 0;">
          <p style="margin: 0; font-size: 15px; color: #374151; line-height: 24px;">
            Need more time to decide?
            <a href="mailto:support@revvie.app" style="color: #07B5F5; text-decoration: underline;">
              Contact us
            </a>
          </p>
        </div>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td style="padding: 8px 0 8px;">
              <a href="{APP_BASE_URL}/dashboard"
                 style="display: inline-block; padding: 14px 32px; background-color: #07B5F5;
                        color: #ffffff; text-decoration: none; border-radius: 8px;
                        font-size: 16px; font-weight: 600;">
                Manage Billing &rarr;
              </a>
            </td>
          </tr>
        </table>
        """
        footer = "Questions? Just reply to this email."

    elif email_type == 'payment_failed':
        subject = "\u26a0\ufe0f Action required: Payment failed for Revvie"
        content = f"""
        <h2 style="margin: 0 0 16px; font-size: 24px; color: #DC2626; font-weight: 600;">
          Payment Issue
        </h2>
        <p style="margin: 0 0 16px; font-size: 16px; color: #374151; line-height: 24px;">
          We couldn't process your payment for Revvie.
        </p>
        <p style="margin: 0 0 24px; font-size: 16px; color: #374151; line-height: 24px;">
          Please update your payment method to keep your account active.
        </p>
        <div style="background-color: #FEF2F2; border-left: 4px solid #DC2626; padding: 16px; margin: 0 0 24px 0;">
          <p style="margin: 0; font-size: 15px; color: #991B1B; line-height: 22px;">
            Your account will be paused if payment isn't received within 7 days.
          </p>
        </div>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td style="padding: 8px 0 8px;">
              <a href="{APP_BASE_URL}/dashboard"
                 style="display: inline-block; padding: 14px 32px; background-color: #DC2626;
                        color: #ffffff; text-decoration: none; border-radius: 8px;
                        font-size: 16px; font-weight: 600;">
                Update Payment Method &rarr;
              </a>
            </td>
          </tr>
        </table>
        """
        footer = "Questions? Just reply to this email."

    elif email_type == 'referral_welcome':
        subject = f"You have ${int(s['credit_amount'])} in Revvie credit! \U0001f389"
        content = f"""
        <h2 style="margin: 0 0 16px; font-size: 24px; color: #111827; font-weight: 600;">
          Welcome to Revvie!
        </h2>
        <p style="margin: 0 0 16px; font-size: 16px; color: #374151; line-height: 24px;">
          Great news &mdash; <strong>${int(s['credit_amount'])} credit</strong> has been applied to your account!
        </p>
        <div style="background-color: #D1FAE5; border-left: 4px solid #6FCF97; padding: 16px; margin: 0 0 24px 0;">
          <p style="margin: 0; font-size: 18px; font-weight: 600; color: #065F46;">
            \U0001f4b0 Your first month: ${79 - int(s['credit_amount'])} instead of $79
          </p>
        </div>
        <p style="margin: 0 0 12px; font-size: 16px; color: #374151; line-height: 24px;">
          Start collecting Google reviews automatically:
        </p>
        <ul style="margin: 0 0 24px; padding-left: 20px; color: #374151; font-size: 15px; line-height: 26px;">
          <li>Connect Square or import your customers</li>
          <li>Review requests sent after each visit</li>
          <li>Track who clicks your links</li>
        </ul>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td style="padding: 8px 0 8px;">
              <a href="{APP_BASE_URL}/dashboard"
                 style="display: inline-block; padding: 14px 32px; background-color: #07B5F5;
                        color: #ffffff; text-decoration: none; border-radius: 8px;
                        font-size: 16px; font-weight: 600;">
                Go to Dashboard &rarr;
              </a>
            </td>
          </tr>
        </table>
        """
        footer = "Questions? Just reply to this email."

    elif email_type == 'referral_reward':
        subject = f"You earned ${int(s['credit_amount'])}! {s['referred_name']} joined Revvie \U0001f389"
        content = f"""
        <h2 style="margin: 0 0 16px; font-size: 24px; color: #111827; font-weight: 600;">
          You earned a referral reward! \U0001f389
        </h2>
        <p style="margin: 0 0 16px; font-size: 16px; color: #374151; line-height: 24px;">
          <strong>{s['referred_name']}</strong> just signed up with your referral link!
        </p>
        <div style="background-color: #D1FAE5; border-left: 4px solid #6FCF97; padding: 20px; margin: 0 0 24px 0; text-align: center;">
          <p style="margin: 0 0 8px; font-size: 16px; color: #065F46;">
            Your reward
          </p>
          <p style="margin: 0; font-size: 36px; font-weight: 700; color: #059669;">
            ${int(s['credit_amount'])}
          </p>
        </div>
        <p style="margin: 0 0 16px; font-size: 16px; color: #374151; line-height: 24px;">
          This credit has been added to your account and will be automatically applied to your next invoice.
        </p>
        <p style="margin: 0 0 24px; font-size: 14px; color: #6B7280; line-height: 22px;">
          Keep sharing your link to earn more!
        </p>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td style="padding: 8px 0 8px;">
              <a href="{APP_BASE_URL}/dashboard"
                 style="display: inline-block; padding: 14px 32px; background-color: #07B5F5;
                        color: #ffffff; text-decoration: none; border-radius: 8px;
                        font-size: 16px; font-weight: 600;">
                Share Your Link &rarr;
              </a>
            </td>
          </tr>
        </table>
        """
        footer = "Questions? Just reply to this email."

    else:
        return None

    return subject, render_email_template(subject, content, footer)


VALID_EMAIL_TYPES = {
    'review_request', 'trial_welcome', 'trial_ending',
    'payment_failed', 'referral_welcome', 'referral_reward',
}


@admin_bp.route('/email-preview/<email_type>', methods=['GET'])
@require_auth
def preview_email(email_type):
    """
    Render a branded email as HTML for visual inspection in the browser.

    Development only — blocked in production.
    Requires admin authentication.

    Args:
        email_type: One of review_request | trial_welcome | trial_ending |
                    payment_failed | referral_welcome | referral_reward
    """
    if not is_admin(request.business):
        return jsonify({"error": "Admin access required"}), 403

    if os.environ.get('FLASK_ENV') != 'development':
        return jsonify({"error": "Email preview is only available in development"}), 403

    if email_type not in VALID_EMAIL_TYPES:
        return jsonify({
            "error": f"Unknown email type '{email_type}'",
            "valid_types": sorted(VALID_EMAIL_TYPES),
        }), 400

    try:
        result = _build_preview_html(email_type)
        if result is None:
            return jsonify({"error": "Failed to build preview"}), 500

        subject, html = result
        logger.info(f"Email preview rendered: {email_type}")

        # Inject a dev banner at the top so it's obvious this is a preview
        banner = (
            '<div style="background:#1f2937;color:#f9fafb;text-align:center;'
            'padding:10px 16px;font-family:monospace;font-size:13px;">'
            f'&#128233; PREVIEW &mdash; <strong>{email_type}</strong> &mdash; {subject}'
            '</div>'
        )
        return Response(banner + html, mimetype='text/html')

    except Exception as e:
        logger.exception(f"Email preview failed for type '{email_type}'")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# ACCOUNT MANAGEMENT
# =============================================================================

def _cancel_stripe_subscription(business_id: str) -> bool:
    """
    Cancel all active Stripe subscriptions for a business.
    Returns True if a subscription was found and canceled, False otherwise.
    """
    import stripe
    stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

    try:
        biz = supabase.table('businesses').select('stripe_customer_id').eq('id', business_id).execute()
        if not biz.data or not biz.data[0].get('stripe_customer_id'):
            return False

        customer_id = biz.data[0]['stripe_customer_id']
        subs = stripe.Subscription.list(customer=customer_id, status='active', limit=10)
        canceled = False
        for sub in subs.auto_paging_iter():
            stripe.Subscription.cancel(sub.id)
            canceled = True
            logger.info(f"Canceled Stripe subscription {sub.id} for business {business_id}")
        return canceled
    except Exception as e:
        logger.warning(f"Stripe cancellation failed for business {business_id}: {e}")
        return False


@admin_bp.route('/search-account', methods=['GET'])
@require_auth
def search_account():
    """
    Search for a business account by email or business name.

    Query params:
        q: Search string (partial match on email or business_name)

    Returns the first matching business with account status fields.
    """
    if not is_admin(request.business):
        return jsonify({"error": "Admin access required"}), 403

    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({"error": "Query parameter 'q' is required"}), 400

    try:
        # Try exact email match first
        result = supabase.table('businesses').select(
            'id, business_name, email, account_status, subscription_status, '
            'blocked_at, blocked_reason, blocked_by, deleted_at, created_at'
        ).eq('email', q.lower()).limit(1).execute()

        # Fall back to case-insensitive partial match on business name
        if not result.data:
            result = supabase.table('businesses').select(
                'id, business_name, email, account_status, subscription_status, '
                'blocked_at, blocked_reason, blocked_by, deleted_at, created_at'
            ).ilike('business_name', f'%{q}%').limit(1).execute()

        if not result.data:
            return jsonify({"business": None}), 200

        return jsonify({"business": result.data[0]}), 200

    except Exception as e:
        logger.error(f"Account search error: {e}")
        return jsonify({"error": "Search failed"}), 500


@admin_bp.route('/block-account', methods=['POST'])
@require_auth
def block_account():
    """
    Block a business account.

    Immediately:
    - Sets account_status = 'blocked' with timestamp, reason, and acting admin
    - Cancels any active Stripe subscription

    Body:
        { "business_id": "uuid", "reason": "Abusing free trials" }
    """
    if not is_admin(request.business):
        return jsonify({"error": "Admin access required"}), 403

    data = request.get_json() or {}
    business_id = data.get('business_id', '').strip()
    reason = data.get('reason', '').strip()

    if not business_id:
        return jsonify({"error": "business_id is required"}), 400
    if not reason:
        return jsonify({"error": "reason is required"}), 400

    # Verify target business exists
    target = supabase.table('businesses').select('id, business_name, account_status').eq('id', business_id).execute()
    if not target.data:
        return jsonify({"error": "Business not found"}), 404

    if target.data[0].get('account_status') == 'blocked':
        return jsonify({"error": "Account is already blocked"}), 409

    admin_email = request.business.get('email', 'unknown')
    now = datetime.now(timezone.utc).isoformat()

    supabase.table('businesses').update({
        'account_status': 'blocked',
        'blocked_at': now,
        'blocked_reason': reason,
        'blocked_by': admin_email,
    }).eq('id', business_id).execute()

    stripe_canceled = _cancel_stripe_subscription(business_id)

    logger.info(
        f"ADMIN BLOCK: {business_id} ({target.data[0].get('business_name')}) "
        f"blocked by {admin_email}. Reason: {reason}. Stripe canceled: {stripe_canceled}"
    )

    return jsonify({
        "success": True,
        "business_id": business_id,
        "business_name": target.data[0].get('business_name'),
        "stripe_subscription_canceled": stripe_canceled,
    }), 200


@admin_bp.route('/unblock-account', methods=['POST'])
@require_auth
def unblock_account():
    """
    Unblock a previously blocked business account.

    Clears account_status back to 'active' and removes block metadata.
    Does NOT reinstate a canceled Stripe subscription — the user must resubscribe.

    Body:
        { "business_id": "uuid" }
    """
    if not is_admin(request.business):
        return jsonify({"error": "Admin access required"}), 403

    data = request.get_json() or {}
    business_id = data.get('business_id', '').strip()

    if not business_id:
        return jsonify({"error": "business_id is required"}), 400

    target = supabase.table('businesses').select('id, business_name, account_status').eq('id', business_id).execute()
    if not target.data:
        return jsonify({"error": "Business not found"}), 404

    if target.data[0].get('account_status') != 'blocked':
        return jsonify({"error": "Account is not blocked"}), 409

    supabase.table('businesses').update({
        'account_status': 'active',
        'blocked_at': None,
        'blocked_reason': None,
        'blocked_by': None,
    }).eq('id', business_id).execute()

    admin_email = request.business.get('email', 'unknown')
    logger.info(
        f"ADMIN UNBLOCK: {business_id} ({target.data[0].get('business_name')}) "
        f"unblocked by {admin_email}"
    )

    return jsonify({
        "success": True,
        "business_id": business_id,
        "business_name": target.data[0].get('business_name'),
        "note": "Account unblocked. Stripe subscription was not reinstated — user must resubscribe.",
    }), 200


@admin_bp.route('/delete-account', methods=['POST'])
@require_auth
def admin_delete_account():
    """
    Soft or hard delete a business account.

    Soft delete (permanent=false, default):
        Sets account_status = 'deleted' and records deleted_at.
        All data is retained for 30 days for potential recovery.

    Hard delete (permanent=true):
        Cancels Stripe subscription then permanently deletes all data
        in FK order: tracking_clicks → tracking_links → credit_transactions
        → referrals → queued_review_requests → review_requests → customers
        → integrations → businesses → auth user.
        IRREVERSIBLE.

    Body:
        { "business_id": "uuid", "permanent": false }
    """
    if not is_admin(request.business):
        return jsonify({"error": "Admin access required"}), 403

    data = request.get_json() or {}
    business_id = data.get('business_id', '').strip()
    permanent = bool(data.get('permanent', False))

    if not business_id:
        return jsonify({"error": "business_id is required"}), 400

    target = supabase.table('businesses').select('id, business_name').eq('id', business_id).execute()
    if not target.data:
        return jsonify({"error": "Business not found"}), 404

    business_name = target.data[0].get('business_name', '')
    admin_email = request.business.get('email', 'unknown')

    if not permanent:
        # ── Soft delete ──────────────────────────────────────────────────────
        supabase.table('businesses').update({
            'account_status': 'deleted',
            'deleted_at': datetime.now(timezone.utc).isoformat(),
        }).eq('id', business_id).execute()

        logger.info(
            f"ADMIN SOFT DELETE: {business_id} ({business_name}) "
            f"soft-deleted by {admin_email}"
        )

        return jsonify({
            "success": True,
            "deleted": "soft",
            "business_id": business_id,
            "business_name": business_name,
            "note": "Data retained for 30 days. Set permanent=true to hard delete immediately.",
        }), 200

    else:
        # ── Hard delete ───────────────────────────────────────────────────────
        stripe_canceled = _cancel_stripe_subscription(business_id)

        # Delete in FK order
        tracking_links = supabase.table('tracking_links').select('id').eq('business_id', business_id).execute()
        if tracking_links.data:
            link_ids = [r['id'] for r in tracking_links.data]
            supabase.table('tracking_clicks').delete().in_('tracking_link_id', link_ids).execute()

        supabase.table('tracking_links').delete().eq('business_id', business_id).execute()
        supabase.table('credit_transactions').delete().eq('business_id', business_id).execute()
        supabase.table('referrals').delete().eq('referrer_business_id', business_id).execute()
        supabase.table('referrals').delete().eq('referred_business_id', business_id).execute()
        supabase.table('queued_review_requests').delete().eq('business_id', business_id).execute()
        supabase.table('review_requests').delete().eq('business_id', business_id).execute()
        supabase.table('customers').delete().eq('business_id', business_id).execute()
        supabase.table('integrations').delete().eq('business_id', business_id).execute()
        supabase.table('businesses').delete().eq('id', business_id).execute()

        try:
            from app.services.supabase_service import supabase_admin as _admin
            _admin.auth.admin.delete_user(business_id)
        except Exception as e:
            logger.warning(f"Auth user deletion failed for {business_id}: {e}")

        logger.warning(
            f"ADMIN HARD DELETE: {business_id} ({business_name}) "
            f"permanently deleted by {admin_email}. Stripe canceled: {stripe_canceled}"
        )

        return jsonify({
            "success": True,
            "deleted": "permanent",
            "business_id": business_id,
            "business_name": business_name,
            "stripe_subscription_canceled": stripe_canceled,
        }), 200


@admin_bp.route('/waitlist', methods=['GET'])
@require_auth
def get_waitlist():
    """Get integration waitlist signups (admin only)."""
    if not is_admin(request.business):
        return jsonify({"error": "Admin access required"}), 403

    try:
        # Count entries per integration
        counts_result = supabase.table('integration_waitlist') \
            .select('integration') \
            .execute()

        counts = {'fresha': 0, 'vagaro': 0, 'mindbody': 0}
        for row in (counts_result.data or []):
            integration = row.get('integration', '')
            if integration in counts:
                counts[integration] += 1

        # Recent 20 signups joined with business name
        recent_result = supabase.table('integration_waitlist') \
            .select('email, integration, created_at, businesses(business_name)') \
            .order('created_at', desc=True) \
            .limit(20) \
            .execute()

        recent = []
        for row in (recent_result.data or []):
            biz = row.get('businesses') or {}
            recent.append({
                'email': row.get('email'),
                'integration': row.get('integration'),
                'created_at': row.get('created_at'),
                'business_name': biz.get('business_name'),
            })

        return jsonify({'counts': counts, 'recent': recent}), 200

    except Exception as e:
        logger.error(f"Waitlist fetch error: {e}")
        return jsonify({"error": "Failed to fetch waitlist data"}), 500
