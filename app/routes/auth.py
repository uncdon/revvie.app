"""
Authentication API endpoints.

Endpoints:
- POST   /api/auth/signup           - Create new account
- POST   /api/auth/login            - Login to existing account
- GET    /api/auth/me               - Get current user (requires token)
- PUT    /api/auth/change-password   - Change password (requires token)
- DELETE /api/business/account       - Delete account and all data (requires token)
"""

import os
import secrets
import logging
from datetime import datetime, timedelta, timezone
from flask import Blueprint, jsonify, request
from app.services.auth_service import signup_user, login_user, get_current_user, require_auth
from app.services.supabase_service import supabase, supabase_admin

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/auth/signup', methods=['POST'])
def signup():
    """
    Create a new user account and business.

    Request body:
    {
        "email": "owner@business.com",
        "password": "securepassword123",
        "business_name": "Joe's Coffee Shop"
    }

    Response:
    {
        "message": "Account created successfully",
        "user": {"id": "...", "email": "..."},
        "business": {"id": "...", "business_name": "..."},
        "session": {"access_token": "...", "refresh_token": "..."}
    }
    """
    try:
        # Get JSON data from request
        data = request.get_json()

        # Validate required fields
        if not data:
            return jsonify({"error": "No data provided"}), 400

        email = data.get('email')
        password = data.get('password')
        business_name = data.get('business_name')

        if not email:
            return jsonify({"error": "Email is required"}), 400
        if not password:
            return jsonify({"error": "Password is required"}), 400
        if not business_name:
            return jsonify({"error": "Business name is required"}), 400

        if len(password) < 6:
            return jsonify({"error": "Password must be at least 6 characters"}), 400

        # Create the account
        result = signup_user(email, password, business_name)

        new_business_id = result['business']['id']

        # Record referral if signup came from a referral link
        ref_code = data.get('referral_code')
        if ref_code:
            try:
                from app.services import referral_service
                referral_service.record_referral_signup(
                    referral_code=ref_code,
                    referred_business_id=new_business_id
                )
                logger.info(f"Referral recorded: {ref_code} → {new_business_id}")
            except Exception as e:
                logger.error(f"Referral recording failed for code {ref_code}: {e}")

        # Generate and store email verification token
        verification_token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=24)

        try:
            supabase_admin.table('businesses').update({
                'email_verification_token': verification_token,
                'email_verification_sent_at': now.isoformat(),
                'email_verification_expires_at': expires_at.isoformat(),
                'email_verified': False,
            }).eq('id', new_business_id).execute()
        except Exception as e:
            # Non-fatal — account is created, verification can be resent later
            logger.error(f"Failed to store verification token for {new_business_id}: {e}")

        # Send verification email (non-fatal if it fails)
        try:
            from app.services import email_service
            verification_url = (
                f"{os.getenv('APP_BASE_URL', 'http://localhost:5000')}"
                f"/verify-email?token={verification_token}"
            )
            email_service.send_verification_email(
                email=email,
                business_name=business_name,
                verification_url=verification_url,
            )
            logger.info(f"Verification email sent to {email} for business {new_business_id}")
        except Exception as e:
            logger.error(f"Failed to send verification email to {email}: {e}")

        return jsonify({
            "message": "Account created. Please check your email to verify.",
            "redirect": "/verify-email-sent",
            "email_verified": False,
            **result
        }), 201

    except Exception as e:
        error_message = str(e)

        # Handle common errors with friendly messages
        if "already registered" in error_message.lower():
            return jsonify({"error": "An account with this email already exists"}), 409

        return jsonify({"error": error_message}), 500


