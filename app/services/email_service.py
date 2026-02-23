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
import logging
import certifi

logger = logging.getLogger(__name__)
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content, TrackingSettings, ClickTracking

# Fix SSL certificate issues on some systems (e.g., macOS)
os.environ.setdefault('SSL_CERT_FILE', certifi.where())
os.environ.setdefault('REQUESTS_CA_BUNDLE', certifi.where())

APP_BASE_URL = os.environ.get('APP_BASE_URL', 'http://localhost:5000')


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


# =============================================================================
# TEMPLATE HELPER
# =============================================================================

def render_email_template(subject: str, content: str, footer_content: str = "") -> str:
    """
    Render the branded Revvie email template with the given content.

    Reads app/templates/email_base.html and substitutes the three
    {{ placeholder }} tokens. All emails should go through this function
    to keep branding consistent.

    Args:
        subject: Email subject line (used in <title>)
        content: Main body HTML — injected into the card content area
        footer_content: Optional footer HTML shown above the address line

    Returns:
        Complete HTML email string ready to pass to send_email()
    """
    import os

    template_path = os.path.join(
        os.path.dirname(__file__), '..', 'templates', 'email_base.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        template = f.read()

    html = template.replace('{{ subject }}', subject)
    html = html.replace('{{ content }}', content)
    html = html.replace('{{ footer_content }}', footer_content)

    return html


def generate_unsubscribe_url(business_id: str, customer_email: str) -> str:
    """
    Generate a signed unsubscribe URL for a customer.

    Uses HMAC-SHA256 so the token can be verified server-side without
    storing anything in the database at send time.

    URL format:
        {APP_BASE_URL}/unsubscribe?business_id=...&email=...&token=...
    """
    import hmac
    import hashlib
    import urllib.parse

    secret = os.environ.get('SECRET_KEY', 'revvie-default-secret')
    payload = f"{business_id}:{customer_email}"
    token = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    params = urllib.parse.urlencode({
        'business_id': business_id,
        'email': customer_email,
        'token': token,
    })
    return f"{APP_BASE_URL}/unsubscribe?{params}"


def send_review_request_email(
    business_id: str,
    customer_email: str,
    customer_name: str,
    business_name: str,
    review_url: str,
) -> dict:
    """
    Send a branded review request email to a customer.

    Args:
        business_id: The business UUID (used for usage cap + unsubscribe link)
        customer_email: Customer's email address
        customer_name: Customer's first name (e.g., "John"), or None
        business_name: Business name (e.g., "Joe's Coffee Shop")
        review_url: The Google review URL

    Returns:
        dict: Same format as send_email() - success, message, status_code
    """
    # Check suppression list before sending
    try:
        from app.services.supabase_service import supabase_admin as _supa
        suppressed = _supa.table('email_suppressions').select('id') \
            .eq('business_id', business_id) \
            .eq('customer_email', customer_email.lower()) \
            .execute()
        if suppressed.data:
            import logging
            logging.getLogger(__name__).info(
                f"Email suppressed: {customer_email} from business {business_id}"
            )
            return {
                'success': False,
                'error': 'email_suppressed',
                'message': 'Customer has unsubscribed from emails',
            }
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            f"Suppression check failed for {customer_email}: {e} — sending anyway"
        )

    # Check usage cap before sending
    from app.services import usage_tracker
    usage_check = usage_tracker.can_send_email(business_id)
    if not usage_check['can_send']:
        import logging
        logging.getLogger(__name__).warning(
            f"Email blocked for business {business_id}: {usage_check['reason']}"
        )
        return {
            'success': False,
            'error': 'monthly_email_limit_reached',
            'message': (
                f"Monthly email limit reached "
                f"({usage_check['current_usage']}/{usage_check['monthly_cap']}). "
                f"Resets {usage_check['resets_on']}."
            ),
            'limit_info': usage_check,
        }

    subject = f"How was your visit to {business_name}?"

    content = f"""
    <h2 style="margin: 0 0 16px; font-size: 24px; color: #111827; font-weight: 600;">
      Hi {customer_name or 'there'}!
    </h2>

    <p style="margin: 0 0 16px; font-size: 16px; color: #374151; line-height: 24px;">
      Thanks for visiting <strong>{business_name}</strong>!
    </p>

    <p style="margin: 0 0 24px; font-size: 16px; color: #374151; line-height: 24px;">
      We'd love to hear about your experience. Your feedback helps us improve
      and helps others find great service.
    </p>

    <!-- CTA Button -->
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td align="center" style="padding: 8px 0 24px;">
          <a clicktracking="off" href="{review_url}"
             style="display: inline-block; padding: 14px 32px; background-color: #07B5F5;
                    color: #ffffff; text-decoration: none; border-radius: 8px;
                    font-size: 16px; font-weight: 600;">
            Leave a Review
          </a>
        </td>
      </tr>
    </table>

    <p style="margin: 0; font-size: 14px; color: #6b7280; line-height: 20px;">
      Takes less than 2 minutes. Thank you!
    </p>
    """

    unsubscribe_url = generate_unsubscribe_url(business_id, customer_email)
    footer = (
        f"Don't want review requests from {business_name}? "
        f'<a href="{unsubscribe_url}" style="color: #07B5F5; text-decoration: underline;">Unsubscribe</a>'
    )

    html_body = render_email_template(subject, content, footer)

    result = send_email(
        to_email=customer_email,
        subject=subject,
        html_body=html_body,
    )

    # Increment usage counter on success
    if result['success']:
        usage_tracker.increment_email_count(business_id)

        warnings = usage_tracker.check_approaching_limit(business_id)
        if warnings['email_warning']:
            import logging
            logging.getLogger(__name__).info(
                f"Business {business_id} approaching email limit: "
                f"{warnings['email_percentage']:.1f}%"
            )

    return result


