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

    This is a PUBLIC endpoint (no JWT auth). Authenticity is verified
    using Stripe's webhook signature.

    CRITICAL: Always return 200 to prevent Stripe from retrying.

    Events handled:
    - customer.subscription.created
    - customer.subscription.updated
    - customer.subscription.deleted
    - customer.subscription.trial_will_end
    - invoice.payment_succeeded
    - invoice.payment_failed
    """
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')

    # Verify webhook signature
    if not STRIPE_WEBHOOK_SECRET:
        logger.warning("STRIPE_WEBHOOK_SECRET not set, skipping signature verification")
        try:
            event = stripe.Event.construct_from(
                stripe.util.convert_to_stripe_object(request.get_json()),
                stripe.api_key
            )
        except Exception as e:
            logger.error(f"Failed to parse webhook payload: {e}")
            return jsonify({"error": "Invalid payload"}), 400
    else:
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, STRIPE_WEBHOOK_SECRET
            )
        except ValueError:
            logger.error("Invalid webhook payload")
            return jsonify({"error": "Invalid payload"}), 400
        except stripe.error.SignatureVerificationError:
            logger.error("Invalid webhook signature")
            return jsonify({"error": "Invalid signature"}), 400

    event_type = event.type
    event_id = event.id
    logger.info(f"Stripe webhook received: {event_type} (event_id: {event_id})")

    # Check for duplicate events (idempotency)
    try:
        dup_check = supabase.table('billing_events').select('id').eq(
            'stripe_event_id', event_id
        ).execute()
        if dup_check.data:
            logger.info(f"Duplicate webhook event {event_id} - already processed")
            return jsonify({"status": "already processed"}), 200
    except Exception as e:
        # Don't block processing if duplicate check fails
        logger.warning(f"Duplicate check failed (proceeding anyway): {e}")

    # Extract the Stripe object from the event
    obj = event.data.object

    # Resolve business_id from metadata or customer lookup
    logger.info(f"[WEBHOOK_DEBUG] Resolving business_id for {event_type} ({event_id})")
    logger.info(f"[WEBHOOK_DEBUG] obj.customer={getattr(obj, 'customer', None)} "
                f"obj.subscription={getattr(obj, 'subscription', None)}")
    business_id = _resolve_business_id(event_type, obj)
    logger.info(f"[WEBHOOK_DEBUG] Resolved business_id={business_id}")

    if not business_id:
        logger.warning(f"Could not resolve business_id for event {event_type} ({event_id})")
        return jsonify({"status": "received", "warning": "no business_id"}), 200

    # Route to the correct handler
    try:
        if event_type == 'customer.subscription.created':
            stripe_service.handle_subscription_created(obj, business_id)

        elif event_type == 'customer.subscription.updated':
            stripe_service.handle_subscription_updated(obj, business_id)

        elif event_type == 'customer.subscription.deleted':
            stripe_service.handle_subscription_deleted(obj, business_id)

        elif event_type == 'customer.subscription.trial_will_end':
            _handle_trial_will_end(obj, business_id)

        elif event_type == 'invoice.payment_succeeded':
            logger.info(f"[WEBHOOK_DEBUG] Calling handle_payment_succeeded for {business_id}")
            stripe_service.handle_payment_succeeded(obj, business_id)

        elif event_type == 'invoice.payment_failed':
            stripe_service.handle_payment_failed(obj, business_id)

        else:
            logger.debug(f"Unhandled event type: {event_type}")

    except Exception as e:
        # CRITICAL: Never return non-200 from webhook handler
        logger.exception(f"Error processing webhook {event_type} ({event_id}): {e}")

    # Store event ID for idempotency (best-effort)
    try:
        supabase.table('billing_events').insert({
            'stripe_event_id': event_id,
            'business_id': business_id,
            'event_type': event_type,
        }).execute()
    except Exception as e:
        logger.warning(f"Failed to store event ID for idempotency: {e}")

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

    # Step 3: customer lookup (most robust when metadata is stale or missing)
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
