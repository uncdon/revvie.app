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

from app.services.supabase_service import supabase
from app.services import square_service

# Load environment variables
load_dotenv()

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
        print("WARNING: SQUARE_WEBHOOK_SIGNATURE_KEY not set, skipping verification")
        # In development, you might want to skip verification
        # In production, this should return False
        return True

    if not signature:
        print("ERROR: No signature provided in webhook request")
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
        print(f"ERROR: Signature verification failed: {str(e)}")
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
    print("\n" + "=" * 60)
    print("SQUARE WEBHOOK RECEIVED")
    print("=" * 60)

    # Get raw body for signature verification
    raw_body = request.get_data()

    # Get signature from header
    signature = request.headers.get('X-Square-Hmacsha256-Signature')

    # Verify signature
    if not verify_square_signature(raw_body, signature, SQUARE_WEBHOOK_URL):
        print("ERROR: Invalid webhook signature - rejecting request")
        return jsonify({"error": "Invalid signature"}), 403

    # Parse JSON body
    try:
        event = request.get_json()
    except Exception as e:
        print(f"ERROR: Failed to parse webhook JSON: {str(e)}")
        return jsonify({"status": "ok"}), 200  # Still return 200

    # Log the event
    event_type = event.get('type', 'unknown')
    event_id = event.get('event_id', 'unknown')
    print(f"Event Type: {event_type}")
    print(f"Event ID: {event_id}")

    # Only process payment.created events
    if event_type != 'payment.created':
        print(f"Ignoring event type: {event_type}")
        return jsonify({"status": "ok", "message": f"Ignored event type: {event_type}"}), 200

    # Process the payment
    try:
        result = process_payment_created(event)
        print(f"Processing result: {result}")
        return jsonify({"status": "ok", **result}), 200

    except Exception as e:
        # Log error but still return 200 to prevent retries
        print(f"ERROR: Failed to process payment webhook: {str(e)}")
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

    print(f"Payment ID: {payment_id}")
    print(f"Location ID: {location_id}")
    print(f"Customer ID: {customer_id}")

    if not payment_id:
        return {"skipped": True, "reason": "No payment_id in event"}

    if not location_id:
        return {"skipped": True, "reason": "No location_id in event"}

    # Step 1: Find integration by location_id
    print(f"Looking up integration for location: {location_id}")
    integration_result = supabase.table('integrations').select('*').eq(
        'square_location_id', location_id
    ).eq('status', 'active').execute()

    if not integration_result.data:
        print(f"No active integration found for location: {location_id}")
        return {"skipped": True, "reason": "No integration for this location"}

    integration = integration_result.data[0]
    business_id = integration['business_id']
    settings = integration.get('settings', {})

    print(f"Found integration for business: {business_id}")

    # Step 2: Check if auto_send is enabled
    auto_send_enabled = settings.get('auto_send_enabled', True)
    if not auto_send_enabled:
        print("Auto-send is disabled for this business")
        return {"skipped": True, "reason": "Auto-send disabled"}

    # Step 3: Check for duplicate (already processed this payment)
    duplicate_check = supabase.table('queued_review_requests').select('id').eq(
        'payment_id', payment_id
    ).execute()

    if duplicate_check.data:
        print(f"Payment {payment_id} already processed - skipping duplicate")
        return {"skipped": True, "reason": "Duplicate payment"}

    # Step 4: Get customer details
    if not customer_id:
        print("No customer_id on payment - cannot send review request")
        return {"skipped": True, "reason": "No customer_id on payment"}

    # Decrypt access token
    access_token = square_service.decrypt_token(integration['access_token'])

    # Check if token needs refresh
    token_expires_at = integration.get('token_expires_at')
    if token_expires_at:
        # Parse the timestamp
        if isinstance(token_expires_at, str):
            token_expires_at = datetime.fromisoformat(token_expires_at.replace('Z', '+00:00'))

        if square_service.check_token_expiry(token_expires_at):
            print("Access token expired or expiring soon - refreshing")
            refresh_token = square_service.decrypt_token(integration['refresh_token'])
            refresh_result = square_service.refresh_access_token(refresh_token)

            if refresh_result['success']:
                # Update tokens in database
                supabase.table('integrations').update({
                    'access_token': square_service.encrypt_token(refresh_result['access_token']),
                    'refresh_token': square_service.encrypt_token(refresh_result['refresh_token']),
                    'token_expires_at': refresh_result['expires_at'].isoformat(),
                }).eq('id', integration['id']).execute()

                access_token = refresh_result['access_token']
                print("Token refreshed successfully")
            else:
                print(f"ERROR: Failed to refresh token: {refresh_result.get('error')}")
                return {"skipped": True, "reason": "Token refresh failed"}

    # Get customer info from Square
    print(f"Fetching customer details for: {customer_id}")
    customer_result = square_service.get_customer_details(access_token, customer_id)

    if not customer_result.get('success'):
        print(f"ERROR: Failed to get customer details: {customer_result.get('error')}")
        return {"skipped": True, "reason": f"Failed to get customer: {customer_result.get('error')}"}

    customer_name = customer_result.get('name', 'Customer')
    customer_email = customer_result.get('email')
    customer_phone = customer_result.get('phone')

    print(f"Customer: {customer_name}, Email: {customer_email}, Phone: {customer_phone}")

    # Check if we have email (required for review request)
    if not customer_email:
        print("Customer has no email on file - cannot send review request")
        return {"skipped": True, "reason": "No customer email"}

    # Step 5: Calculate scheduled send time
    delay_hours = settings.get('delay_hours', 2)

    # Parse payment created_at
    if created_at_str:
        payment_created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
    else:
        payment_created_at = datetime.now(timezone.utc)

    scheduled_send_at = payment_created_at + timedelta(hours=delay_hours)

    print(f"Payment created at: {payment_created_at}")
    print(f"Scheduled to send at: {scheduled_send_at} (delay: {delay_hours} hours)")

    # Step 6: Queue the review request
    queue_data = {
        'business_id': business_id,
        'customer_name': customer_name,
        'customer_email': customer_email,
        'customer_phone': customer_phone,
        'scheduled_send_at': scheduled_send_at.isoformat(),
        'status': 'queued',
        'integration_source': 'square',
        'payment_id': payment_id,
    }

    insert_result = supabase.table('queued_review_requests').insert(queue_data).execute()

    if not insert_result.data:
        print("ERROR: Failed to insert queued review request")
        return {"skipped": True, "reason": "Database insert failed"}

    queued_id = insert_result.data[0]['id']
    print(f"SUCCESS: Queued review request {queued_id}")
    print(f"  Customer: {customer_name} ({customer_email})")
    print(f"  Scheduled: {scheduled_send_at}")
    print("=" * 60 + "\n")

    return {
        "queued": True,
        "queued_review_request_id": queued_id,
        "customer_name": customer_name,
        "scheduled_send_at": scheduled_send_at.isoformat(),
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
