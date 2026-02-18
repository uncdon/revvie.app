"""
Square Webhook Handler - receives payment notifications from Square.

When a customer makes a payment at a Square-connected business, Square
sends us a webhook notification. We use this to automatically queue
review requests.

HOW WEBHOOKS WORK:
==================
1. Business connects Square to Revvie (OAuth)
2. We register a webhook URL with Square (done in Square Dashboard)
3. When a payment happens, Square sends a POST request to our webhook URL
4. We verify the request is really from Square (signature verification)
5. We extract payment info and queue a review request

SECURITY:
=========
- Webhooks are PUBLIC endpoints (no JWT auth)
- We verify authenticity using Square's webhook signature
- Square signs each request with a secret key (SQUARE_WEBHOOK_SIGNATURE_KEY)
- We compute our own signature and compare
- If they don't match, the request is rejected

IMPORTANT:
==========
- Always return 200 OK, even on errors (prevents infinite retries)
- Square will retry failed webhooks, which can cause duplicate processing
- We check for duplicate payment_ids before queueing
"""

import os
import hmac
import hashlib
import base64
from datetime import datetime, timedelta, timezone
from flask import Blueprint, request, jsonify
from dotenv import load_dotenv

from app.services.supabase_service import supabase, supabase_admin
from app.services import square_service
from app.services import duplicate_checker
from app.services.square_logger import get_square_logger, log_webhook_event, log_oauth_event

# Load environment variables
load_dotenv()

# Get logger for webhooks
logger = get_square_logger('webhooks')

# Create Blueprint
# Note: Registered without /api prefix since this is a webhook endpoint
square_webhooks_bp = Blueprint('square_webhooks', __name__)

# Square webhook signature key (from Square Developer Dashboard)
# This is different from your app secret - it's specifically for webhooks
SQUARE_WEBHOOK_SIGNATURE_KEY = os.environ.get("SQUARE_WEBHOOK_SIGNATURE_KEY")

# The URL Square uses to send webhooks (must match what's in Square Dashboard)
SQUARE_WEBHOOK_URL = os.environ.get("SQUARE_WEBHOOK_URL", "http://localhost:5001/webhooks/square")


# ============================================================================
# SIGNATURE VERIFICATION
# ============================================================================