@auth_bp.route('/auth/login', methods=['POST'])
def login():
    """
    Login to an existing account.

    Request body:
    {
        "email": "owner@business.com",
        "password": "securepassword123"
    }

    Response:
    {
        "message": "Login successful",
        "user": {"id": "...", "email": "..."},
        "session": {"access_token": "...", "refresh_token": "...", "expires_in": 3600}
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "No data provided"}), 400

        email = data.get('email')
        password = data.get('password')

        if not email:
            return jsonify({"error": "Email is required"}), 400
        if not password:
            return jsonify({"error": "Password is required"}), 400

        # Authenticate — verifies password via Supabase
        result = login_user(email, password)

        # Post-auth checks: account status + onboarding + subscription redirect
        redirect = "/dashboard"
        try:
            user_id = result['user']['id']
            biz = supabase.table('businesses').select('*').eq('id', user_id).execute()
            if biz.data:
                b = biz.data[0]

                # Check account status before allowing access
                account_status = b.get('account_status', 'active')

                if account_status == 'blocked':
                    return jsonify({
                        "error": "Account blocked",
                        "message": "Your account has been blocked. Contact support for assistance.",
                        "reason": b.get('blocked_reason'),
                        "support_email": "support@revvie.app",
                    }), 403

                if account_status == 'deleted':
                    return jsonify({
                        "error": "Account deleted",
                        "message": "This account has been deleted.",
                    }), 410

                # Determine redirect based on onboarding + subscription state
                if not b.get('google_place_id'):
                    redirect = "/onboarding"
                else:
                    sub_status = b.get('subscription_status') or 'none'
                    if sub_status in ('none', 'canceled', 'unpaid'):
                        redirect = "/subscribe"
                    # 'trialing', 'active', 'past_due' all go to /dashboard
            else:
                redirect = "/onboarding"
        except Exception:
            pass  # Default to /dashboard if status check fails

        return jsonify({
            "message": "Login successful",
            "redirect": redirect,
            **result
        }), 200

    except Exception as e:
        error_message = str(e)

        if "invalid" in error_message.lower():
            return jsonify({"error": "Invalid email or password"}), 401

        return jsonify({"error": error_message}), 500


@auth_bp.route('/auth/me', methods=['GET'])
@require_auth  # This decorator requires a valid JWT token
def get_me():
    """
    Get the current authenticated user's info.

    Headers required:
        Authorization: Bearer <access_token>

    Response:
    {
        "user": {"id": "...", "email": "..."},
        "business": {"id": "...", "business_name": "...", ...}
    }
    """
    # request.user and request.business are set by @require_auth decorator
    return jsonify({
        "user": request.user,
        "business": request.business
    }), 200


@auth_bp.route('/auth/change-password', methods=['PUT'])
@require_auth
def change_password():
    """
    Change the current user's password.

    Request body:
    {
        "current_password": "oldpass123",
        "new_password": "newpass456"
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "No data provided"}), 400

        current_password = data.get('current_password', '')
        new_password = data.get('new_password', '')

        if not current_password:
            return jsonify({"error": "Current password is required"}), 400
        if not new_password:
            return jsonify({"error": "New password is required"}), 400
        if len(new_password) < 8:
            return jsonify({"error": "New password must be at least 8 characters"}), 400

        # Verify current password by attempting a login
        email = request.user.get('email')
        try:
            supabase.auth.sign_in_with_password({
                "email": email,
                "password": current_password
            })
        except Exception:
            return jsonify({"error": "Current password is incorrect"}), 401

        # Update to new password using the user's token
        token = request.headers.get('Authorization').split()[1]
        supabase.auth._headers = {"Authorization": f"Bearer {token}"}
        supabase.auth.update_user({"password": new_password})

        return jsonify({
            "success": True,
            "message": "Password updated successfully"
        }), 200

    except Exception as e:
        logger.error(f"Change password error: {e}")
        return jsonify({"error": "Failed to update password. Please try again."}), 500


