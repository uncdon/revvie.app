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
