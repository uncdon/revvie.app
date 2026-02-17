"""
Email service using SendGrid API.

SendGrid is a cloud-based email delivery service. Instead of running your own
email server (which is complex and often blocked by spam filters), SendGrid
handles all the email infrastructure for you.

How it works:
1. You sign up at sendgrid.com and verify your sender email/domain
2. SendGrid gives you an API key (like a password for your app)
3. Your app sends HTTP requests to SendGrid's API with the email details
4. SendGrid delivers the email and handles bounces, spam reports, etc.

This service wraps the SendGrid Python library to make sending emails easy.
"""

import os
import certifi
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content, TrackingSettings, ClickTracking

# Fix SSL certificate issues on some systems (e.g., macOS)
os.environ.setdefault('SSL_CERT_FILE', certifi.where())
os.environ.setdefault('REQUESTS_CA_BUNDLE', certifi.where())


def send_email(to_email: str, subject: str, html_body: str) -> dict:
    """
    Send an email using SendGrid.

    Args:
        to_email: The recipient's email address (e.g., "customer@example.com")
        subject: The email subject line (e.g., "Your Review Request")
        html_body: The email content in HTML format (e.g., "<h1>Hello!</h1>")

    Returns:
        dict: {
            "success": True/False,
            "message": "Description of what happened",
            "status_code": 202 (if successful)
        }

    Example:
        result = send_email(
            to_email="customer@example.com",
            subject="How was your visit?",
            html_body="<p>Please leave us a review!</p>"
        )
        if result["success"]:
            print("Email sent!")
    """

    # Get credentials from environment variables
    # These are loaded from .env file by python-dotenv
    api_key = os.environ.get('SENDGRID_API_KEY')
    from_email = os.environ.get('SENDGRID_FROM_EMAIL')

    # Check if credentials are configured
    if not api_key:
        return {
            "success": False,
            "message": "SendGrid API key not configured. Add SENDGRID_API_KEY to .env file.",
            "status_code": None
        }

    if not from_email:
        return {
            "success": False,
            "message": "Sender email not configured. Add SENDGRID_FROM_EMAIL to .env file.",
            "status_code": None
        }

    try:
        # Create the email message object
        # Mail() is SendGrid's helper class that structures the email properly
        message = Mail(
            from_email=Email(from_email),  # Who the email is from
            to_emails=To(to_email),        # Who the email is going to
            subject=subject,                # Email subject line
            html_content=Content("text/html", html_body)  # Email body (HTML)
        )

        # Disable SendGrid click tracking so review URLs aren't wrapped
        # in sendgrid.net redirects (we'll build our own link tracking)
        message.tracking_settings = TrackingSettings(
            click_tracking=ClickTracking(enable=False, enable_text=False)
        )

        # Create the SendGrid client with your API key
        # This client handles authentication and HTTP requests to SendGrid
        sg = SendGridAPIClient(api_key)

        # Send the email via SendGrid's API
        # This makes an HTTP POST request to api.sendgrid.com
        response = sg.send(message)

        # Status code 202 means "Accepted" - SendGrid received the email
        # and will deliver it (email delivery is asynchronous)
        if response.status_code == 202:
            return {
                "success": True,
                "message": f"Email sent successfully to {to_email}",
                "status_code": response.status_code
            }
        else:
            # Unexpected status code
            return {
                "success": False,
                "message": f"Unexpected response from SendGrid: {response.status_code}",
                "status_code": response.status_code
            }

    except Exception as e:
        # Handle any errors (network issues, invalid API key, etc.)
        error_message = str(e)

        # Provide helpful error messages for common issues
        if "401" in error_message or "Unauthorized" in error_message:
            return {
                "success": False,
                "message": "Invalid SendGrid API key. Check your SENDGRID_API_KEY.",
                "status_code": 401
            }
        elif "403" in error_message or "Forbidden" in error_message:
            return {
                "success": False,
                "message": "SendGrid rejected the request. Verify your sender email is authenticated.",
                "status_code": 403
            }

        return {
            "success": False,
            "message": f"Failed to send email: {error_message}",
            "status_code": None
        }