@auth_bp.route('/auth/verify-email', methods=['GET'])
def verify_email():
    """
    Verify a user's email address using the token from the verification email.

    Query params:
        token: The verification token from the email link

    Response (success):
    {
        "success": true,
        "message": "Email verified successfully!",
        "redirect": "/onboarding",
        "business": {"id": "...", "email": "...", "business_name": "..."}
    }
    """
    token = request.args.get('token', '').strip()

    if not token:
        return jsonify({"error": "Verification token required"}), 400

    try:
        result = supabase_admin.table('businesses').select(
            'id, email, business_name, email_verified, '
            'email_verification_expires_at, google_place_id, subscription_status'
        ).eq('email_verification_token', token).execute()

        if not result.data:
            return jsonify({
                "error": "Invalid verification token",
                "redirect": "/verify-email-error?reason=invalid"
            }), 404

        biz = result.data[0]
        business_id = biz['id']

        # Already verified — idempotent success
        if biz.get('email_verified'):
            return jsonify({
                "success": True,
                "already_verified": True,
                "redirect": "/dashboard",
                "message": "Email already verified"
            }), 200

        # Check expiry
        expires_at_str = biz.get('email_verification_expires_at')
        if expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
            if datetime.now(expires_at.tzinfo) > expires_at:
                return jsonify({
                    "error": "Verification link expired",
                    "redirect": "/verify-email-error?reason=expired",
                    "can_resend": True
                }), 410

        # Mark as verified and clear the one-time token
        supabase_admin.table('businesses').update({
            'email_verified': True,
            'email_verification_token': None,
            'email_verification_sent_at': None,
            'email_verification_expires_at': None,
        }).eq('id', business_id).execute()

        logger.info(f"Email verified for business {business_id}")

        # Determine where to send the user based on onboarding state
        redirect = "/onboarding"
        if biz.get('google_place_id'):
            sub_status = biz.get('subscription_status') or 'none'
            if sub_status in ('trialing', 'active', 'past_due'):
                redirect = "/dashboard"
            else:
                redirect = "/subscribe"

        return jsonify({
            "success": True,
            "message": "Email verified successfully!",
            "redirect": redirect,
            "business": {
                "id": biz['id'],
                "email": biz.get('email'),
                "business_name": biz.get('business_name'),
            }
        }), 200

    except Exception as e:
        logger.error(f"Email verification error: {e}")
        return jsonify({"error": "Verification failed. Please try again."}), 500


@auth_bp.route('/auth/forgot-password', methods=['POST'])
def forgot_password():
    """
    Send a password reset email.

    Always returns success to avoid leaking whether an email exists.

    Request body:
        { "email": "user@example.com" }
    """
    email = (request.get_json() or {}).get('email', '').strip().lower()
    if not email:
        return jsonify({'error': 'Email required'}), 400

    _SUCCESS = {'success': True, 'message': 'If that email exists, we sent a password reset link.'}

    try:
        result = supabase_admin.table('businesses').select(
            'id, email, business_name'
        ).eq('email', email).execute()

        if not result.data:
            logger.info(f"Password reset requested for unknown email: {email}")
            return jsonify(_SUCCESS), 200

        business = result.data[0]
        reset_token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        supabase_admin.table('businesses').update({
            'password_reset_token': reset_token,
            'password_reset_expires_at': expires_at,
        }).eq('id', business['id']).execute()

        reset_url = (
            f"{os.getenv('APP_BASE_URL', 'http://localhost:5000')}"
            f"/reset-password?token={reset_token}"
        )

        from app.services import email_service
        email_service.send_password_reset_email(
            email=email,
            business_name=business.get('business_name', ''),
            reset_url=reset_url,
        )

        logger.info(f"Password reset email sent to {email}")
        return jsonify(_SUCCESS), 200

    except Exception as e:
        logger.error(f"Forgot password error for {email}: {e}")
        return jsonify(_SUCCESS), 200  # Still don't leak anything on error


