"""
Billing API endpoints - handles Stripe checkout, portal, and webhooks.

API Endpoints (registered under /api):
- POST /api/billing/create-checkout      - Create Stripe Checkout session (auth required)
- POST /api/billing/create-portal        - Create Stripe Customer Portal session (auth required)
- GET  /api/billing/status               - Get subscription status (auth required)
- GET  /api/billing/trial-eligibility    - Check trial + referral discount eligibility (auth required)
- POST /api/billing/webhook              - Handle Stripe webhook events (public, signature verified)

Page Routes (registered at root):
- GET /billing/success   - Shown after successful Stripe checkout
- GET /billing/canceled  - Shown if user cancels Stripe checkout
"""

import os
import logging
from flask import Blueprint, jsonify, request, send_from_directory

import stripe
from dotenv import load_dotenv

from app.services.auth_service import require_auth
from app.services.supabase_service import supabase_admin as supabase
from app.services import stripe_service

load_dotenv()

logger = logging.getLogger(__name__)

# API routes (registered under /api prefix)
billing_bp = Blueprint('billing', __name__)

# Page routes (registered with no prefix)
billing_pages_bp = Blueprint('billing_pages', __name__)

STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')

# Path to frontend folder
FRONTEND_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'frontend')


# =============================================================================
# ROUTE 1: CREATE CHECKOUT SESSION
# =============================================================================

@billing_bp.route('/billing/create-checkout', methods=['POST'])
@require_auth
def create_checkout():
    """
    Create a Stripe Checkout session for the authenticated business.

    The frontend redirects the user to the returned checkout_url,
    which is a Stripe-hosted payment page with a 14-day free trial.

    Returns:
        {"checkout_url": "https://checkout.stripe.com/..."}
    """
    try:
        business = request.business
        if not business:
            return jsonify({"error": "Business profile not found"}), 404

        business_id = business.get('id')
        email = business.get('email')
        business_name = business.get('business_name')

        # Check if already has active subscription
        status = stripe_service.get_subscription_status(business_id)
        if status['is_active']:
            return jsonify({
                "error": "You already have an active subscription",
                "status": status['status']
            }), 400

        # Check for referral discount
        discount_percent = None
        discount_applied = business.get('discount_applied') or 0
        if discount_applied > 0:
            discount_percent = 50

        # Create checkout session
        session = stripe_service.create_checkout_session(
            business_id, email, business_name, discount_percent
        )

        if not session:
            return jsonify({"error": "Failed to create checkout session"}), 500

        return jsonify({"checkout_url": session.url}), 200

    except Exception as e:
        logger.error(f"Error creating checkout session: {e}")
        return jsonify({"error": "An unexpected error occurred"}), 500


# =============================================================================
# ROUTE 1b: TRIAL ELIGIBILITY CHECK
# =============================================================================

@billing_bp.route('/billing/trial-eligibility', methods=['GET'])
@require_auth
def trial_eligibility():
    """
    Return whether the authenticated business qualifies for a free trial
    and/or a referral discount.

    Used by the subscribe page to update its messaging before the user
    clicks through to Stripe — prevents surprise charges for returning users.

    Returns:
        {
            "eligible_for_trial": true,
            "eligible_for_referral_discount": false,
            "message": "You qualify for a 14-day free trial"
        }
    """
    try:
        business = request.business
        if not business:
            return jsonify({"error": "Business profile not found"}), 404

        business_id = business.get('id')

        result = supabase.table('businesses').select(
            'has_had_trial, referral_credit_used, discount_applied'
        ).eq('id', business_id).execute()

        if not result.data:
            return jsonify({"error": "Business not found"}), 404

        biz = result.data[0]
        eligible_for_trial = not biz.get('has_had_trial', False)
        eligible_for_referral = (
            not biz.get('referral_credit_used', False)
            and (biz.get('discount_applied') or 0) > 0
        )

        if eligible_for_trial and eligible_for_referral:
            message = "You qualify for a 14-day free trial and a $40 referral discount"
        elif eligible_for_trial:
            message = "You qualify for a 14-day free trial"
        elif eligible_for_referral:
            message = "No free trial available (already used). A $40 referral discount will be applied."
        else:
            message = "Your trial was already used. Subscribe for $79/month, billed immediately."

        return jsonify({
            "eligible_for_trial": eligible_for_trial,
            "eligible_for_referral_discount": eligible_for_referral,
            "message": message,
        }), 200

    except Exception as e:
        logger.error(f"Error checking trial eligibility: {e}")
        return jsonify({"error": "An unexpected error occurred"}), 500


