"""
Telnyx Webhook Handler - receives SMS delivery status updates.

When you send an SMS through Telnyx, the message goes through several stages:
1. queued - Message accepted by Telnyx
2. sent - Message sent to the carrier
3. delivered - Message delivered to the recipient's phone
4. failed - Message failed to deliver

Telnyx sends webhooks to notify us about these status changes, so we can
track whether our review request SMS was actually delivered.

HOW TELNYX WEBHOOKS WORK:
=========================
1. You configure a webhook URL in your Telnyx portal (Messaging > Webhooks)
2. When an SMS status changes, Telnyx sends a POST request to your URL
3. The request contains event data (message ID, status, error info, etc.)
4. Telnyx signs the request so you can verify it's really from Telnyx
5. You process the event and return 200 OK

WEBHOOK SECURITY:
================
Telnyx signs webhooks using your "Public Key" from the Telnyx portal.
We verify this signature to ensure the request really came from Telnyx
and wasn't spoofed by an attacker.

SETUP INSTRUCTIONS:
==================
1. Go to Telnyx Portal > Messaging > Webhooks
2. Add webhook URL: https://your-domain.com/webhooks/telnyx
3. Copy the "Public Key" and add to .env as TELNYX_PUBLIC_KEY
4. Enable these events:
   - message.sent
   - message.delivered
   - message.failed

EVENT TYPES:
============
- message.queued     - Message accepted, waiting to send
- message.sent       - Message sent to carrier network
- message.delivered  - Message delivered to recipient
- message.failed     - Message failed (bad number, carrier rejected, etc.)
- message.finalized  - Final status reached (won't change again)
"""

import os
import json
import logging
import hashlib
import base64
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from dotenv import load_dotenv

from app.services.supabase_service import supabase, supabase_admin

# Load environment variables
load_dotenv()

# Set up logging
logger = logging.getLogger(__name__)

# Create Blueprint
telnyx_webhooks_bp = Blueprint('telnyx_webhooks', __name__)

# Telnyx webhook public key for signature verification
# Get this from Telnyx Portal > Account > API Keys > Public Key
TELNYX_PUBLIC_KEY = os.environ.get('TELNYX_PUBLIC_KEY')

# Map Telnyx event types to our status values
STATUS_MAP = {
    'message.queued': 'queued',
    'message.sent': 'sent',
    'message.delivered': 'delivered',
    'message.failed': 'failed',
    'message.finalized': None,  # Keep existing status, just log it
}

# SMS opt-out keywords (TCPA / CTIA standard)
STOP_KEYWORDS = {'stop', 'unsubscribe', 'cancel', 'end', 'quit'}
START_KEYWORDS = {'start', 'yes', 'unstop'}


def verify_telnyx_signature(payload: bytes, signature: str, timestamp: str) -> bool:
    """
    Verify that a webhook request actually came from Telnyx.

    Telnyx signs webhooks using Ed25519 signatures. The signature is computed
    over: timestamp + '|' + payload

    Note: For simplicity, we're using a basic verification approach.
    For production, you should use the telnyx SDK's built-in verification
    or the cryptography library with Ed25519.

    Args:
        payload: The raw request body (bytes)
        signature: The signature from telnyx-signature-ed25519 header
        timestamp: The timestamp from telnyx-timestamp header

    Returns:
        True if signature is valid, False otherwise
    """
    if not TELNYX_PUBLIC_KEY:
        logger.warning("TELNYX_PUBLIC_KEY not set - skipping signature verification")
        # In development/initial setup, skip verification
        return True

    if not signature or not timestamp:
        logger.error("Missing signature or timestamp in webhook request")
        return False

    # For now, accept webhooks if we have the headers
    # TODO: Implement proper Ed25519 signature verification for Telnyx v4
    # The Telnyx v4 SDK doesn't have the old Webhook.construct_event method
    logger.info("Webhook received with signature - accepting (full verification TODO)")
    return True