def send_review_request_email(
    customer_name: str,
    customer_email: str,
    business_name: str,
    review_url: str
) -> dict:
    """
    Send a review request email to a customer.

    This sends a professionally designed email asking the customer
    to leave a Google review for your business.

    Args:
        customer_name: Customer's first name (e.g., "John")
        customer_email: Customer's email address
        business_name: Your business name (e.g., "Joe's Coffee Shop")
        review_url: The Google review URL (from generate_google_review_url)

    Returns:
        dict: Same format as send_email() - success, message, status_code

    Example:
        result = send_review_request_email(
            customer_name="John",
            customer_email="john@example.com",
            business_name="Joe's Coffee",
            review_url="https://search.google.com/local/writereview?placeid=..."
        )
    """

    # Create the email subject with customer's name
    subject = f"We'd love your feedback, {customer_name}!"

    # Build the HTML email body
    # We use inline CSS because email clients don't support external stylesheets
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="margin: 0; padding: 0; font-family: Arial, Helvetica, sans-serif; background-color: #f4f4f4;">
        <!-- Main container -->
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f4f4f4;">
            <tr>
                <td align="center" style="padding: 40px 20px;">
                    <!-- Email card -->
                    <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">

                        <!-- Header -->
                        <tr>
                            <td style="background-color: #4F46E5; padding: 30px 40px; border-radius: 8px 8px 0 0;">
                                <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 600;">
                                    {business_name}
                                </h1>
                            </td>
                        </tr>

                        <!-- Body -->
                        <tr>
                            <td style="padding: 40px;">
                                <!-- Greeting -->
                                <h2 style="margin: 0 0 20px 0; color: #1f2937; font-size: 22px;">
                                    Hi {customer_name}!
                                </h2>

                                <!-- Thank you message -->
                                <p style="margin: 0 0 25px 0; color: #4b5563; font-size: 16px; line-height: 1.6;">
                                    Thanks for visiting <strong>{business_name}</strong>! We hope you had a great experience with us.
                                </p>

                                <!-- Request message -->
                                <p style="margin: 0 0 30px 0; color: #4b5563; font-size: 16px; line-height: 1.6;">
                                    Your feedback helps us improve and helps other customers discover our business. Would you take a moment to share your experience?
                                </p>

                                <!-- CTA Button -->
                                <table role="presentation" cellspacing="0" cellpadding="0" style="margin: 0 auto;">
                                    <tr>
                                        <td style="border-radius: 6px; background-color: #4F46E5;">
                                            <a clicktracking="off" href="{review_url}" target="_blank" style="display: inline-block; padding: 16px 32px; color: #ffffff; text-decoration: none; font-size: 16px; font-weight: 600;">
                                                Leave us a Google Review
                                            </a>
                                        </td>
                                    </tr>
                                </table>

                                <!-- Star rating visual -->
                                <p style="margin: 30px 0 0 0; text-align: center; font-size: 28px;">
                                    ⭐⭐⭐⭐⭐
                                </p>

                                <!-- Thank you note -->
                                <p style="margin: 25px 0 0 0; color: #6b7280; font-size: 14px; text-align: center;">
                                    Thank you for your support!
                                </p>
                            </td>
                        </tr>

                        <!-- Footer -->
                        <tr>
                            <td style="padding: 20px 40px; background-color: #f9fafb; border-radius: 0 0 8px 8px; border-top: 1px solid #e5e7eb;">
                                <p style="margin: 0; color: #9ca3af; font-size: 12px; text-align: center;">
                                    This email was sent by {business_name}.<br>
                                    You received this because you recently visited us.
                                </p>
                            </td>
                        </tr>

                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

    # Use our existing send_email function to actually send it
    return send_email(
        to_email=customer_email,
        subject=subject,
        html_body=html_body
    )


# =============================================================================
# BILLING EMAILS
# =============================================================================

APP_BASE_URL = os.environ.get('APP_BASE_URL', 'http://localhost:5000')


