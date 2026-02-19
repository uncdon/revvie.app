"""
Email unsubscribe route.

GET /unsubscribe?business_id=<uuid>&email=<email>&token=<hmac>

Public route — no authentication required.
Token is verified using HMAC-SHA256 against SECRET_KEY.
On success, the customer's email is added to email_suppressions.
"""

import os
import hmac
import hashlib
import logging

from flask import Blueprint, request, render_template
from app.services.supabase_service import supabase_admin as supabase

logger = logging.getLogger(__name__)

unsubscribe_bp = Blueprint('unsubscribe', __name__)


def _verify_token(business_id: str, customer_email: str, token: str) -> bool:
    """
    Verify the HMAC-SHA256 token embedded in the unsubscribe URL.

    Must produce the same result as generate_unsubscribe_url() in email_service.py.
    """
    secret = os.environ.get('SECRET_KEY', 'revvie-default-secret')
    payload = f"{business_id}:{customer_email}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, token)


@unsubscribe_bp.route('/unsubscribe', methods=['GET'])
def unsubscribe():
    """
    Handle an email unsubscribe request.

    Verifies the HMAC token, then inserts a row into email_suppressions.
    Subsequent calls for the same business+email pair are silently accepted
    (duplicate key is treated as already-unsubscribed, not an error).
    """
    business_id = request.args.get('business_id', '').strip()
    customer_email = request.args.get('email', '').strip().lower()
    token = request.args.get('token', '').strip()

    # --- Validate params ---
    if not all([business_id, customer_email, token]):
        return render_template(
            'unsubscribe_error.html',
            error='This unsubscribe link is missing required information. '
                  'Please use the link directly from your email.'
        ), 400

    # --- Verify token ---
    if not _verify_token(business_id, customer_email, token):
        logger.warning(f"Invalid unsubscribe token for {customer_email} / {business_id}")
        return render_template(
            'unsubscribe_error.html',
            error='This unsubscribe link is invalid or has already been used. '
                  'Please use the link directly from your email.'
        ), 403

    # --- Look up business name ---
    business_name = 'this business'
    try:
        biz = supabase.table('businesses').select('business_name').eq('id', business_id).execute()
        if biz.data:
            business_name = biz.data[0].get('business_name') or business_name
    except Exception as e:
        logger.warning(f"Could not look up business name for {business_id}: {e}")

    # --- Add to suppression list ---
    try:
        supabase.table('email_suppressions').insert({
            'business_id': business_id,
            'customer_email': customer_email,
        }).execute()
        logger.info(f"Unsubscribed {customer_email} from business {business_id}")
    except Exception as e:
        if 'duplicate' in str(e).lower() or 'unique' in str(e).lower():
            # Already unsubscribed — show success anyway (idempotent)
            logger.info(f"Already unsubscribed: {customer_email} from {business_id}")
        else:
            logger.error(f"Unsubscribe DB insert failed for {customer_email}: {e}")
            return render_template(
                'unsubscribe_error.html',
                error='We could not process your request. Please try again or '
                      'reply directly to the email to opt out.'
            ), 500

    return render_template(
        'unsubscribe_success.html',
        customer_email=customer_email,
        business_name=business_name,
    ), 200