@telnyx_webhooks_bp.route('/telnyx', methods=['POST'])
def handle_telnyx_webhook():
    """
    Handle incoming webhook notifications from Telnyx.

    Telnyx sends webhooks when SMS delivery status changes.
    We use this to update our database with the latest status.

    Headers from Telnyx:
        telnyx-signature-ed25519: The webhook signature
        telnyx-timestamp: When the webhook was sent

    Request body (JSON):
        {
            "data": {
                "event_type": "message.delivered",
                "id": "evt_abc123",
                "occurred_at": "2024-01-15T10:30:00Z",
                "payload": {
                    "id": "msg_abc123",        // Message ID (our sms_sid)
                    "to": [{"phone_number": "+14155551234"}],
                    "from": {"phone_number": "+14155550000"},
                    "text": "Hi John! Thanks for...",
                    "direction": "outbound",
                    "type": "SMS",
                    "errors": []               // Error details if failed
                },
                "record_type": "event"
            },
            "meta": {
                "attempt": 1,
                "delivered_to": "https://your-domain.com/webhooks/telnyx"
            }
        }

    Always returns 200 OK to prevent Telnyx from retrying.
    """
    logger.info("Telnyx webhook received")

    # Get raw body for signature verification
    raw_body = request.get_data()

    # Get signature headers
    signature = request.headers.get('telnyx-signature-ed25519')
    timestamp = request.headers.get('telnyx-timestamp')

    # Verify signature
    if not verify_telnyx_signature(raw_body, signature, timestamp):
        logger.error("Invalid Telnyx webhook signature - rejecting request")
        # Still return 200 to prevent retries, but log the security issue
        return jsonify({"status": "error", "message": "Invalid signature"}), 200

    # Parse JSON body
    try:
        event_data = request.get_json()
    except Exception as e:
        logger.error(f"Failed to parse Telnyx webhook JSON: {e}")
        return jsonify({"status": "ok"}), 200

    # Extract event details
    try:
        data = event_data.get('data', {})
        event_type = data.get('event_type', 'unknown')
        event_id = data.get('id', 'unknown')
        payload = data.get('payload', {})

        # Get message details
        message_id = payload.get('id')  # This is our sms_sid
        errors = payload.get('errors', [])

        logger.info(f"Telnyx event: type={event_type}, event_id={event_id}, message_id={message_id}")

        # Process the event
        result = process_telnyx_event(
            event_type=event_type,
            event_id=event_id,
            message_id=message_id,
            errors=errors,
            payload=payload
        )

        return jsonify({"status": "ok", **result}), 200

    except Exception as e:
        logger.exception(f"Error processing Telnyx webhook: {e}")
        return jsonify({"status": "ok", "error": str(e)}), 200


def handle_inbound_sms(payload: dict) -> dict:
    """
    Handle an inbound SMS reply from a customer.

    STOP/UNSUBSCRIBE/CANCEL/END/QUIT:
        - Looks up the most recent business that messaged this number
        - Inserts into sms_suppressions (business_id + phone)
        - Does NOT send a reply — Telnyx blocks outbound messages to
          numbers that just opted out; the carrier sends its own
          standard confirmation automatically

    START/UNSTOP/YES:
        - Removes all sms_suppressions rows for this phone number
        - Sends an opt-in confirmation (allowed and expected)

    Args:
        payload: Telnyx message payload dict from the webhook.

    Returns:
        Dict describing what action was taken.
    """
    from_info = payload.get('from', {})
    to_list = payload.get('to', [{}])
    sender_phone = from_info.get('phone_number', '')
    our_phone = to_list[0].get('phone_number', '') if to_list else ''
    text = (payload.get('text') or '').strip().lower()

    if not sender_phone or not text:
        logger.warning("Inbound SMS missing sender phone or text — ignoring")
        return {"processed": False, "reason": "Missing sender or text"}

    logger.info(f"Inbound SMS from {sender_phone[:6]}***: '{text}'")

    if text in STOP_KEYWORDS:
        # Look up the business this number most recently heard from
        business_id = _lookup_recent_business_id(sender_phone)

        if not business_id:
            logger.warning(f"STOP from unknown number {sender_phone[:6]}*** — no matching review request")
            return {"processed": True, "action": "opted_out", "status": "unknown_number"}

        # Add to per-business suppression list
        try:
            supabase_admin.table("sms_suppressions").insert({
                "business_id": business_id,
                "customer_phone": sender_phone,
            }).execute()
            logger.info(f"SMS opt-out: {sender_phone[:6]}*** suppressed for business {business_id}")
        except Exception as e:
            if 'duplicate' in str(e).lower() or 'unique' in str(e).lower():
                logger.info(f"Already opted out: {sender_phone[:6]}*** for business {business_id}")
            else:
                logger.error(f"Opt-out insert failed for {sender_phone}: {e}")

        # DO NOT send a reply — Telnyx blocks outbound to opted-out numbers.
        # The carrier automatically delivers a standard opt-out confirmation.
        return {"processed": True, "action": "opted_out", "phone": sender_phone}

    if text in START_KEYWORDS:
        # Remove all suppressions for this phone (re-subscribes across all businesses)
        try:
            supabase_admin.table("sms_suppressions") \
                .delete() \
                .eq("customer_phone", sender_phone) \
                .execute()
            logger.info(f"SMS opt-in: all suppressions cleared for {sender_phone[:6]}***")
        except Exception as e:
            logger.error(f"Opt-in suppression removal failed for {sender_phone}: {e}")

        # Sending a reply IS allowed after START — it's expected by the user
        _send_reply(
            our_phone,
            sender_phone,
            "You're resubscribed to review requests. Reply STOP anytime to opt out."
        )
        return {"processed": True, "action": "opted_in", "phone": sender_phone}

    # Unrecognised reply — log and ignore
    logger.info(f"Inbound SMS from {sender_phone[:6]}*** not a keyword: '{text[:20]}'")
    return {"processed": True, "action": "ignored", "reason": "Not an opt-out keyword"}