def send_trial_welcome_email(email: str, business_name: str, trial_end_date: str) -> dict:
    """
    Send a welcome email when a business starts their 14-day free trial.

    Args:
        email: Business owner's email
        business_name: Name of the business
        trial_end_date: Human-readable trial end date (e.g., "March 1, 2026")

    Returns:
        dict: Same format as send_email()
    """
    subject = "Your 14-day free trial has started!"

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="margin: 0; padding: 0; font-family: Arial, Helvetica, sans-serif; background-color: #f4f4f4;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f4f4f4;">
            <tr>
                <td align="center" style="padding: 40px 20px;">
                    <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">

                        <!-- Header -->
                        <tr>
                            <td style="background-color: #4F46E5; padding: 30px 40px; border-radius: 8px 8px 0 0;">
                                <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 600;">
                                    *revvie
                                </h1>
                            </td>
                        </tr>

                        <!-- Body -->
                        <tr>
                            <td style="padding: 40px;">
                                <h2 style="margin: 0 0 20px 0; color: #1f2937; font-size: 22px;">
                                    Welcome to *revvie!
                                </h2>

                                <p style="margin: 0 0 25px 0; color: #4b5563; font-size: 16px; line-height: 1.6;">
                                    Your 14-day free trial for <strong>{business_name}</strong> is now active.
                                    You won't be charged until <strong>{trial_end_date}</strong>.
                                </p>

                                <p style="margin: 0 0 10px 0; color: #4b5563; font-size: 16px; line-height: 1.6;">
                                    Here's what you can do during your trial:
                                </p>

                                <table role="presentation" cellspacing="0" cellpadding="0" style="margin: 0 0 25px 0;">
                                    <tr>
                                        <td style="padding: 6px 0; color: #4b5563; font-size: 15px;">&#10003; Send unlimited review requests</td>
                                    </tr>
                                    <tr>
                                        <td style="padding: 6px 0; color: #4b5563; font-size: 15px;">&#10003; Import customers via CSV</td>
                                    </tr>
                                    <tr>
                                        <td style="padding: 6px 0; color: #4b5563; font-size: 15px;">&#10003; Connect Square integration</td>
                                    </tr>
                                    <tr>
                                        <td style="padding: 6px 0; color: #4b5563; font-size: 15px;">&#10003; View click analytics</td>
                                    </tr>
                                </table>

                                <!-- CTA Button -->
                                <table role="presentation" cellspacing="0" cellpadding="0" style="margin: 0 auto;">
                                    <tr>
                                        <td style="border-radius: 6px; background-color: #4F46E5;">
                                            <a href="{APP_BASE_URL}/dashboard" target="_blank" style="display: inline-block; padding: 16px 32px; color: #ffffff; text-decoration: none; font-size: 16px; font-weight: 600;">
                                                Go to Dashboard &rarr;
                                            </a>
                                        </td>
                                    </tr>
                                </table>

                                <p style="margin: 25px 0 0 0; color: #6b7280; font-size: 14px; text-align: center;">
                                    Questions? Just reply to this email.
                                </p>
                            </td>
                        </tr>

                        <!-- Footer -->
                        <tr>
                            <td style="padding: 20px 40px; background-color: #f9fafb; border-radius: 0 0 8px 8px; border-top: 1px solid #e5e7eb;">
                                <p style="margin: 0; color: #9ca3af; font-size: 12px; text-align: center;">
                                    *revvie &mdash; Get more Google reviews, automatically.
                                </p>
                            </td>
                        </tr>

                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

    return send_email(to_email=email, subject=subject, html_body=html_body)