def verify_square_signature(payload: bytes, signature: str, webhook_url: str) -> bool:
    """
    Verify that a webhook request actually came from Square.

    Square signs each webhook request using HMAC-SHA256:
    1. Combines: webhook_url + request_body
    2. Signs with the webhook signature key
    3. Base64 encodes the result
    4. Sends it in the X-Square-Hmacsha256-Signature header

    We do the same calculation and compare. If they match, it's from Square.

    Args:
        payload: The raw request body (bytes)
        signature: The signature from X-Square-Hmacsha256-Signature header
        webhook_url: The webhook URL (must match exactly what Square has)

    Returns:
        True if signature is valid, False otherwise
    """
    if not SQUARE_WEBHOOK_SIGNATURE_KEY:
        logger.warning("SQUARE_WEBHOOK_SIGNATURE_KEY not set, skipping verification")
        # In development, you might want to skip verification
        # In production, this should return False
        return True

    if not signature:
        logger.error("No signature provided in webhook request")
        return False

    try:
        # Combine URL and body (this is what Square signs)
        string_to_sign = webhook_url + payload.decode('utf-8')

        # Create HMAC-SHA256 signature
        expected_signature = hmac.new(
            key=SQUARE_WEBHOOK_SIGNATURE_KEY.encode('utf-8'),
            msg=string_to_sign.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()

        # Base64 encode
        expected_signature_b64 = base64.b64encode(expected_signature).decode('utf-8')

        # Compare (using hmac.compare_digest to prevent timing attacks)
        return hmac.compare_digest(expected_signature_b64, signature)

    except Exception as e:
        logger.error(f"Signature verification failed: {str(e)}")
        return False


# ============================================================================
# WEBHOOK ROUTE
# ============================================================================

@square_webhooks_bp.route('/square', methods=['POST'])
def handle_square_webhook():
    """
    Handle incoming webhook notifications from Square.

    Square sends various event types. We primarily care about:
    - payment.created: A new payment was made

    Headers from Square:
        X-Square-Hmacsha256-Signature: The webhook signature for verification

    Request body (JSON):
        {
            "merchant_id": "XXXXX",
            "type": "payment.created",
            "event_id": "unique-event-id",
            "created_at": "2024-01-15T10:30:00Z",
            "data": {
                "type": "payment",
                "id": "payment-id",
                "object": {
                    "payment": {
                        "id": "payment-id",
                        "location_id": "location-id",
                        "customer_id": "customer-id",
                        ...
                    }
                }
            }
        }

    Always returns 200 OK to prevent Square from retrying.
    """
    log_webhook_event('received', details={'endpoint': '/webhooks/square'})

    # Get raw body for signature verification
    raw_body = request.get_data()

    # Get signature from header
    signature = request.headers.get('X-Square-Hmacsha256-Signature')

    # Verify signature
    if not verify_square_signature(raw_body, signature, SQUARE_WEBHOOK_URL):
        log_webhook_event('signature_invalid', success=False, error="Invalid webhook signature")
        return jsonify({"error": "Invalid signature"}), 403

    log_webhook_event('signature_verified')

    # Parse JSON body
    try:
        event = request.get_json()
    except Exception as e:
        log_webhook_event('parse_error', success=False, error=f"Failed to parse JSON: {str(e)}")
        return jsonify({"status": "ok"}), 200  # Still return 200

    # Log the event
    event_type = event.get('type', 'unknown')
    event_id = event.get('event_id', 'unknown')
    log_webhook_event('event_parsed', event_id=event_id, details={'event_type': event_type})

    # Only process payment.created events
    if event_type != 'payment.created':
        log_webhook_event('event_ignored', event_id=event_id, details={'event_type': event_type})
        return jsonify({"status": "ok", "message": f"Ignored event type: {event_type}"}), 200

    # Process the payment
    try:
        result = process_payment_created(event)
        if result.get('queued'):
            log_webhook_event('processed', event_id=event_id,
                            payment_id=result.get('payment_id'),
                            details={'queued_id': result.get('queued_review_request_id')})
        elif result.get('skipped'):
            log_webhook_event('skipped', event_id=event_id,
                            details={'reason': result.get('reason')})
        return jsonify({"status": "ok", **result}), 200

    except Exception as e:
        # Log error but still return 200 to prevent retries
        log_webhook_event('process_error', event_id=event_id, success=False, error=str(e))
        logger.exception("Failed to process payment webhook")
        return jsonify({"status": "ok", "error": str(e)}), 200


def process_payment_created(event: dict) -> dict:
    """
    Process a payment.created webhook event.

    Steps:
    1. Extract payment info from the event
    2. Find the integration by location_id
    3. Get customer details from Square
    4. Check settings and duplicates
    5. Queue the review request

    Args:
        event: The webhook event data from Square

    Returns:
        Dict with processing result info
    """
    # Extract payment data from the event
    data = event.get('data', {})
    payment_data = data.get('object', {}).get('payment', {})

    payment_id = payment_data.get('id')
    location_id = payment_data.get('location_id')
    customer_id = payment_data.get('customer_id')
    created_at_str = payment_data.get('created_at')

    logger.info(f"Processing payment: payment_id={payment_id}, location_id={location_id}, customer_id={customer_id}")

    if not payment_id:
        return {"skipped": True, "reason": "No payment_id in event"}

    if not location_id:
        return {"skipped": True, "reason": "No location_id in event"}

    # Step 1: Find integration by location_id
    logger.debug(f"Looking up integration for location: {location_id}")
    integration_result = supabase.table('integrations').select('*').eq(
        'square_location_id', location_id
    ).eq('status', 'active').execute()

    if not integration_result.data:
        logger.warning(f"No active integration found for location: {location_id}")
        return {"skipped": True, "reason": "No integration for this location", "payment_id": payment_id}

    integration = integration_result.data[0]
    business_id = integration['business_id']
    settings = integration.get('settings', {})

    logger.info(f"Found integration for business: {business_id}")

    # Step 2: Check if auto_send is enabled
    auto_send_enabled = settings.get('auto_send_enabled', True)
    if not auto_send_enabled:
        logger.info(f"Auto-send is disabled for business {business_id}")
        return {"skipped": True, "reason": "Auto-send disabled", "payment_id": payment_id}

    # Step 3: Check for duplicate (already processed this payment)
    duplicate_check = supabase.table('queued_review_requests').select('id').eq(
        'payment_id', payment_id
    ).execute()

    if duplicate_check.data:
        logger.warning(f"Payment {payment_id} already processed - skipping duplicate")
        return {"skipped": True, "reason": "Duplicate payment", "payment_id": payment_id}

    # Step 4: Get customer details
    if not customer_id:
        logger.warning(f"No customer_id on payment {payment_id} - cannot send review request")
        return {"skipped": True, "reason": "No customer_id on payment", "payment_id": payment_id}

    # Get valid access token (auto-refreshes if needed)
    token_result = square_service.ensure_valid_token(integration['id'])

    if not token_result['success']:
        log_oauth_event('token_refresh', business_id=business_id, success=False,
                      error=token_result.get('error'))
        return {"skipped": True, "reason": f"Token error: {token_result.get('error')}", "payment_id": payment_id}

    access_token = token_result['access_token']

    if token_result.get('refreshed'):
        log_oauth_event('token_refresh', business_id=business_id, details={'action': 'auto_refreshed'})

    # Get customer info from Square
    logger.info(f"Fetching customer details for: {customer_id}")
    customer_result = square_service.get_customer_details(access_token, customer_id)

    if not customer_result.get('success'):
        logger.error(f"Failed to get customer details: {customer_result.get('error')}")
        return {"skipped": True, "reason": f"Failed to get customer: {customer_result.get('error')}", "payment_id": payment_id}

    customer_name = customer_result.get('name', 'Customer')
    customer_email = customer_result.get('email')
    customer_phone = customer_result.get('phone')

    logger.info(f"Customer found: name={customer_name}, has_email={bool(customer_email)}, has_phone={bool(customer_phone)}")

    # Check if we have at least one contact method (email or phone)
    if not customer_email and not customer_phone:
        logger.warning(f"Customer {customer_id} has no email or phone on file - cannot send review request")
        return {"skipped": True, "reason": "No customer email or phone", "payment_id": payment_id}

    # Determine send method based on available contact info
    if customer_email and customer_phone:
        send_method = 'both'
    elif customer_email:
        send_method = 'email'
    else:
        send_method = 'sms'

    logger.info(f"Send method determined: {send_method}")

    # Step 4.5: Check for duplicate review requests (cooldown period)
    dup_check = duplicate_checker.can_send_review_request(
        business_id=business_id,
        customer_email=customer_email,
        customer_phone=customer_phone
    )

    if not dup_check['can_send']:
        logger.info(
            f"Square webhook: Skipping review request for "
            f"{customer_email or customer_phone} - {dup_check.get('reason', 'duplicate')} "
            f"(cooldown: {dup_check.get('cooldown_days', 30)} days)"
        )
        return {
            "skipped": True,
            "reason": f"Duplicate: {dup_check.get('reason', 'recently contacted')}",
            "payment_id": payment_id,
        }

    # Step 5: Calculate scheduled send time
    delay_hours = settings.get('delay_hours', 2)

    # Parse payment created_at
    if created_at_str:
        payment_created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
    else:
        payment_created_at = datetime.now(timezone.utc)

    scheduled_send_at = payment_created_at + timedelta(hours=delay_hours)

    logger.info(f"Review request scheduled: send_at={scheduled_send_at.isoformat()}, delay_hours={delay_hours}")

    # Step 6: Queue the review request
    queue_data = {
        'business_id': business_id,
        'customer_name': customer_name,
        'customer_email': customer_email,
        'customer_phone': customer_phone,
        'scheduled_send_at': scheduled_send_at.isoformat(),
        'status': 'queued',
        'method': send_method,
        'integration_source': 'square',
        'payment_id': payment_id,
    }

    # Use admin client to bypass RLS for queue insert
    insert_result = supabase_admin.table('queued_review_requests').insert(queue_data).execute()

    if not insert_result.data:
        logger.error(f"Failed to insert queued review request for payment {payment_id}")
        return {"skipped": True, "reason": "Database insert failed", "payment_id": payment_id}

    queued_id = insert_result.data[0]['id']
    log_webhook_event('queued', payment_id=payment_id, business_id=business_id,
                     details={'queued_id': queued_id, 'scheduled_send_at': scheduled_send_at.isoformat()})

    return {
        "queued": True,
        "queued_review_request_id": queued_id,
        "customer_name": customer_name,
        "scheduled_send_at": scheduled_send_at.isoformat(),
        "payment_id": payment_id,
    }


# ============================================================================
# TEST ENDPOINT (for development only)
# ============================================================================

@square_webhooks_bp.route('/square/test', methods=['GET'])
def test_webhook_endpoint():
    """
    Simple test endpoint to verify the webhook route is accessible.
    This is useful during development to confirm the route is registered.

    Returns basic info about the webhook configuration.
    """
    return jsonify({
        "status": "ok",
        "message": "Square webhook endpoint is active",
        "webhook_url": SQUARE_WEBHOOK_URL,
        "signature_key_configured": bool(SQUARE_WEBHOOK_SIGNATURE_KEY),
    }), 200


# ============================================================================
# SETUP INSTRUCTIONS
# ============================================================================
#
# 1. Add these to your .env file:
#
#    # Square Webhook Configuration
#    SQUARE_WEBHOOK_SIGNATURE_KEY=your-webhook-signature-key
#    SQUARE_WEBHOOK_URL=https://your-domain.com/webhooks/square
#
# 2. In Square Developer Dashboard:
#    - Go to your app -> Webhooks
#    - Add webhook subscription
#    - URL: https://your-domain.com/webhooks/square
#    - Events: payment.created
#    - Copy the "Signature Key" to your .env file
#
# 3. For local testing with ngrok:
#    - Run: ngrok http 5001
#    - Copy the https URL (e.g., https://abc123.ngrok.io)
#    - Update SQUARE_WEBHOOK_URL in .env
#    - Update webhook URL in Square Dashboard
#
# 4. Test the webhook:
#    - Make a test payment in Square Sandbox
#    - Check your Flask logs for webhook processing
#    - Check queued_review_requests table for new entries