# =============================================================================
# BILLING EMAILS
# =============================================================================


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
    subject = "Welcome to Revvie \u2014 Your free trial has started! \U0001f389"

    content = f"""
    <h2 style="margin: 0 0 16px; font-size: 24px; color: #111827; font-weight: 600;">
      Welcome to Revvie!
    </h2>

    <p style="margin: 0 0 16px; font-size: 16px; color: #374151; line-height: 24px;">
      Your 14-day free trial has started today.
    </p>

    <p style="margin: 0 0 4px; font-size: 16px; color: #374151; line-height: 24px;">
      <strong>Trial ends:</strong> {trial_end_date}
    </p>

    <p style="margin: 0 0 24px; font-size: 16px; color: #374151; line-height: 24px;">
      You won't be charged until {trial_end_date}.
    </p>

    <div style="background-color: #EBF8FF; border-left: 4px solid #07B5F5; padding: 16px; margin: 0 0 24px 0;">
      <p style="margin: 0 0 8px; font-size: 15px; font-weight: 600; color: #07B5F5;">
        What you can do during your trial:
      </p>
      <ul style="margin: 0; padding-left: 20px; color: #374151; font-size: 15px; line-height: 26px;">
        <li>Send unlimited review requests via SMS &amp; email</li>
        <li>Import customers from any CSV</li>
        <li>Connect Square integration</li>
        <li>Track who opens your review links</li>
      </ul>
    </div>

    <!-- CTA Button -->
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td style="padding: 8px 0 8px;">
          <a href="{APP_BASE_URL}/dashboard"
             style="display: inline-block; padding: 14px 32px; background-color: #07B5F5;
                    color: #ffffff; text-decoration: none; border-radius: 8px;
                    font-size: 16px; font-weight: 600;">
            Go to Dashboard &rarr;
          </a>
        </td>
      </tr>
    </table>
    """

    return send_email(
        to_email=email,
        subject=subject,
        html_body=render_email_template(subject, content, "Questions? Just reply to this email."),
    )


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
    subject = f"Your Revvie trial ends in {days_remaining} days"

    content = f"""
    <h2 style="margin: 0 0 16px; font-size: 24px; color: #111827; font-weight: 600;">
      Your free trial is ending soon
    </h2>

    <p style="margin: 0 0 16px; font-size: 16px; color: #374151; line-height: 24px;">
      Your trial ends <strong>{trial_end_date}</strong> ({days_remaining} days from now).
    </p>

    <p style="margin: 0 0 24px; font-size: 16px; color: #374151; line-height: 24px;">
      After that, you'll be charged $79/month.
    </p>

    <div style="background-color: #F3F4F6; border-radius: 8px; padding: 20px; margin: 0 0 24px 0;">
      <p style="margin: 0; font-size: 15px; color: #374151; line-height: 24px;">
        Need more time to decide?
        <a href="mailto:support@revvie.app" style="color: #07B5F5; text-decoration: underline;">
          Contact us
        </a>
      </p>
    </div>

    <!-- CTA Button -->
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td style="padding: 8px 0 8px;">
          <a href="{APP_BASE_URL}/dashboard"
             style="display: inline-block; padding: 14px 32px; background-color: #07B5F5;
                    color: #ffffff; text-decoration: none; border-radius: 8px;
                    font-size: 16px; font-weight: 600;">
            Manage Billing &rarr;
          </a>
        </td>
      </tr>
    </table>
    """

    return send_email(
        to_email=email,
        subject=subject,
        html_body=render_email_template(subject, content, "Questions? Just reply to this email."),
    )


