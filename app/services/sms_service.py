"""
SMS Service - sends text messages via Telnyx.

HOW TELNYX SMS WORKS:
====================
Telnyx is a cloud communications platform (similar to Twilio but often cheaper).
Here's how SMS sending works:

1. You create a Telnyx account at telnyx.com
2. Telnyx gives you:
   - API Key: Your secret authentication key (starts with "KEY...")
   - Phone Number: A phone number you purchase to send SMS from
   - Messaging Profile ID: Groups your phone numbers for messaging (optional)

3. When you want to send an SMS:
   - Your code calls Telnyx's API with the API key
   - Telnyx verifies your credentials
   - Telnyx sends the SMS from your Telnyx number to the recipient
   - Telnyx returns a confirmation with a message ID

4. Telnyx charges per SMS segment:
   - 1 segment = 160 characters (standard SMS)
   - Longer messages are split into multiple segments
   - Each segment costs money, so keep messages short!

5. Delivery status webhooks (optional):
   - Telnyx can notify your app when SMS is delivered/failed
   - Configure webhook URL in Telnyx portal

PHONE NUMBER FORMAT (E.164):
============================
Telnyx requires E.164 format: +[country code][number]
Examples:
  - US:  +14155551234 (country code 1)
  - UK:  +447911123456 (country code 44)
  - AU:  +61412345678 (country code 61)

IMPORTANT: Always include the + and country code!

SMS CHARACTER LIMITS:
====================
- Standard SMS: 160 characters = 1 segment
- With special characters (emojis, etc.): 70 characters = 1 segment
- Keep review request messages under 160 chars to save money!

SETUP INSTRUCTIONS:
==================
1. Sign up at https://telnyx.com
2. Go to API Keys section, create a new API key
3. Go to Numbers section, buy a phone number with SMS capability
4. Add to your .env file:
   TELNYX_API_KEY=KEYxxxxxxxxxxxxxxxxxxxxxxxx
   TELNYX_PHONE_NUMBER=+14155551234
"""

import os
import re
import logging
from dotenv import load_dotenv

from app.services.supabase_service import supabase_admin

# Load environment variables
load_dotenv()

# Set up logging for debugging
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

# Get Telnyx credentials from environment variables
# These should be in your .env file - NEVER commit them to git!
TELNYX_API_KEY = os.environ.get('TELNYX_API_KEY')
TELNYX_PHONE_NUMBER = os.environ.get('TELNYX_PHONE_NUMBER')

# Optional: Messaging Profile ID (for advanced routing)
TELNYX_MESSAGING_PROFILE_ID = os.environ.get('TELNYX_MESSAGING_PROFILE_ID')

# Required TCPA opt-out footer — must appear in every review request SMS
OPT_OUT_FOOTER = "\nReply STOP to opt out"

# Initialize Telnyx client (v4.x uses client-based API)
telnyx_configured = False
telnyx_client = None

if TELNYX_API_KEY and TELNYX_PHONE_NUMBER:
    try:
        from telnyx import Telnyx
        telnyx_client = Telnyx(api_key=TELNYX_API_KEY)
        telnyx_configured = True
        logger.info("Telnyx SMS service initialized successfully")
    except ImportError:
        logger.error("Telnyx SDK not installed. Run: pip install telnyx")
    except Exception as e:
        logger.error(f"Failed to initialize Telnyx: {e}")
