"""
Authentication API endpoints.

Endpoints:
- POST   /api/auth/signup           - Create new account
- POST   /api/auth/login            - Login to existing account
- GET    /api/auth/me               - Get current user (requires token)
- PUT    /api/auth/change-password   - Change password (requires token)
- DELETE /api/business/account       - Delete account and all data (requires token)
"""

import logging
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

        return jsonify({
            "message": "Account created successfully",
            "redirect": "/onboarding",
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

        # Authenticate
        result = login_user(email, password)

        # Check onboarding + subscription status to determine redirect
        redirect = "/dashboard"
        try:
            user_id = result['user']['id']
            biz = supabase.table('businesses').select('*').eq('id', user_id).execute()
            if biz.data:
                b = biz.data[0]
                if not b.get('google_place_id'):
                    redirect = "/onboarding"
                else:
                    # Onboarding complete — check subscription status
                    sub_status = b.get('subscription_status') or 'none'
                    if sub_status in ('none', 'canceled', 'unpaid'):
                        redirect = "/subscribe"
                    # 'trialing', 'active', 'past_due' all go to /dashboard
            else:
                redirect = "/onboarding"
        except Exception:
            pass  # Default to dashboard if check fails

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

        # Cancel Stripe subscription if active
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

        # Delete data in order (respecting foreign keys)
        supabase_admin.table("tracking_clicks").delete().in_(
            "tracking_link_id",
            [r['id'] for r in supabase_admin.table("tracking_links").select("id").eq("business_id", user_id).execute().data or []]
        ).execute() if supabase_admin.table("tracking_links").select("id").eq("business_id", user_id).execute().data else None

        supabase_admin.table("tracking_links").delete().eq("business_id", user_id).execute()
        supabase_admin.table("queued_review_requests").delete().eq("business_id", user_id).execute()
        supabase_admin.table("review_requests").delete().eq("business_id", user_id).execute()
        supabase_admin.table("customers").delete().eq("business_id", user_id).execute()
        supabase_admin.table("integrations").delete().eq("business_id", user_id).execute()
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