def send_trial_ending_email(email: str, business_name: str, trial_end_date: str, days_remaining: int) -> dict:
    """
    Send a reminder email when the trial is about to end.

    Triggered by Stripe's trial_will_end event (3 days before expiry).

    Args:
        email: Business owner's email
        business_name: Name of the business
        trial_end_date: Human-readable trial end date
        days_remaining: Number of days left in trial

    Returns:
        dict: Same format as send_email()
    """
    subject = f"Your *revvie trial ends in {days_remaining} days"

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="margin: 0; padding: 0; font-family: Arial, Helvetica, sans-serif; background-color: #f4f4f4;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f4f4f4;">
            <tr>
                <td align="center" style="padding: 40px 20px;">
                    <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">

                        <!-- Header -->
                        <tr>
                            <td style="background-color: #4F46E5; padding: 30px 40px; border-radius: 8px 8px 0 0;">
                                <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 600;">
                                    *revvie
                                </h1>
                            </td>
                        </tr>

                        <!-- Body -->
                        <tr>
                            <td style="padding: 40px;">
                                <h2 style="margin: 0 0 20px 0; color: #1f2937; font-size: 22px;">
                                    Your trial is ending soon
                                </h2>

                                <p style="margin: 0 0 25px 0; color: #4b5563; font-size: 16px; line-height: 1.6;">
                                    Your free trial for <strong>{business_name}</strong> ends on
                                    <strong>{trial_end_date}</strong> ({days_remaining} days from now).
                                    After that, you'll be charged <strong>$79/month</strong> to continue using *revvie.
                                </p>

                                <p style="margin: 0 0 25px 0; color: #4b5563; font-size: 16px; line-height: 1.6;">
                                    You've been doing great! Keep the momentum going &mdash; your customers
                                    are already seeing your review requests and clicking through.
                                    Don't let that progress stop.
                                </p>

                                <!-- CTA Buttons -->
                                <table role="presentation" cellspacing="0" cellpadding="0" style="margin: 0 auto 15px auto;">
                                    <tr>
                                        <td style="border-radius: 6px; background-color: #4F46E5;">
                                            <a href="{APP_BASE_URL}/dashboard" target="_blank" style="display: inline-block; padding: 16px 32px; color: #ffffff; text-decoration: none; font-size: 16px; font-weight: 600;">
                                                Manage Billing &rarr;
                                            </a>
                                        </td>
                                    </tr>
                                </table>

                                <p style="margin: 0; text-align: center;">
                                    <a href="{APP_BASE_URL}/dashboard" style="color: #4F46E5; text-decoration: none; font-size: 14px;">
                                        Continue to Dashboard
                                    </a>
                                </p>
                            </td>
                        </tr>

                        <!-- Footer -->
                        <tr>
                            <td style="padding: 20px 40px; background-color: #f9fafb; border-radius: 0 0 8px 8px; border-top: 1px solid #e5e7eb;">
                                <p style="margin: 0; color: #9ca3af; font-size: 12px; text-align: center;">
                                    *revvie &mdash; Get more Google reviews, automatically.
                                </p>
                            </td>
                        </tr>

                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

    return send_email(to_email=email, subject=subject, html_body=html_body)


def send_payment_failed_email(email: str, business_name: str) -> dict:
    """
    Send a notification when a payment fails.

    Args:
        email: Business owner's email
        business_name: Name of the business

    Returns:
        dict: Same format as send_email()
    """
    subject = "Action required: Payment failed for *revvie"

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="margin: 0; padding: 0; font-family: Arial, Helvetica, sans-serif; background-color: #f4f4f4;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f4f4f4;">
            <tr>
                <td align="center" style="padding: 40px 20px;">
                    <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">

                        <!-- Header -->
                        <tr>
                            <td style="background-color: #DC2626; padding: 30px 40px; border-radius: 8px 8px 0 0;">
                                <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 600;">
                                    *revvie
                                </h1>
                            </td>
                        </tr>

                        <!-- Body -->
                        <tr>
                            <td style="padding: 40px;">
                                <h2 style="margin: 0 0 20px 0; color: #1f2937; font-size: 22px;">
                                    Payment Failed
                                </h2>

                                <p style="margin: 0 0 25px 0; color: #4b5563; font-size: 16px; line-height: 1.6;">
                                    We couldn't process the payment for your <strong>{business_name}</strong> *revvie subscription.
                                    Please update your payment method to keep your account active.
                                </p>

                                <p style="margin: 0 0 30px 0; color: #4b5563; font-size: 16px; line-height: 1.6;">
                                    Your account will be paused if payment isn't received within 7 days.
                                    Update your card now to avoid any interruption.
                                </p>

                                <!-- CTA Button -->
                                <table role="presentation" cellspacing="0" cellpadding="0" style="margin: 0 auto;">
                                    <tr>
                                        <td style="border-radius: 6px; background-color: #DC2626;">
                                            <a href="{APP_BASE_URL}/dashboard" target="_blank" style="display: inline-block; padding: 16px 32px; color: #ffffff; text-decoration: none; font-size: 16px; font-weight: 600;">
                                                Update Payment Method &rarr;
                                            </a>
                                        </td>
                                    </tr>
                                </table>

                                <p style="margin: 25px 0 0 0; color: #6b7280; font-size: 14px; text-align: center;">
                                    Questions? Just reply to this email.
                                </p>
                            </td>
                        </tr>

                        <!-- Footer -->
                        <tr>
                            <td style="padding: 20px 40px; background-color: #f9fafb; border-radius: 0 0 8px 8px; border-top: 1px solid #e5e7eb;">
                                <p style="margin: 0; color: #9ca3af; font-size: 12px; text-align: center;">
                                    *revvie &mdash; Get more Google reviews, automatically.
                                </p>
                            </td>
                        </tr>

                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

    return send_email(to_email=email, subject=subject, html_body=html_body)


