"""
SMS Service - sends text messages via Twilio.

HOW TWILIO SMS WORKS:
====================
1. You create a Twilio account at twilio.com
2. Twilio gives you:
   - Account SID: Your account identifier (like a username)
   - Auth Token: Your secret key (like a password)
   - Phone Number: A phone number Twilio provides to send SMS from

3. When you want to send an SMS:
   - Your code calls Twilio's API
   - Twilio receives the request and verifies your credentials
   - Twilio sends the SMS from your Twilio number to the recipient
   - Twilio returns a confirmation with a message SID (unique ID)

4. Twilio charges per SMS sent (check their pricing)

PHONE NUMBER FORMAT:
===================
Twilio requires E.164 format: +[country code][number]
Examples:
  - US: +14155551234
  - UK: +447911123456
  - AU: +61412345678
"""

import os
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from dotenv import load_dotenv

load_dotenv()

# Get Twilio credentials from environment variables
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER')

# Validate credentials exist
if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER]):
    print("Warning: Twilio credentials not fully configured in .env file")
    twilio_client = None
else:
    # Create the Twilio client
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def send_sms(to_phone: str, message: str) -> dict:
    """
    Send an SMS message via Twilio.

    Args:
        to_phone: Recipient's phone number (E.164 format: +1234567890)
        message: The text message to send (max 1600 characters)

    Returns:
        dict with:
            - success: True/False
            - message_sid: Twilio's unique ID for this message (if successful)
            - error: Error message (if failed)

    Example:
        result = send_sms("+14155551234", "Hello from Revvie!")
        if result['success']:
            print(f"Sent! Message ID: {result['message_sid']}")
        else:
            print(f"Failed: {result['error']}")
    """
    # Check if Twilio is configured
    if not twilio_client:
        return {
            'success': False,
            'error': 'Twilio is not configured. Check your .env file.'
        }

    # Validate inputs
    if not to_phone:
        return {
            'success': False,
            'error': 'Recipient phone number is required'
        }

    if not message:
        return {
            'success': False,
            'error': 'Message content is required'
        }

    # Clean up phone number (basic formatting)
    to_phone = to_phone.strip()
    if not to_phone.startswith('+'):
        # Assume US number if no country code
        to_phone = '+1' + to_phone.replace('-', '').replace(' ', '').replace('(', '').replace(')', '')

    try:
        # Send the SMS via Twilio
        twilio_message = twilio_client.messages.create(
            body=message,
            from_=TWILIO_PHONE_NUMBER,
            to=to_phone
        )

        # Success! Return the message details
        return {
            'success': True,
            'message_sid': twilio_message.sid,
            'status': twilio_message.status,  # 'queued', 'sent', 'delivered', etc.
            'to': twilio_message.to,
            'from': twilio_message.from_
        }

    except TwilioRestException as e:
        # Twilio-specific error (invalid number, insufficient funds, etc.)
        return {
            'success': False,
            'error': str(e.msg),
            'error_code': e.code
        }
    except Exception as e:
        # Other errors (network issues, etc.)
        return {
            'success': False,
            'error': str(e)
        }


def format_phone_number(phone: str) -> str:
    """
    Format a phone number to E.164 format.

    Args:
        phone: Phone number in any format

    Returns:
        Phone number in E.164 format (+1234567890)
    """
    # Remove all non-digit characters except +
    cleaned = ''.join(c for c in phone if c.isdigit() or c == '+')

    # If doesn't start with +, assume US number
    if not cleaned.startswith('+'):
        # Remove leading 1 if present, then add +1
        if cleaned.startswith('1') and len(cleaned) == 11:
            cleaned = '+' + cleaned
        else:
            cleaned = '+1' + cleaned

    return cleaned