else:
    logger.warning("Telnyx credentials not configured. Set TELNYX_API_KEY and TELNYX_PHONE_NUMBER in .env")


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def validate_phone_number(phone: str) -> tuple[bool, str]:
    """
    Validate and format a phone number to E.164 format.

    E.164 format is the international standard: +[country][number]
    Example: +14155551234 (US), +447911123456 (UK)

    Args:
        phone: Phone number in any format

    Returns:
        Tuple of (is_valid, formatted_number_or_error_message)

    Examples:
        validate_phone_number("(415) 555-1234")     -> (True, "+14155551234")
        validate_phone_number("+1 415 555 1234")    -> (True, "+14155551234")
        validate_phone_number("415-555-1234")       -> (True, "+14155551234")
        validate_phone_number("123")                -> (False, "Invalid phone...")
    """
    if not phone:
        return False, "Phone number is required"

    # Remove all non-digit characters except +
    cleaned = ''.join(c for c in str(phone) if c.isdigit() or c == '+')

    # If starts with +, validate it has enough digits
    if cleaned.startswith('+'):
        # E.164 numbers are 8-15 digits after the +
        digits_only = cleaned[1:]
        if len(digits_only) < 8 or len(digits_only) > 15:
            return False, f"Invalid phone number length: {phone}"
        return True, cleaned

    # No + prefix - assume US number
    # US numbers should be 10 digits (or 11 with leading 1)
    if len(cleaned) == 10:
        return True, '+1' + cleaned
    elif len(cleaned) == 11 and cleaned.startswith('1'):
        return True, '+' + cleaned
    else:
        return False, f"Invalid US phone number format: {phone}. Expected 10 digits."


def truncate_message(message: str, max_length: int = 160) -> str:
    """
    Truncate a message to fit in a single SMS segment.

    Why? SMS messages over 160 characters are split into multiple segments,
    and you pay for each segment. Keeping messages short saves money!

    Args:
        message: The message text
        max_length: Maximum characters (default 160 for standard SMS)

    Returns:
        Truncated message with "..." if it was shortened
    """
    if len(message) <= max_length:
        return message

    # Truncate and add ellipsis
    return message[:max_length - 3] + "..."


def is_phone_opted_out(phone: str, business_id: str = None) -> bool:
    """
    Check if a phone number has opted out of SMS messages for a given business.

    Args:
        phone: Phone number in any format (will be normalised to E.164)
        business_id: The business UUID to check suppression against.
                     If omitted, falls back to False (allow send).

    Returns:
        True if the number is in sms_suppressions for this business, False otherwise.
        Defaults to False (allow send) on any lookup error.
    """
    if not business_id:
        return False

    is_valid, formatted = validate_phone_number(phone)
    if not is_valid:
        return False

    try:
        result = supabase_admin.table("sms_suppressions") \
            .select("id") \
            .eq("business_id", business_id) \
            .eq("customer_phone", formatted) \
            .limit(1) \
            .execute()
        return bool(result.data)
    except Exception as e:
        logger.error(f"Error checking SMS suppression for {formatted}: {e}")
        return False


# ============================================================================
# MAIN SMS FUNCTIONS
# ============================================================================

