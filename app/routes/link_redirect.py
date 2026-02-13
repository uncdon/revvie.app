"""
Link redirect endpoint - resolves short tracking URLs.

PUBLIC route, no authentication required.
Customer clicks /r/a3Fx9Kp -> we log the click -> redirect to Google review page.

Performance is critical: look up the link, fire off the click log,
and redirect immediately. Tracking errors must never break the redirect.
"""

import os
import logging
from flask import Blueprint, redirect, request

from app.services.link_tracker import get_tracking_link, log_click
from app.services.supabase_service import supabase_admin

logger = logging.getLogger(__name__)

APP_BASE_URL = os.getenv('APP_BASE_URL', 'http://localhost:5001')

# No url_prefix — registered at root so URLs stay short: /r/<code>
link_redirect_bp = Blueprint('link_redirect', __name__)


@link_redirect_bp.route('/r/<short_code>')
def redirect_tracking_link(short_code):
    """
    Look up a tracking link, log the click, and redirect to the destination.

    If the short code isn't found, redirect to the homepage instead of
    showing an error page (better UX for the customer).
    """
    # Step 1: Look up the tracking link
    tracking_link = get_tracking_link(short_code)

    if not tracking_link:
        logger.warning(f"Unknown short code: {short_code}")
        return redirect(APP_BASE_URL, code=302)

    # Step 2: Log the click — must never block or break the redirect
    try:
        user_agent = request.headers.get('User-Agent', '')
        ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)

        log_click(
            tracking_link_id=tracking_link['id'],
            user_agent=user_agent,
            ip_address=ip_address,
        )
    except Exception as e:
        logger.error(f"Click logging failed for {short_code}: {e}")

    # Step 3: Update review request / queued request status to 'clicked'
    try:
        review_request_id = tracking_link.get('review_request_id')
        if review_request_id:
            supabase_admin.table('review_requests') \
                .update({'status': 'clicked'}) \
                .eq('id', review_request_id) \
                .neq('status', 'failed') \
                .execute()

        queued_request_id = tracking_link.get('queued_request_id')
        if queued_request_id:
            supabase_admin.table('queued_review_requests') \
                .update({'status': 'clicked'}) \
                .eq('id', queued_request_id) \
                .neq('status', 'failed') \
                .execute()
    except Exception as e:
        logger.error(f"Status update failed for {short_code}: {e}")

    # Step 4: Redirect to the destination (Google review page)
    return redirect(tracking_link['destination_url'], code=302)