def send_subscription_canceled_email(email: str, business_name: str) -> dict:
    """
    Send a notification when a subscription is canceled.

    Args:
        email: Business owner's email
        business_name: Name of the business

    Returns:
        dict: Same format as send_email()
    """
    subject = "Your *revvie subscription has been canceled"

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="margin: 0; padding: 0; font-family: Arial, Helvetica, sans-serif; background-color: #f4f4f4;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f4f4f4;">
            <tr>
                <td align="center" style="padding: 40px 20px;">
                    <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">

                        <!-- Header -->
                        <tr>
                            <td style="background-color: #4F46E5; padding: 30px 40px; border-radius: 8px 8px 0 0;">
                                <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 600;">
                                    *revvie
                                </h1>
                            </td>
                        </tr>

                        <!-- Body -->
                        <tr>
                            <td style="padding: 40px;">
                                <h2 style="margin: 0 0 20px 0; color: #1f2937; font-size: 22px;">
                                    We're sorry to see you go
                                </h2>

                                <p style="margin: 0 0 25px 0; color: #4b5563; font-size: 16px; line-height: 1.6;">
                                    Your *revvie subscription for <strong>{business_name}</strong> has been canceled.
                                    You'll retain access until the end of your current billing period.
                                </p>

                                <p style="margin: 0 0 30px 0; color: #4b5563; font-size: 16px; line-height: 1.6;">
                                    Changed your mind? You can reactivate anytime.
                                </p>

                                <!-- CTA Button -->
                                <table role="presentation" cellspacing="0" cellpadding="0" style="margin: 0 auto;">
                                    <tr>
                                        <td style="border-radius: 6px; background-color: #4F46E5;">
                                            <a href="{APP_BASE_URL}/dashboard" target="_blank" style="display: inline-block; padding: 16px 32px; color: #ffffff; text-decoration: none; font-size: 16px; font-weight: 600;">
                                                Reactivate &rarr;
                                            </a>
                                        </td>
                                    </tr>
                                </table>

                                <p style="margin: 25px 0 0 0; color: #6b7280; font-size: 14px; text-align: center;">
                                    We'd love your feedback &mdash; just reply to this email.
                                </p>
                            </td>
                        </tr>

                        <!-- Footer -->
                        <tr>
                            <td style="padding: 20px 40px; background-color: #f9fafb; border-radius: 0 0 8px 8px; border-top: 1px solid #e5e7eb;">
                                <p style="margin: 0; color: #9ca3af; font-size: 12px; text-align: center;">
                                    *revvie &mdash; Get more Google reviews, automatically.
                                </p>
                            </td>
                        </tr>

                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

    return send_email(to_email=email, subject=subject, html_body=html_body)


# ============================================================================
# REFERRAL EMAILS
# ============================================================================