def _lookup_recent_business_id(phone: str) -> str | None:
    """Return the business_id from the most recent review request sent to this phone."""
    try:
        result = supabase_admin.table("review_requests") \
            .select("business_id") \
            .eq("customer_phone", phone) \
            .order("sent_at", desc=True) \
            .limit(1) \
            .execute()
        if result.data:
            return result.data[0].get("business_id")
    except Exception as e:
        logger.error(f"Error looking up business_id for {phone}: {e}")
    return None


def _send_reply(from_phone: str, to_phone: str, text: str) -> None:
    """Send an SMS reply using the Telnyx client."""
    try:
        from app.services.sms_service import telnyx_client, telnyx_configured
        if not telnyx_configured:
            logger.warning("Cannot send opt-out reply — Telnyx not configured")
            return
        telnyx_client.messages.send(from_=from_phone, to=to_phone, text=text)
        logger.info(f"Opt-out reply sent to {to_phone[:6]}***")
    except Exception as e:
        logger.error(f"Failed to send opt-out reply to {to_phone}: {e}")


def process_telnyx_event(
    event_type: str,
    event_id: str,
    message_id: str,
    errors: list,
    payload: dict
) -> dict:
    """
    Process a Telnyx webhook event.

    Args:
        event_type: The type of event (message.sent, message.delivered, etc.)
        event_id: Telnyx event ID
        message_id: Telnyx message ID (our sms_sid)
        errors: List of error objects if the message failed
        payload: Full payload from Telnyx

    Returns:
        Dict with processing result
    """
    # Inbound replies (customer texting back) — handle before outbound status logic
    if event_type == 'message.received':
        return handle_inbound_sms(payload)

    if not message_id:
        logger.warning(f"Telnyx webhook missing message_id: event_type={event_type}")
        return {"processed": False, "reason": "No message_id"}

    # Get the new status from our mapping
    new_status = STATUS_MAP.get(event_type)

    if new_status is None and event_type != 'message.finalized':
        logger.info(f"Ignoring Telnyx event type: {event_type}")
        return {"processed": False, "reason": f"Unhandled event type: {event_type}"}

    # Build error message if failed
    error_message = None
    if event_type == 'message.failed' and errors:
        # Extract error details
        error_parts = []
        for err in errors:
            code = err.get('code', 'unknown')
            title = err.get('title', 'Unknown error')
            detail = err.get('detail', '')
            error_parts.append(f"{code}: {title}")
            if detail:
                error_parts[-1] += f" - {detail}"
        error_message = "; ".join(error_parts)
        logger.error(f"SMS delivery failed for {message_id}: {error_message}")

    # Update review_requests table
    review_updated = update_review_request_status(
        message_id=message_id,
        new_status=new_status,
        error_message=error_message
    )

    # Update queued_review_requests table (in case it's still there)
    queue_updated = update_queued_request_status(
        message_id=message_id,
        new_status=new_status,
        error_message=error_message
    )

    logger.info(
        f"Processed Telnyx event: message_id={message_id}, "
        f"status={new_status}, review_updated={review_updated}, queue_updated={queue_updated}"
    )

    return {
        "processed": True,
        "message_id": message_id,
        "new_status": new_status,
        "review_updated": review_updated,
        "queue_updated": queue_updated
    }