# =============================================================================
# ROUTE 2: CREATE PORTAL SESSION
# =============================================================================

@billing_bp.route('/billing/create-portal', methods=['POST'])
@require_auth
def create_portal():
    """
    Create a Stripe Customer Portal session for the authenticated business.

    The portal lets users update their card, cancel, and view invoices.

    Returns:
        {"portal_url": "https://billing.stripe.com/..."}
    """
    try:
        business = request.business
        if not business:
            return jsonify({"error": "Business profile not found"}), 404

        business_id = business.get('id')

        session = stripe_service.create_portal_session(business_id)

        if not session:
            return jsonify({
                "error": "No billing account found. Please set up billing first."
            }), 404

        return jsonify({"portal_url": session.url}), 200

    except Exception as e:
        logger.error(f"Error creating portal session: {e}")
        return jsonify({"error": "An unexpected error occurred"}), 500


# =============================================================================
# ROUTE 3: GET SUBSCRIPTION STATUS
# =============================================================================

@billing_bp.route('/billing/status', methods=['GET'])
@require_auth
def get_status():
    """
    Get the subscription status for the authenticated business.

    Returns:
        {
            "status": "trialing",
            "is_active": true,
            "trial_ends_at": "2026-03-01T...",
            "subscription_ends_at": "2026-03-01T...",
            "days_remaining_in_trial": 12,
            "stripe_customer_id": "cus_xxx"
        }
    """
    try:
        business = request.business
        if not business:
            return jsonify({"error": "Business profile not found"}), 404

        business_id = business.get('id')
        status = stripe_service.get_subscription_status(business_id)

        return jsonify(status), 200

    except Exception as e:
        logger.error(f"Error getting billing status: {e}")
        return jsonify({"error": "An unexpected error occurred"}), 500


# =============================================================================
# ROUTE 4: STRIPE WEBHOOK
# =============================================================================