def send_referral_welcome_email(email: str, business_name: str, credit_amount: float = 40) -> dict:
    """
    Send a welcome email when someone signs up via a referral link.
    Tells the new user about their credit.

    Args:
        email: New user's email
        business_name: Name of the new business
        credit_amount: Dollar amount of credit (default $40)

    Returns:
        dict: Same format as send_email()
    """
    subject = f"You have ${int(credit_amount)} in *revvie credit! 🎉"

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="margin: 0; padding: 0; font-family: Arial, Helvetica, sans-serif; background-color: #f4f4f4;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f4f4f4;">
            <tr>
                <td align="center" style="padding: 40px 20px;">
                    <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">

                        <!-- Header -->
                        <tr>
                            <td style="background-color: #4F46E5; padding: 30px 40px; border-radius: 8px 8px 0 0;">
                                <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 600;">
                                    *revvie
                                </h1>
                            </td>
                        </tr>

                        <!-- Body -->
                        <tr>
                            <td style="padding: 40px;">
                                <h2 style="margin: 0 0 20px 0; color: #1f2937; font-size: 22px;">
                                    Welcome to *revvie! 🎉
                                </h2>

                                <p style="margin: 0 0 25px 0; color: #4b5563; font-size: 16px; line-height: 1.6;">
                                    Great news &mdash; because you were referred by a friend, <strong>{business_name}</strong> has
                                    <strong>${int(credit_amount)} in credit</strong> waiting for you!
                                </p>

                                <p style="margin: 0 0 30px 0; color: #4b5563; font-size: 16px; line-height: 1.6;">
                                    Your credit will be automatically applied to your subscription. That's
                                    free reviews, on us.
                                </p>

                                <!-- CTA Button -->
                                <table role="presentation" cellspacing="0" cellpadding="0" style="margin: 0 auto;">
                                    <tr>
                                        <td style="border-radius: 6px; background-color: #07B5F5;">
                                            <a href="{APP_BASE_URL}/dashboard" target="_blank" style="display: inline-block; padding: 16px 32px; color: #ffffff; text-decoration: none; font-size: 16px; font-weight: 600;">
                                                Go to Dashboard &rarr;
                                            </a>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>

                        <!-- Footer -->
                        <tr>
                            <td style="padding: 20px 40px; background-color: #f9fafb; border-radius: 0 0 8px 8px; border-top: 1px solid #e5e7eb;">
                                <p style="margin: 0; color: #9ca3af; font-size: 12px; text-align: center;">
                                    *revvie &mdash; Get more Google reviews, automatically.
                                </p>
                            </td>
                        </tr>

                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

    return send_email(to_email=email, subject=subject, html_body=html_body)


def send_referral_reward_email(email: str, business_name: str, referred_name: str, credit_amount: float = 40) -> dict:
    """
    Send a reward email when someone the user referred completes signup.
    Tells the referrer they earned credit.

    Args:
        email: Referrer's email
        business_name: Referrer's business name
        referred_name: Name of the business that just signed up
        credit_amount: Dollar amount of credit earned (default $40)

    Returns:
        dict: Same format as send_email()
    """
    subject = f"You earned ${int(credit_amount)}! {referred_name} just joined *revvie 🎉"

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="margin: 0; padding: 0; font-family: Arial, Helvetica, sans-serif; background-color: #f4f4f4;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f4f4f4;">
            <tr>
                <td align="center" style="padding: 40px 20px;">
                    <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">

                        <!-- Header -->
                        <tr>
                            <td style="background-color: #4F46E5; padding: 30px 40px; border-radius: 8px 8px 0 0;">
                                <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 600;">
                                    *revvie
                                </h1>
                            </td>
                        </tr>

                        <!-- Body -->
                        <tr>
                            <td style="padding: 40px;">
                                <h2 style="margin: 0 0 20px 0; color: #1f2937; font-size: 22px;">
                                    You earned ${int(credit_amount)}! 💰
                                </h2>

                                <p style="margin: 0 0 25px 0; color: #4b5563; font-size: 16px; line-height: 1.6;">
                                    Great news, <strong>{business_name}</strong> &mdash; your referral just paid off!
                                </p>

                                <!-- Referral detail card -->
                                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin: 0 0 25px 0;">
                                    <tr>
                                        <td style="background-color: #f0fdf4; border: 1px solid #dcfce7; border-radius: 8px; padding: 20px;">
                                            <p style="margin: 0 0 8px 0; color: #166534; font-size: 14px; font-weight: 600;">
                                                Referral Complete
                                            </p>
                                            <p style="margin: 0 0 4px 0; color: #4b5563; font-size: 15px;">
                                                <strong>{referred_name}</strong> just signed up for *revvie.
                                            </p>
                                            <p style="margin: 0; color: #166534; font-size: 18px; font-weight: 700;">
                                                +${int(credit_amount)} credit added to your account
                                            </p>
                                        </td>
                                    </tr>
                                </table>

                                <p style="margin: 0 0 30px 0; color: #4b5563; font-size: 16px; line-height: 1.6;">
                                    Your credit will be automatically applied to your next bill. Keep
                                    referring friends to earn even more!
                                </p>

                                <!-- CTA Button -->
                                <table role="presentation" cellspacing="0" cellpadding="0" style="margin: 0 auto;">
                                    <tr>
                                        <td style="border-radius: 6px; background-color: #07B5F5;">
                                            <a href="{APP_BASE_URL}/dashboard" target="_blank" style="display: inline-block; padding: 16px 32px; color: #ffffff; text-decoration: none; font-size: 16px; font-weight: 600;">
                                                View Your Referrals &rarr;
                                            </a>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>

                        <!-- Footer -->
                        <tr>
                            <td style="padding: 20px 40px; background-color: #f9fafb; border-radius: 0 0 8px 8px; border-top: 1px solid #e5e7eb;">
                                <p style="margin: 0; color: #9ca3af; font-size: 12px; text-align: center;">
                                    *revvie &mdash; Get more Google reviews, automatically.
                                </p>
                            </td>
                        </tr>

                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

    return send_email(to_email=email, subject=subject, html_body=html_body)