def send_payment_failed_email(email: str, business_name: str) -> dict:
    """
    Send a notification when a payment fails.

    Args:
        email: Business owner's email
        business_name: Name of the business

    Returns:
        dict: Same format as send_email()
    """
    subject = "\u26a0\ufe0f Action required: Payment failed for Revvie"

    content = f"""
    <h2 style="margin: 0 0 16px; font-size: 24px; color: #DC2626; font-weight: 600;">
      Payment Issue
    </h2>

    <p style="margin: 0 0 16px; font-size: 16px; color: #374151; line-height: 24px;">
      We couldn't process your payment for Revvie.
    </p>

    <p style="margin: 0 0 24px; font-size: 16px; color: #374151; line-height: 24px;">
      Please update your payment method to keep your account active.
    </p>

    <div style="background-color: #FEF2F2; border-left: 4px solid #DC2626; padding: 16px; margin: 0 0 24px 0;">
      <p style="margin: 0; font-size: 15px; color: #991B1B; line-height: 22px;">
        Your account will be paused if payment isn't received within 7 days.
      </p>
    </div>

    <!-- CTA Button -->
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td style="padding: 8px 0 8px;">
          <a href="{APP_BASE_URL}/dashboard"
             style="display: inline-block; padding: 14px 32px; background-color: #DC2626;
                    color: #ffffff; text-decoration: none; border-radius: 8px;
                    font-size: 16px; font-weight: 600;">
            Update Payment Method &rarr;
          </a>
        </td>
      </tr>
    </table>
    """

    return send_email(
        to_email=email,
        subject=subject,
        html_body=render_email_template(subject, content, "Questions? Just reply to this email."),
    )


def send_subscription_canceled_email(email: str, business_name: str) -> dict:
    """
    Send a notification when a subscription is canceled.

    Args:
        email: Business owner's email
        business_name: Name of the business

    Returns:
        dict: Same format as send_email()
    """
    subject = "Your Revvie subscription has been canceled"

    content = f"""
    <h2 style="margin: 0 0 16px; font-size: 24px; color: #111827; font-weight: 600;">
      We're sorry to see you go
    </h2>

    <p style="margin: 0 0 16px; font-size: 16px; color: #374151; line-height: 24px;">
      Your Revvie subscription for <strong>{business_name}</strong> has been canceled.
      You'll retain access until the end of your current billing period.
    </p>

    <p style="margin: 0 0 24px; font-size: 16px; color: #374151; line-height: 24px;">
      Changed your mind? You can reactivate anytime.
    </p>

    <!-- CTA Button -->
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td style="padding: 8px 0 8px;">
          <a href="{APP_BASE_URL}/dashboard"
             style="display: inline-block; padding: 14px 32px; background-color: #07B5F5;
                    color: #ffffff; text-decoration: none; border-radius: 8px;
                    font-size: 16px; font-weight: 600;">
            Reactivate &rarr;
          </a>
        </td>
      </tr>
    </table>
    """

    return send_email(
        to_email=email,
        subject=subject,
        html_body=render_email_template(subject, content, "We'd love your feedback &mdash; just reply to this email."),
    )


# ============================================================================
# ACCOUNT / TRANSACTIONAL EMAILS
# ============================================================================

def send_password_reset_email(email: str, business_name: str, reset_url: str) -> dict:
    """
    Send a password reset link.

    Args:
        email: Business email address
        business_name: Business name (used in greeting)
        reset_url: Reset link with token (expires 1 hour)

    Returns:
        {'success': True} or {'success': False, 'error': str}
    """
    subject = "Reset your Revvie password"

    content = f"""
    <h2 style="margin: 0 0 16px; font-size: 24px; color: #111827; font-weight: 600;">
      Reset your password
    </h2>

    <p style="margin: 0 0 16px; font-size: 16px; color: #374151; line-height: 24px;">
      Hi {business_name},
    </p>

    <p style="margin: 0 0 24px; font-size: 16px; color: #374151; line-height: 24px;">
      We received a request to reset your password for your Revvie account.
      Click the button below to create a new password:
    </p>

    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td align="center" style="padding: 8px 0 24px;">
          <a href="{reset_url}"
             style="display: inline-block; padding: 14px 32px; background-color: #07B5F5;
                    color: #ffffff; text-decoration: none; border-radius: 8px;
                    font-size: 16px; font-weight: 600;">
            Reset Password
          </a>
        </td>
      </tr>
    </table>

    <p style="margin: 0 0 16px; font-size: 14px; color: #6b7280; line-height: 20px;">
      This link expires in <strong>1 hour</strong> for security.
    </p>

    <p style="margin: 0 0 16px; font-size: 14px; color: #6b7280; line-height: 20px;">
      If you didn't request a password reset, you can safely ignore this email.
      Your password won't be changed.
    </p>

    <div style="margin-top: 24px; padding-top: 24px; border-top: 1px solid #e5e7eb;">
      <p style="margin: 0; font-size: 12px; color: #9ca3af;">
        Or copy and paste this link into your browser:<br>
        <a href="{reset_url}" style="color: #07B5F5; word-break: break-all;">{reset_url}</a>
      </p>
    </div>
    """

    html_body = render_email_template(subject, content, footer_content="")

    try:
        message = Mail(
            from_email='noreply@revvie.app',
            to_emails=email,
            subject=subject,
            html_content=html_body,
        )
        sg = SendGridAPIClient(os.getenv('SENDGRID_API_KEY'))
        sg.send(message)
        logger.info(f"Password reset email sent to {email}")
        return {'success': True}
    except Exception as e:
        logger.error(f"Password reset email failed for {email}: {e}")
        return {'success': False, 'error': str(e)}