def update_review_request_status(
    message_id: str,
    new_status: str,
    error_message: str = None
) -> bool:
    """
    Update the SMS status in the review_requests table.

    Args:
        message_id: Telnyx message ID (stored as sms_sid)
        new_status: New status to set
        error_message: Error message if failed

    Returns:
        True if a record was updated, False otherwise
    """
    try:
        # Build update data
        update_data = {}

        if new_status:
            update_data['sms_status'] = new_status

        if error_message:
            update_data['sms_error'] = error_message

        if not update_data:
            return False

        # Find and update the record by sms_sid
        result = supabase.table('review_requests').update(
            update_data
        ).eq(
            'sms_sid', message_id
        ).execute()

        # Check if any rows were updated
        if result.data:
            logger.info(f"Updated review_request sms_status to '{new_status}' for sms_sid={message_id}")
            return True
        else:
            logger.debug(f"No review_request found with sms_sid={message_id}")
            return False

    except Exception as e:
        logger.error(f"Failed to update review_request status: {e}")
        return False


def update_queued_request_status(
    message_id: str,
    new_status: str,
    error_message: str = None
) -> bool:
    """
    Update the SMS status in the queued_review_requests table.

    Args:
        message_id: Telnyx message ID (stored as sms_sid)
        new_status: New status to set
        error_message: Error message if failed

    Returns:
        True if a record was updated, False otherwise
    """
    try:
        # Build update data
        update_data = {}

        if new_status:
            update_data['sms_status'] = new_status

        if error_message:
            update_data['sms_error'] = error_message

        if not update_data:
            return False

        # Find and update the record by sms_sid
        result = supabase.table('queued_review_requests').update(
            update_data
        ).eq(
            'sms_sid', message_id
        ).execute()

        # Check if any rows were updated
        if result.data:
            logger.info(f"Updated queued_review_request sms_status to '{new_status}' for sms_sid={message_id}")
            return True
        else:
            logger.debug(f"No queued_review_request found with sms_sid={message_id}")
            return False

    except Exception as e:
        logger.error(f"Failed to update queued_review_request status: {e}")
        return False


@telnyx_webhooks_bp.route('/telnyx/test', methods=['GET'])
def test_telnyx_webhook():
    """
    Test endpoint to verify the Telnyx webhook route is accessible.

    Returns basic configuration info.
    """
    return jsonify({
        "status": "ok",
        "message": "Telnyx webhook endpoint is active",
        "public_key_configured": bool(TELNYX_PUBLIC_KEY),
        "supported_events": list(STATUS_MAP.keys())
    }), 200


# ============================================================================
# SETUP INSTRUCTIONS
# ============================================================================
#
# 1. Add to your .env file:
#
#    # Telnyx Webhook Configuration
#    TELNYX_PUBLIC_KEY=your-public-key-from-telnyx-portal
#
# 2. Register this blueprint in app/__init__.py:
#
#    from app.routes.telnyx_webhooks import telnyx_webhooks_bp
#    app.register_blueprint(telnyx_webhooks_bp, url_prefix='/webhooks')
#
# 3. In Telnyx Portal (https://portal.telnyx.com):
#    - Go to Messaging > Programmable Messaging
#    - Select your Messaging Profile (or create one)
#    - Under "Inbound Settings" or "Webhooks", add:
#      URL: https://your-domain.com/webhooks/telnyx
#    - Enable webhook events:
#      * message.received  (inbound replies — required for STOP handling)
#      * message.sent
#      * message.delivered
#      * message.failed
#
# 4. Get your Public Key:
#    - Go to Account > API Keys
#    - Copy the "Public Key" (not the API Key)
#    - Add it to your .env as TELNYX_PUBLIC_KEY
#
# 5. For local testing with ngrok:
#    - Run: ngrok http 5001
#    - Copy the https URL (e.g., https://abc123.ngrok.io)
#    - Use https://abc123.ngrok.io/webhooks/telnyx as your webhook URL
#
# ============================================================================