@billing_bp.route('/billing/webhook', methods=['POST'])
def stripe_webhook():
    """
    Handle incoming Stripe webhook events.

    PUBLIC endpoint — no JWT auth. Authenticity verified via Stripe signature.
    CRITICAL: Always return 200 to prevent Stripe from retrying.
    """
    logger.info("=" * 80)
    logger.info("[STRIPE_WEBHOOK] ROUTE HIT - REQUEST RECEIVED")
    logger.info("=" * 80)
    logger.info(f"[STRIPE_WEBHOOK] Headers: {dict(request.headers)}")
    logger.info(f"[STRIPE_WEBHOOK] Content-Type: {request.content_type}")

    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')

    logger.info(f"[STRIPE_WEBHOOK] Data length: {len(payload)}")
    logger.info(f"[STRIPE_WEBHOOK] Signature present: {bool(sig_header)}")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
        logger.info(f"[STRIPE_WEBHOOK] Event verified: {event.type}")
    except ValueError as e:
        logger.error(f"[STRIPE_WEBHOOK] Invalid payload: {e}")
        return jsonify({"error": "Invalid payload"}), 400
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"[STRIPE_WEBHOOK] Invalid signature: {e}")
        return jsonify({"error": "Invalid signature"}), 400

    event_type = event.type
    event_id = event.id
    logger.info(f"[WEBHOOK_DEBUG] Stripe webhook received: {event_type} ({event_id})")

    # Idempotency check
    try:
        dup = supabase.table('billing_events').select('id').eq(
            'stripe_event_id', event_id
        ).execute()
        if dup.data:
            logger.info(f"[WEBHOOK_DEBUG] Duplicate event {event_id} — skipping")
            return jsonify({"status": "already processed"}), 200
    except Exception as e:
        logger.warning(f"[WEBHOOK_DEBUG] Duplicate check failed (proceeding): {e}")

    obj = event.data.object

    # Resolve business_id from metadata or DB lookup
    logger.info(f"[WEBHOOK_DEBUG] Resolving business_id for {event_type} — "
                f"customer={getattr(obj, 'customer', None)} "
                f"subscription={getattr(obj, 'subscription', None)}")
    business_id = _resolve_business_id(event_type, obj)
    logger.info(f"[WEBHOOK_DEBUG] Resolved business_id={business_id}")

    if not business_id:
        logger.warning(f"[WEBHOOK_DEBUG] Could not resolve business_id for {event_type} ({event_id}) — skipping handlers")
        return jsonify({"status": "received", "warning": "no business_id"}), 200

    # Route to handler
    try:
        if event_type == 'customer.subscription.created':
            logger.info(f"[WEBHOOK_DEBUG] Processing subscription.created for {business_id}")
            stripe_service.handle_subscription_created(obj, business_id)

        elif event_type == 'customer.subscription.updated':
            logger.info(f"[WEBHOOK_DEBUG] Processing subscription.updated for {business_id}")
            stripe_service.handle_subscription_updated(obj, business_id)

        elif event_type == 'customer.subscription.deleted':
            logger.info(f"[WEBHOOK_DEBUG] Processing subscription.deleted for {business_id}")
            stripe_service.handle_subscription_deleted(obj, business_id)

        elif event_type == 'customer.subscription.trial_will_end':
            logger.info(f"[WEBHOOK_DEBUG] Processing trial_will_end for {business_id}")
            _handle_trial_will_end(obj, business_id)

        elif event_type == 'invoice.payment_succeeded':
            logger.info(f"[REFERRAL_DEBUG] Processing invoice.payment_succeeded for {business_id}")
            stripe_service.handle_payment_succeeded(obj, business_id)

        elif event_type == 'invoice.payment_failed':
            logger.info(f"[WEBHOOK_DEBUG] Processing payment_failed for {business_id}")
            stripe_service.handle_payment_failed(obj, business_id)

        else:
            logger.info(f"[WEBHOOK_DEBUG] Unhandled event type: {event_type}")

    except Exception as e:
        logger.exception(f"[WEBHOOK_DEBUG] Error processing {event_type} ({event_id}): {e}")
        # Always return 200 to prevent Stripe retries

    # Store event ID for idempotency (best-effort)
    try:
        supabase.table('billing_events').insert({
            'stripe_event_id': event_id,
            'business_id': business_id,
            'event_type': event_type,
        }).execute()
    except Exception as e:
        logger.warning(f"[WEBHOOK_DEBUG] Failed to store event for idempotency: {e}")

    return jsonify({"status": "received"}), 200


# =============================================================================
# PRIVATE HELPERS
# =============================================================================