def send_verification_email(email: str, business_name: str, verification_url: str) -> dict:
    """
    Send email verification link to new signups.

    Uses noreply@revvie.app as the sender rather than the shared
    SENDGRID_FROM_EMAIL so it's clearly a system/transactional email.

    Args:
        email: Business email address
        business_name: Business name
        verification_url: Verification link with token (expires 24h)

    Returns:
        {'success': True} or {'success': False, 'error': str}
    """
    subject = "Verify your email for Revvie"

    content = f"""
    <h2 style="margin: 0 0 16px; font-size: 24px; color: #111827; font-weight: 600;">
      Welcome to Revvie, {business_name}!
    </h2>

    <p style="margin: 0 0 16px; font-size: 16px; color: #374151; line-height: 24px;">
      Thanks for signing up. To get started, please verify your email address.
    </p>

    <p style="margin: 0 0 24px; font-size: 16px; color: #374151; line-height: 24px;">
      Click the button below to verify your email and start your free trial:
    </p>

    <!-- CTA Button -->
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td align="center" style="padding: 8px 0 24px;">
          <a href="{verification_url}"
             style="display: inline-block; padding: 14px 32px; background-color: #07B5F5;
                    color: #ffffff; text-decoration: none; border-radius: 8px;
                    font-size: 16px; font-weight: 600;">
            Verify Email Address
          </a>
        </td>
      </tr>
    </table>

    <p style="margin: 0 0 16px; font-size: 14px; color: #6b7280; line-height: 20px;">
      This link expires in 24 hours.
    </p>

    <p style="margin: 0; font-size: 14px; color: #6b7280; line-height: 20px;">
      If you didn't create this account, you can safely ignore this email.
    </p>

    <div style="margin-top: 24px; padding-top: 24px; border-top: 1px solid #e5e7eb;">
      <p style="margin: 0; font-size: 12px; color: #9ca3af;">
        Or copy and paste this link into your browser:<br>
        <a href="{verification_url}" style="color: #07B5F5; word-break: break-all;">
          {verification_url}
        </a>
      </p>
    </div>
    """

    html_body = render_email_template(subject, content, footer_content="")

    try:
        message = Mail(
            from_email='noreply@revvie.app',
            to_emails=email,
            subject=subject,
            html_content=html_body,
        )
        sg = SendGridAPIClient(os.getenv('SENDGRID_API_KEY'))
        sg.send(message)
        logger.info(f"Verification email sent to {email}")
        return {'success': True}
    except Exception as e:
        logger.error(f"Verification email failed for {email}: {e}")
        return {'success': False, 'error': str(e)}


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
    subject = f"You have ${int(credit_amount)} in Revvie credit! \U0001f389"

    content = f"""
    <h2 style="margin: 0 0 16px; font-size: 24px; color: #111827; font-weight: 600;">
      Welcome to Revvie!
    </h2>

    <p style="margin: 0 0 16px; font-size: 16px; color: #374151; line-height: 24px;">
      Great news &mdash; <strong>${int(credit_amount)} credit</strong> has been applied to your account!
    </p>

    <div style="background-color: #D1FAE5; border-left: 4px solid #6FCF97; padding: 16px; margin: 0 0 24px 0;">
      <p style="margin: 0; font-size: 18px; font-weight: 600; color: #065F46;">
        \U0001f4b0 Your first month: ${79 - int(credit_amount)} instead of $79
      </p>
    </div>

    <p style="margin: 0 0 12px; font-size: 16px; color: #374151; line-height: 24px;">
      Start collecting Google reviews automatically:
    </p>

    <ul style="margin: 0 0 24px; padding-left: 20px; color: #374151; font-size: 15px; line-height: 26px;">
      <li>Connect Square or import your customers</li>
      <li>Review requests sent after each visit</li>
      <li>Track who clicks your links</li>
    </ul>

    <!-- CTA Button -->
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td style="padding: 8px 0 8px;">
          <a href="{APP_BASE_URL}/dashboard"
             style="display: inline-block; padding: 14px 32px; background-color: #07B5F5;
                    color: #ffffff; text-decoration: none; border-radius: 8px;
                    font-size: 16px; font-weight: 600;">
            Go to Dashboard &rarr;
          </a>
        </td>
      </tr>
    </table>
    """

    return send_email(
        to_email=email,
        subject=subject,
        html_body=render_email_template(subject, content, "Questions? Just reply to this email."),
    )


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
    subject = f"You earned ${int(credit_amount)}! {referred_name} joined Revvie \U0001f389"

    content = f"""
    <h2 style="margin: 0 0 16px; font-size: 24px; color: #111827; font-weight: 600;">
      You earned a referral reward! \U0001f389
    </h2>

    <p style="margin: 0 0 16px; font-size: 16px; color: #374151; line-height: 24px;">
      <strong>{referred_name}</strong> just signed up with your referral link!
    </p>

    <div style="background-color: #D1FAE5; border-left: 4px solid #6FCF97; padding: 20px; margin: 0 0 24px 0; text-align: center;">
      <p style="margin: 0 0 8px; font-size: 16px; color: #065F46;">
        Your reward
      </p>
      <p style="margin: 0; font-size: 36px; font-weight: 700; color: #059669;">
        ${int(credit_amount)}
      </p>
    </div>

    <p style="margin: 0 0 16px; font-size: 16px; color: #374151; line-height: 24px;">
      This credit has been added to your account and will be automatically applied to your next invoice.
    </p>

    <p style="margin: 0 0 24px; font-size: 14px; color: #6B7280; line-height: 22px;">
      Keep sharing your link to earn more!
    </p>

    <!-- CTA Button -->
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td style="padding: 8px 0 8px;">
          <a href="{APP_BASE_URL}/dashboard"
             style="display: inline-block; padding: 14px 32px; background-color: #07B5F5;
                    color: #ffffff; text-decoration: none; border-radius: 8px;
                    font-size: 16px; font-weight: 600;">
            Share Your Link &rarr;
          </a>
        </td>
      </tr>
    </table>
    """

    return send_email(
        to_email=email,
        subject=subject,
        html_body=render_email_template(subject, content, "Questions? Just reply to this email."),
    )


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
    referral_word = f"referral{'s' if pending_count != 1 else ''}"
    subject = f"You have {pending_count} pending {referral_word} on Revvie"

    content = f"""
    <h2 style="margin: 0 0 16px; font-size: 24px; color: #111827; font-weight: 600;">
      Your referrals are waiting!
    </h2>

    <p style="margin: 0 0 24px; font-size: 16px; color: #374151; line-height: 24px;">
      Hey <strong>{business_name}</strong> &mdash; you have
      <strong>{pending_count} pending {referral_word}</strong> that haven't completed
      signup yet. Each one is worth <strong>$40 in credit</strong> for both of you!
    </p>

    <!-- Referral link box -->
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
           style="margin: 0 0 24px 0;">
      <tr>
        <td style="background-color: #EBF8FF; border: 1px solid #BAE6FD; border-radius: 8px;
                   padding: 16px; text-align: center;">
          <p style="margin: 0 0 6px; font-size: 12px; color: #6B7280;">Your referral link</p>
          <a href="{referral_link}"
             style="color: #07B5F5; font-size: 14px; font-weight: 600;
                    text-decoration: none; word-break: break-all;">
            {referral_link}
          </a>
        </td>
      </tr>
    </table>

    <!-- CTA Button -->
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td style="padding: 8px 0 8px;">
          <a href="{APP_BASE_URL}/dashboard"
             style="display: inline-block; padding: 14px 32px; background-color: #07B5F5;
                    color: #ffffff; text-decoration: none; border-radius: 8px;
                    font-size: 16px; font-weight: 600;">
            View Referrals &rarr;
          </a>
        </td>
      </tr>
    </table>
    """

    return send_email(
        to_email=email,
        subject=subject,
        html_body=render_email_template(subject, content, "Questions? Just reply to this email."),
    )