def send_sms(to_phone: str, message: str) -> dict:
    """
    Send an SMS message via Telnyx.

    This is the core function that actually sends the SMS. It:
    1. Validates the phone number format
    2. Sends the message via Telnyx API
    3. Returns the result with message ID for tracking

    Args:
        to_phone: Recipient's phone number (any format, will be converted to E.164)
        message: The text message to send

    Returns:
        dict with:
            - success: True if SMS was sent, False if failed
            - message_id: Telnyx's unique ID for tracking (if successful)
            - status: "sent", "queued", or error description
            - error: Detailed error message (if failed)
            - error_code: Telnyx error code (if applicable)

    Example:
        result = send_sms("+14155551234", "Hello from Revvie!")

        if result['success']:
            print(f"SMS sent! ID: {result['message_id']}")
            # Save message_id to database for tracking delivery status
        else:
            print(f"Failed: {result['error']}")
    """
    # Check if Telnyx is properly configured
    if not telnyx_configured:
        logger.error("Attempted to send SMS but Telnyx is not configured")
        return {
            'success': False,
            'message_id': None,
            'status': 'error',
            'error': 'Telnyx is not configured. Check TELNYX_API_KEY and TELNYX_PHONE_NUMBER in .env'
        }

    # Validate phone number
    is_valid, phone_result = validate_phone_number(to_phone)
    if not is_valid:
        logger.warning(f"Invalid phone number: {to_phone} - {phone_result}")
        return {
            'success': False,
            'message_id': None,
            'status': 'error',
            'error': phone_result
        }

    formatted_phone = phone_result

    # Validate message
    if not message or not message.strip():
        return {
            'success': False,
            'message_id': None,
            'status': 'error',
            'error': 'Message content is required'
        }

    # Log the attempt (but don't log the full message for privacy)
    logger.info(f"Sending SMS to {formatted_phone[:6]}***{formatted_phone[-2:]} ({len(message)} chars)")

    try:
        # Send SMS via Telnyx API (v4.x client-based API)
        response = telnyx_client.messages.send(
            from_=TELNYX_PHONE_NUMBER,  # Your Telnyx phone number
            to=formatted_phone,          # Recipient's phone number
            text=message,                # The message content
        )

        # Success! Extract the message details from response
        message_id = response.data.id if hasattr(response, 'data') else getattr(response, 'id', None)

        logger.info(f"SMS sent successfully. Message ID: {message_id}")

        return {
            'success': True,
            'message_id': message_id,
            'status': 'sent',
            'to': formatted_phone,
            'from': TELNYX_PHONE_NUMBER,
            'segments': 1
        }

    except Exception as e:
        # Catch all errors - Telnyx v4 uses different exception structure
        error_message = str(e)
        logger.exception(f"Error sending SMS: {error_message}")
        return {
            'success': False,
            'message_id': None,
            'status': 'error',
            'error': f"Unexpected error: {str(e)}",
            'error_code': 'unknown'
        }