def send_referral_reminder_email(email: str, business_name: str, referral_link: str, pending_count: int) -> dict:
    """
    Send a reminder email about pending referrals.

    Args:
        email: Business owner's email
        business_name: Name of the business
        referral_link: The user's unique referral link
        pending_count: Number of pending (incomplete) referrals

    Returns:
        dict: Same format as send_email()
    """
    subject = f"You have {pending_count} pending referral{'s' if pending_count != 1 else ''} on *revvie"

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="margin: 0; padding: 0; font-family: Arial, Helvetica, sans-serif; background-color: #f4f4f4;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f4f4f4;">
            <tr>
                <td align="center" style="padding: 40px 20px;">
                    <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">

                        <!-- Header -->
                        <tr>
                            <td style="background-color: #4F46E5; padding: 30px 40px; border-radius: 8px 8px 0 0;">
                                <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 600;">
                                    *revvie
                                </h1>
                            </td>
                        </tr>

                        <!-- Body -->
                        <tr>
                            <td style="padding: 40px;">
                                <h2 style="margin: 0 0 20px 0; color: #1f2937; font-size: 22px;">
                                    Your referrals are waiting!
                                </h2>

                                <p style="margin: 0 0 25px 0; color: #4b5563; font-size: 16px; line-height: 1.6;">
                                    Hey <strong>{business_name}</strong> &mdash; you have <strong>{pending_count}
                                    pending referral{'s' if pending_count != 1 else ''}</strong> that haven't completed
                                    signup yet. Each one is worth <strong>$40 in credit</strong> for both of you!
                                </p>

                                <p style="margin: 0 0 25px 0; color: #4b5563; font-size: 16px; line-height: 1.6;">
                                    Share your referral link to earn more:
                                </p>

                                <!-- Referral link box -->
                                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin: 0 0 30px 0;">
                                    <tr>
                                        <td style="background-color: #f0f9ff; border: 1px solid #bae6fd; border-radius: 8px; padding: 16px; text-align: center;">
                                            <a href="{referral_link}" target="_blank" style="color: #0369a1; font-size: 15px; font-weight: 600; text-decoration: none; word-break: break-all;">
                                                {referral_link}
                                            </a>
                                        </td>
                                    </tr>
                                </table>

                                <!-- CTA Button -->
                                <table role="presentation" cellspacing="0" cellpadding="0" style="margin: 0 auto;">
                                    <tr>
                                        <td style="border-radius: 6px; background-color: #07B5F5;">
                                            <a href="{APP_BASE_URL}/dashboard" target="_blank" style="display: inline-block; padding: 16px 32px; color: #ffffff; text-decoration: none; font-size: 16px; font-weight: 600;">
                                                View Referrals &rarr;
                                            </a>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>

                        <!-- Footer -->
                        <tr>
                            <td style="padding: 20px 40px; background-color: #f9fafb; border-radius: 0 0 8px 8px; border-top: 1px solid #e5e7eb;">
                                <p style="margin: 0; color: #9ca3af; font-size: 12px; text-align: center;">
                                    *revvie &mdash; Get more Google reviews, automatically.
                                </p>
                            </td>
                        </tr>

                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

    return send_email(to_email=email, subject=subject, html_body=html_body)