@auth_bp.route('/auth/reset-password', methods=['POST'])
def reset_password():
    """
    Reset password using the token from the reset email.

    Request body:
        { "token": "...", "new_password": "newpass123" }
    """
    data = request.get_json() or {}
    token = data.get('token', '').strip()
    new_password = data.get('new_password', '')

    if not token or not new_password:
        return jsonify({'error': 'Token and new password required'}), 400
    if len(new_password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    try:
        result = supabase_admin.table('businesses').select(
            'id, email, password_reset_expires_at'
        ).eq('password_reset_token', token).execute()

        if not result.data:
            return jsonify({'error': 'Invalid or expired reset link'}), 404

        business = result.data[0]

        expires_str = business.get('password_reset_expires_at')
        if expires_str:
            expires_at = datetime.fromisoformat(expires_str.replace('Z', '+00:00'))
            if datetime.now(timezone.utc) > expires_at:
                return jsonify({'error': 'Reset link expired. Please request a new one.'}), 410

        # Update password via Supabase Auth admin API (no bcrypt needed — Supabase handles it)
        supabase_admin.auth.admin.update_user_by_id(
            business['id'],
            {'password': new_password}
        )

        # Clear the one-time token
        supabase_admin.table('businesses').update({
            'password_reset_token': None,
            'password_reset_expires_at': None,
        }).eq('id', business['id']).execute()

        logger.info(f"Password reset successful for {business['email']}")
        return jsonify({'success': True, 'message': 'Password reset successfully! You can now log in.'}), 200

    except Exception as e:
        logger.error(f"Password reset error: {e}")
        return jsonify({'error': 'Failed to reset password. Please try again.'}), 500


@auth_bp.route('/auth/resend-verification', methods=['POST'])
def resend_verification():
    """
    Resend the email verification link.

    Request body:
    {
        "email": "user@example.com"
    }

    No authentication required — public endpoint.
    Always returns success to avoid leaking whether an email exists.
    """
    try:
        email = (request.json or {}).get('email', '').strip().lower()

        if not email:
            return jsonify({"error": "Email required"}), 400

        # Look up business by email (use admin client to bypass RLS on public route)
        result = supabase_admin.table('businesses').select(
            'id, business_name, email_verified, email_verification_sent_at'
        ).eq('email', email).execute()

        if not result.data:
            # Don't reveal whether the email exists
            return jsonify({
                "success": True,
                "message": "If that email exists, we sent a verification link."
            }), 200

        business = result.data[0]

        # Already verified — no point resending
        if business.get('email_verified'):
            return jsonify({
                "error": "Email already verified",
                "redirect": "/login"
            }), 400

        # Rate limit: max one resend per 5 minutes
        last_sent = business.get('email_verification_sent_at')
        if last_sent:
            last_sent_dt = datetime.fromisoformat(last_sent.replace('Z', '+00:00'))
            if datetime.now(last_sent_dt.tzinfo) - last_sent_dt < timedelta(minutes=5):
                return jsonify({
                    "error": "Please wait 5 minutes before requesting another email"
                }), 429

        # Generate a new token
        new_token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=24)

        supabase_admin.table('businesses').update({
            'email_verification_token': new_token,
            'email_verification_sent_at': now.isoformat(),
            'email_verification_expires_at': expires_at.isoformat(),
        }).eq('id', business['id']).execute()

        verification_url = (
            f"{os.getenv('APP_BASE_URL', 'http://localhost:5000')}"
            f"/verify-email?token={new_token}"
        )

        from app.services import email_service
        email_service.send_verification_email(
            email=email,
            business_name=business.get('business_name', ''),
            verification_url=verification_url,
        )

        logger.info(f"Verification email resent to {email}")
        return jsonify({
            "success": True,
            "message": "Verification email sent. Check your inbox."
        }), 200

    except Exception as e:
        logger.error(f"Resend verification error: {e}")
        return jsonify({"error": "Failed to resend. Please try again."}), 500


@auth_bp.route('/business/account', methods=['DELETE'])
@require_auth
def delete_account():
    """
    Delete the user's account and all associated data.

    Request body:
    {
        "confirmation": "DELETE"
    }
    """
    try:
        data = request.get_json()

        if not data or data.get('confirmation') != 'DELETE':
            return jsonify({"error": "Please type DELETE to confirm"}), 400

        user_id = request.user['id']

        # Cancel Stripe subscription if active (can't CASCADE to external service)
        try:
            biz = supabase.table("businesses").select("stripe_customer_id").eq("id", user_id).execute()
            if biz.data and biz.data[0].get('stripe_customer_id'):
                import stripe
                import os
                stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
                subs = stripe.Subscription.list(customer=biz.data[0]['stripe_customer_id'], limit=1)
                for sub in subs.data:
                    stripe.Subscription.cancel(sub.id)
                logger.info(f"Canceled Stripe subscription for user {user_id}")
        except Exception as e:
            logger.warning(f"Could not cancel Stripe subscription: {e}")

        # Delete the business — CASCADE handles all related records automatically
        supabase_admin.table("businesses").delete().eq("id", user_id).execute()

        # Delete the auth user
        try:
            supabase_admin.auth.admin.delete_user(user_id)
        except Exception as e:
            logger.warning(f"Could not delete auth user: {e}")

        logger.info(f"Account deleted for user {user_id}")

        return jsonify({
            "success": True,
            "message": "Account deleted successfully"
        }), 200

    except Exception as e:
        logger.error(f"Delete account error: {e}")
        return jsonify({"error": "Failed to delete account. Please contact support."}), 500