def _resolve_business_id(event_type: str, obj) -> str | None:
    """
    Extract or look up the business_id for a Stripe event.

    Resolution order:
    1. Subscription metadata  (set via subscription_data.metadata at checkout)
    2. Invoice/object metadata (set via subscription_data.invoice_settings.metadata)
    3. Customer lookup         (stripe_customer_id in businesses table)

    Every metadata-sourced ID is validated against the businesses table
    before being returned — stale metadata from deleted accounts is skipped.
    """
    def _exists_in_db(bid: str) -> bool:
        try:
            r = supabase.table('businesses').select('id').eq('id', bid).limit(1).execute()
            return bool(r.data)
        except Exception:
            return False

    def _try_id(bid: str, source: str) -> str | None:
        if not bid:
            return None
        if _exists_in_db(bid):
            logger.info(f"[WEBHOOK_DEBUG] business_id resolved from {source}: {bid}")
            return bid
        logger.warning(f"[WEBHOOK_DEBUG] {source} business_id {bid} not in DB — skipping")
        return None

    # Step 1: subscription metadata (most reliable — set at checkout creation)
    if hasattr(obj, 'subscription') and obj.subscription:
        try:
            sub = stripe.Subscription.retrieve(obj.subscription)
            result = _try_id((sub.metadata or {}).get('business_id'), 'subscription metadata')
            if result:
                return result
        except Exception as e:
            logger.warning(f"Failed to retrieve subscription for business_id lookup: {e}")

    # Step 2: invoice/object metadata (set via subscription_data.invoice_settings.metadata)
    obj_metadata = getattr(obj, 'metadata', None) or {}
    result = _try_id(obj_metadata.get('business_id'), 'invoice/object metadata')
    if result:
        return result

    # Step 3: customer lookup (stripe_customer_id column in businesses)
    customer_id = getattr(obj, 'customer', None)
    if customer_id:
        try:
            rows = supabase.table('businesses').select('id').eq(
                'stripe_customer_id', customer_id
            ).execute()
            if rows.data:
                bid = rows.data[0]['id']
                logger.info(f"[WEBHOOK_DEBUG] business_id resolved from customer lookup ({customer_id}): {bid}")
                return bid
            logger.warning(f"[WEBHOOK_DEBUG] No business found for stripe_customer_id {customer_id}")
        except Exception as e:
            logger.warning(f"Failed to look up business by customer_id {customer_id}: {e}")

    # Step 4: subscription ID lookup (stripe_subscription_id column in businesses)
    # Covers invoice events where customer lookup fails but subscription was stored at creation
    subscription_id = getattr(obj, 'subscription', None)
    if not subscription_id and getattr(obj, 'object', None) == 'subscription':
        subscription_id = getattr(obj, 'id', None)
    if subscription_id:
        try:
            rows = supabase.table('businesses').select('id').eq(
                'stripe_subscription_id', subscription_id
            ).execute()
            if rows.data:
                bid = rows.data[0]['id']
                logger.info(f"[WEBHOOK_DEBUG] business_id resolved from subscription_id lookup ({subscription_id}): {bid}")
                return bid
            logger.warning(f"[WEBHOOK_DEBUG] No business found for stripe_subscription_id {subscription_id}")
        except Exception as e:
            logger.warning(f"Failed to look up business by subscription_id {subscription_id}: {e}")

    logger.error(f"[WEBHOOK_DEBUG] All resolution methods failed for {event_type}")
    return None


def _handle_trial_will_end(subscription, business_id: str) -> None:
    """
    Send a reminder email when the trial is about to end (3 days before).

    Stripe sends this event automatically 3 days before trial_end.
    """
    try:
        from app.services.email_service import send_trial_ending_email
        from datetime import datetime, timezone

        biz_result = supabase.table('businesses').select(
            'email, business_name'
        ).eq('id', business_id).execute()

        if not biz_result.data:
            return

        biz = biz_result.data[0]
        email = biz.get('email')
        if not email:
            return

        # Calculate trial end date and days remaining
        trial_end_date = 'soon'
        days_remaining = 3
        if subscription.trial_end:
            trial_end_dt = datetime.fromtimestamp(subscription.trial_end, tz=timezone.utc)
            trial_end_date = trial_end_dt.strftime('%B %d, %Y')
            delta = trial_end_dt - datetime.now(timezone.utc)
            days_remaining = max(0, delta.days)

        send_trial_ending_email(
            email=email,
            business_name=biz.get('business_name', 'your business'),
            trial_end_date=trial_end_date,
            days_remaining=days_remaining
        )
        logger.info(f"Sent trial ending reminder to business {business_id}")

    except Exception as e:
        logger.error(f"Failed to send trial ending email for business {business_id}: {e}")


# =============================================================================
# ROUTE 5: BILLING SUCCESS PAGE
# =============================================================================

@billing_pages_bp.route('/billing/success')
def billing_success():
    """Serve the success page after Stripe checkout completes."""
    return send_from_directory(FRONTEND_FOLDER, 'billing_success.html')


# =============================================================================
# ROUTE 6: BILLING CANCELED PAGE
# =============================================================================

@billing_pages_bp.route('/billing/canceled')
def billing_canceled():
    """Serve the canceled page if user exits Stripe checkout."""
    return send_from_directory(FRONTEND_FOLDER, 'billing_canceled.html')