def send_review_request_sms(
    customer_name: str,
    customer_phone: str,
    business_name: str,
    review_url: str,
    business_id: str = None
) -> dict:
    """
    Send a review request SMS to a customer.

    This function formats a professional, friendly review request message
    and sends it via SMS. The message is optimized to:
    - Be personal (uses customer's name)
    - Be concise (under 160 characters to avoid multi-segment charges)
    - Include a clear call-to-action (the review URL)

    Args:
        customer_name: Customer's first name (e.g., "John")
        customer_phone: Customer's phone number (any format)
        business_name: Your business name (e.g., "Joe's Coffee")
        review_url: Direct link to Google review page

    Returns:
        Same format as send_sms():
        {
            'success': True/False,
            'message_id': 'msg_xxx' or None,
            'status': 'sent' or error,
            'error': error details if failed
        }

    Example:
        result = send_review_request_sms(
            customer_name="John",
            customer_phone="+14155551234",
            business_name="Joe's Coffee",
            review_url="https://g.page/r/xxx/review"
        )

        if result['success']:
            # Save to database
            save_review_request(message_id=result['message_id'], status='sent')
    """
    # Validate required fields
    if not customer_phone:
        return {
            'success': False,
            'message_id': None,
            'status': 'error',
            'error': 'Customer phone number is required'
        }

    if not review_url:
        return {
            'success': False,
            'message_id': None,
            'status': 'error',
            'error': 'Review URL is required'
        }

    # Check usage cap before sending
    if business_id:
        from app.services import usage_tracker
        usage_check = usage_tracker.can_send_sms(business_id)
        if not usage_check['can_send']:
            logger.warning(f"SMS blocked for business {business_id}: {usage_check['reason']}")
            return {
                'success': False,
                'message_id': None,
                'status': 'error',
                'error': 'monthly_sms_limit_reached',
                'message': f"Monthly SMS limit reached ({usage_check['current_usage']}/{usage_check['monthly_cap']}). Resets {usage_check['resets_on']}.",
                'limit_info': usage_check
            }

    # Use defaults if name/business not provided
    name = (customer_name or "there").split()[0]  # First name only
    business = business_name or "us"

    # Check suppression list before building/sending
    if is_phone_opted_out(customer_phone, business_id):
        logger.info(f"SMS blocked for {customer_phone}: number is on opt-out suppression list")
        return {
            'success': False,
            'message_id': None,
            'status': 'opted_out',
            'error': 'Phone number has opted out of SMS messages'
        }

    # Build the message.
    # The STOP footer is legally required (TCPA) and must appear in every SMS.
    # This means messages will typically span 2 SMS segments — that is expected.
    # Template selection still picks the most descriptive variant that fits
    # within 320 chars (2 segments).
    MAX_LENGTH = 320

    # Short template (preferred)
    short_template = (
        f"Thanks for visiting {business}! Leave a review: {review_url}"
        f"{OPT_OUT_FOOTER}"
    )

    # Medium template (if short is too long - very long business names)
    medium_template = (
        f"Leave a review for {business}: {review_url}"
        f"{OPT_OUT_FOOTER}"
    )

    # Minimal template (last resort)
    minimal_template = f"Review {business}: {review_url}{OPT_OUT_FOOTER}"

    # Pick the best template that fits within MAX_LENGTH
    if len(short_template) <= MAX_LENGTH:
        message = short_template
    elif len(medium_template) <= MAX_LENGTH:
        message = medium_template
        logger.info(f"Using medium template for {customer_phone} (short was {len(short_template)} chars)")
    elif len(minimal_template) <= MAX_LENGTH:
        message = minimal_template
        logger.warning(f"Using minimal template for {customer_phone} - review URL is very long")
    else:
        # URL is too long even for minimal template + footer
        logger.error(f"Review URL too long ({len(review_url)} chars): {review_url[:50]}...")
        return {
            'success': False,
            'message_id': None,
            'status': 'error',
            'error': f'Review URL is too long ({len(review_url)} chars). Max ~270 chars.'
        }

    logger.info(f"Sending review request SMS to {customer_phone} for {business} ({len(message)} chars)")

    # Send the SMS
    result = send_sms(customer_phone, message)

    # Increment usage counter on success
    if result['success'] and business_id:
        from app.services import usage_tracker
        usage_tracker.increment_sms_count(business_id)

        warnings = usage_tracker.check_approaching_limit(business_id)
        if warnings['sms_warning']:
            logger.info(f"Business {business_id} approaching SMS limit: {warnings['sms_percentage']:.1f}%")

    return result


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def format_phone_number(phone: str) -> str:
    """
    Format a phone number to E.164 format.

    This is a convenience wrapper around validate_phone_number()
    that just returns the formatted number or the original if invalid.

    Args:
        phone: Phone number in any format

    Returns:
        Phone number in E.164 format (+1234567890)
    """
    is_valid, result = validate_phone_number(phone)
    return result if is_valid else phone


def get_sms_status() -> dict:
    """
    Get the current status of the SMS service.

    Useful for health checks and debugging.

    Returns:
        dict with configuration status
    """
    return {
        'configured': telnyx_configured,
        'provider': 'telnyx',
        'phone_number': TELNYX_PHONE_NUMBER[:6] + '****' if TELNYX_PHONE_NUMBER else None,
        'api_key_set': bool(TELNYX_API_KEY),
    }


# ============================================================================
# TESTING (only runs when file is executed directly)
# ============================================================================

if __name__ == '__main__':
    # Quick test - only runs if you execute this file directly:
    # python -m app.services.sms_service

    print("SMS Service Status:")
    print(get_sms_status())

    print("\nPhone number validation tests:")
    test_numbers = [
        "(415) 555-1234",
        "+1 415 555 1234",
        "4155551234",
        "+447911123456",
        "123",  # Invalid
        "",     # Invalid
    ]

    for num in test_numbers:
        is_valid, result = validate_phone_number(num)
        status = "VALID" if is_valid else "INVALID"
        print(f"  {num:20} -> {status}: {result}")
