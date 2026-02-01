"""
Square Integration API endpoints.

These routes handle connecting/disconnecting Square accounts and managing settings.

Endpoints:
- GET  /api/integrations/square/connect     - Get Square OAuth URL
- GET  /integrations/square/callback        - OAuth callback (Square redirects here)
- GET  /api/integrations/square/status      - Check if Square is connected
- PUT  /api/integrations/square/settings    - Update integration settings
- POST /api/integrations/square/disconnect  - Disconnect Square account

HOW THE SQUARE CONNECTION FLOW WORKS:
=====================================
1. User clicks "Connect Square" button on frontend
2. Frontend calls GET /api/integrations/square/connect
3. Backend returns Square's authorization URL
4. Frontend redirects user to Square's site
5. User logs into Square and approves the connection
6. Square redirects to GET /integrations/square/callback with auth code
7. Backend exchanges code for tokens, saves to database
8. Backend redirects user to dashboard with success message
"""

import os
import secrets
from flask import Blueprint, jsonify, request, redirect, session
from app.services.auth_service import require_auth
from app.services.supabase_service import supabase
from app.services import square_service

# Create Blueprint
# NOTE: This blueprint is registered WITHOUT url_prefix so we can have both
# /api/... routes and the /integrations/square/callback route
square_bp = Blueprint('square_integration', __name__)

# Get the frontend URL for redirects (defaults to localhost for development)
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5001")


# ============================================================================
# ROUTE 1: Get Square Authorization URL
# ============================================================================

@square_bp.route('/connect', methods=['GET'])
@require_auth
def get_connect_url():
    """
    Generate the URL to redirect users to Square for OAuth authorization.
    In sandbox mode, uses the sandbox access token directly (OAuth doesn't work in sandbox).
    """
    try:
        if not request.business:
            return jsonify({"error": "Business not found. Please complete your account setup."}), 404

        business_id = request.business.get('id')
        if not business_id:
            return jsonify({"error": "Business ID not found"}), 404

        # Check if we're in sandbox mode with a sandbox token available
        square_env = os.environ.get("SQUARE_ENVIRONMENT", "sandbox")
        sandbox_token = os.environ.get("SQUARE_SANDBOX_ACCESS_TOKEN")

        if square_env == "sandbox" and sandbox_token:
            # In sandbox mode, use the sandbox token directly (OAuth doesn't work in sandbox)
            print("INFO: Sandbox mode - using sandbox access token directly")

            # Get merchant info using sandbox token
            merchant_info = square_service.get_merchant_info(sandbox_token)

            merchant_name = None
            location_id = None
            location_name = None

            if merchant_info.get('success'):
                merchant_name = merchant_info.get('business_name')
                locations = merchant_info.get('locations', [])
                for loc in locations:
                    if loc.get('status') == 'ACTIVE':
                        location_id = loc.get('id')
                        location_name = loc.get('name')
                        break

            # Encrypt token before storing
            encrypted_token = square_service.encrypt_token(sandbox_token)

            # Check if integration already exists
            existing = supabase.table('integrations').select('id').eq(
                'business_id', business_id
            ).eq('integration_type', 'square').execute()

            from datetime import datetime, timedelta, timezone
            integration_data = {
                'business_id': business_id,
                'integration_type': 'square',
                'access_token': encrypted_token,
                'refresh_token': encrypted_token,  # Sandbox tokens don't refresh
                'token_expires_at': (datetime.now(timezone.utc) + timedelta(days=365)).isoformat(),
                'square_merchant_id': merchant_info.get('merchant_id') if merchant_info.get('success') else 'sandbox',
                'square_location_id': location_id,
                'settings': {
                    'delay_hours': 2,
                    'auto_send_enabled': True,
                    'merchant_name': merchant_name or 'Sandbox Business',
                    'location_name': location_name or 'Sandbox Location'
                },
                'status': 'active'
            }

            if existing.data:
                supabase.table('integrations').update(integration_data).eq(
                    'id', existing.data[0]['id']
                ).execute()
            else:
                supabase.table('integrations').insert(integration_data).execute()

            # Return a special response indicating sandbox mode connected directly
            return jsonify({
                "sandbox_connected": True,
                "message": "Sandbox mode - connected using test credentials"
            }), 200

        # Production mode - use normal OAuth flow
        state = secrets.token_urlsafe(32)
        session['square_oauth_state'] = state
        session['square_oauth_business_id'] = business_id

        authorization_url = square_service.get_authorization_url(state=state)
        print(f"DEBUG: Returning Square auth URL: {authorization_url}")

        return jsonify({
            "authorization_url": authorization_url
        }), 200

    except Exception as e:
        import traceback
        print(f"ERROR: Failed to generate Square auth URL: {str(e)}")
        print(f"TRACEBACK: {traceback.format_exc()}")
        return jsonify({"error": "Failed to start Square connection", "details": str(e)}), 500


# ============================================================================
# ROUTE 2: OAuth Callback (Square redirects here)
# ============================================================================

@square_bp.route('/callback', methods=['GET'])
def oauth_callback():
    """
    Handle the OAuth callback from Square.

    After the user approves the connection on Square's site, Square redirects
    them back to this URL with an authorization code. We exchange that code
    for access tokens and save them to the database.

    Query parameters (sent by Square):
        code: The authorization code to exchange for tokens
        state: The state parameter we sent (for CSRF verification)
        error: Error code if user denied or something went wrong
        error_description: Human-readable error message

    On success: Redirects to /dashboard?square_connected=true
    On error: Redirects to /dashboard?square_error=<message>
    """
    # Check for errors from Square
    error = request.args.get('error')
    if error:
        error_description = request.args.get('error_description', 'Unknown error')
        print(f"ERROR: Square OAuth error: {error} - {error_description}")
        return redirect(f"{FRONTEND_URL}/dashboard?square_error={error_description}")

    # Get the authorization code
    code = request.args.get('code')
    state = request.args.get('state')

    if not code:
        print("ERROR: No authorization code received from Square")
        return redirect(f"{FRONTEND_URL}/dashboard?square_error=No authorization code received")

    # Verify state parameter (CSRF protection)
    # Note: In production with multiple servers, store state in Redis/database
    stored_state = session.get('square_oauth_state')
    business_id = session.get('square_oauth_business_id')

    if not stored_state or state != stored_state:
        print(f"ERROR: State mismatch. Expected: {stored_state}, Got: {state}")
        # In development, we might not have sessions working perfectly
        # If no business_id in session, we can't proceed
        if not business_id:
            return redirect(f"{FRONTEND_URL}/dashboard?square_error=Session expired. Please try again.")

    # Clear the session data (one-time use)
    session.pop('square_oauth_state', None)
    session.pop('square_oauth_business_id', None)

    try:
        # Exchange the authorization code for tokens
        token_result = square_service.exchange_code_for_token(code)

        if not token_result.get('success'):
            error_msg = token_result.get('error', 'Failed to exchange code')
            print(f"ERROR: Token exchange failed: {error_msg}")
            return redirect(f"{FRONTEND_URL}/dashboard?square_error={error_msg}")

        # Get merchant info to display in the UI
        merchant_info = square_service.get_merchant_info(token_result['access_token'])

        merchant_name = None
        location_id = None
        location_name = None

        if merchant_info.get('success'):
            merchant_name = merchant_info.get('business_name')
            # Use the first active location by default
            locations = merchant_info.get('locations', [])
            for loc in locations:
                if loc.get('status') == 'ACTIVE':
                    location_id = loc.get('id')
                    location_name = loc.get('name')
                    break

        # Encrypt tokens before storing in database
        encrypted_access_token = square_service.encrypt_token(token_result['access_token'])
        encrypted_refresh_token = square_service.encrypt_token(token_result['refresh_token'])

        # Check if integration already exists for this business
        existing = supabase.table('integrations').select('id').eq(
            'business_id', business_id
        ).eq('integration_type', 'square').execute()

        integration_data = {
            'business_id': business_id,
            'integration_type': 'square',
            'access_token': encrypted_access_token,
            'refresh_token': encrypted_refresh_token,
            'token_expires_at': token_result['expires_at'].isoformat(),
            'square_merchant_id': token_result.get('merchant_id'),
            'square_location_id': location_id,
            'settings': {
                'delay_hours': 2,
                'auto_send_enabled': True,
                'merchant_name': merchant_name,
                'location_name': location_name
            },
            'status': 'active'
        }

        if existing.data:
            # Update existing integration
            result = supabase.table('integrations').update(integration_data).eq(
                'id', existing.data[0]['id']
            ).execute()
            print(f"INFO: Updated Square integration for business {business_id}")
        else:
            # Create new integration
            result = supabase.table('integrations').insert(integration_data).execute()
            print(f"INFO: Created Square integration for business {business_id}")

        if not result.data:
            print("ERROR: Failed to save integration to database")
            return redirect(f"{FRONTEND_URL}/dashboard?square_error=Failed to save connection")

        # Success! Redirect to dashboard
        return redirect(f"{FRONTEND_URL}/dashboard?square_connected=true")

    except Exception as e:
        print(f"ERROR: Square callback exception: {str(e)}")
        return redirect(f"{FRONTEND_URL}/dashboard?square_error=Connection failed")


# ============================================================================
# ROUTE 3: Get Integration Status
# ============================================================================

@square_bp.route('/status', methods=['GET'])
@require_auth
def get_status():
    """
    Check if Square is connected and get integration details.

    Headers required:
        Authorization: Bearer <access_token>

    Returns:
        {
            "connected": true,
            "merchant_name": "Bella Salon",
            "location_name": "Main Street",
            "merchant_id": "XXXXX",
            "settings": {
                "delay_hours": 2,
                "auto_send_enabled": true
            },
            "status": "active"
        }

    Or if not connected:
        {
            "connected": false
        }
    """
    try:
        if not request.business:
            return jsonify({"connected": False}), 200

        business_id = request.business.get('id')

        if not business_id:
            return jsonify({"connected": False}), 200

        # Query the integrations table
        result = supabase.table('integrations').select('*').eq(
            'business_id', business_id
        ).eq('integration_type', 'square').execute()

        if not result.data:
            return jsonify({"connected": False}), 200

        integration = result.data[0]
        settings = integration.get('settings', {})

        return jsonify({
            "connected": True,
            "merchant_name": settings.get('merchant_name'),
            "location_name": settings.get('location_name'),
            "merchant_id": integration.get('square_merchant_id'),
            "location_id": integration.get('square_location_id'),
            "settings": {
                "delay_hours": settings.get('delay_hours', 2),
                "auto_send_enabled": settings.get('auto_send_enabled', True)
            },
            "status": integration.get('status')
        }), 200

    except Exception as e:
        print(f"ERROR: Failed to get Square status: {str(e)}")
        return jsonify({"error": "Failed to get integration status"}), 500


# ============================================================================
# ROUTE 4: Update Settings
# ============================================================================

@square_bp.route('/settings', methods=['PUT'])
@require_auth
def update_settings():
    """
    Update Square integration settings.

    Headers required:
        Authorization: Bearer <access_token>

    Request body:
        {
            "delay_hours": 4,
            "auto_send_enabled": false
        }

    Returns:
        {
            "success": true,
            "settings": {
                "delay_hours": 4,
                "auto_send_enabled": false,
                "merchant_name": "Bella Salon",
                "location_name": "Main Street"
            }
        }
    """
    try:
        if not request.business:
            return jsonify({"error": "Business not found"}), 404

        business_id = request.business.get('id')

        if not business_id:
            return jsonify({"error": "Business not found"}), 404

        # Get request data
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        # Get existing integration
        result = supabase.table('integrations').select('*').eq(
            'business_id', business_id
        ).eq('integration_type', 'square').execute()

        if not result.data:
            return jsonify({"error": "Square not connected"}), 404

        integration = result.data[0]
        current_settings = integration.get('settings', {})

        # Update only the fields that were provided
        if 'delay_hours' in data:
            # Validate delay_hours is a reasonable number
            delay = data['delay_hours']
            if not isinstance(delay, (int, float)) or delay < 0 or delay > 168:  # Max 1 week
                return jsonify({"error": "delay_hours must be between 0 and 168"}), 400
            current_settings['delay_hours'] = delay

        if 'auto_send_enabled' in data:
            current_settings['auto_send_enabled'] = bool(data['auto_send_enabled'])

        # Save updated settings
        update_result = supabase.table('integrations').update({
            'settings': current_settings
        }).eq('id', integration['id']).execute()

        if not update_result.data:
            return jsonify({"error": "Failed to update settings"}), 500

        return jsonify({
            "success": True,
            "settings": current_settings
        }), 200

    except Exception as e:
        print(f"ERROR: Failed to update Square settings: {str(e)}")
        return jsonify({"error": "Failed to update settings"}), 500


# ============================================================================
# ROUTE 5: Disconnect Square
# ============================================================================

@square_bp.route('/disconnect', methods=['POST'])
@require_auth
def disconnect():
    """
    Disconnect the Square integration.

    This deletes the integration record from the database, including
    the encrypted tokens. The user will need to re-authorize if they
    want to connect again.

    Headers required:
        Authorization: Bearer <access_token>

    Returns:
        {
            "success": true,
            "message": "Square disconnected successfully"
        }
    """
    try:
        if not request.business:
            return jsonify({"error": "Business not found"}), 404

        business_id = request.business.get('id')

        if not business_id:
            return jsonify({"error": "Business not found"}), 404

        # Find the integration
        result = supabase.table('integrations').select('id').eq(
            'business_id', business_id
        ).eq('integration_type', 'square').execute()

        if not result.data:
            return jsonify({"error": "Square not connected"}), 404

        integration_id = result.data[0]['id']

        # Delete the integration
        # Note: We fully delete rather than just setting status='inactive'
        # because we want to remove the tokens completely
        delete_result = supabase.table('integrations').delete().eq(
            'id', integration_id
        ).execute()

        print(f"INFO: Disconnected Square for business {business_id}")

        return jsonify({
            "success": True,
            "message": "Square disconnected successfully"
        }), 200

    except Exception as e:
        print(f"ERROR: Failed to disconnect Square: {str(e)}")
        return jsonify({"error": "Failed to disconnect Square"}), 500


# ============================================================================
# USAGE EXAMPLES (for frontend developers)
# ============================================================================
#
# --- CONNECTING SQUARE ---
#
#   // 1. Get the authorization URL
#   const response = await fetch('/api/integrations/square/connect', {
#       headers: {
#           'Authorization': `Bearer ${accessToken}`
#       }
#   });
#   const data = await response.json();
#
#   // 2. Redirect user to Square
#   window.location.href = data.authorization_url;
#
#   // 3. User approves on Square's site
#   // 4. Square redirects back to /integrations/square/callback
#   // 5. Backend handles everything and redirects to /dashboard
#
# --- CHECKING CONNECTION STATUS ---
#
#   const response = await fetch('/api/integrations/square/status', {
#       headers: {
#           'Authorization': `Bearer ${accessToken}`
#       }
#   });
#   const data = await response.json();
#
#   if (data.connected) {
#       console.log(`Connected to: ${data.merchant_name}`);
#       console.log(`Auto-send: ${data.settings.auto_send_enabled}`);
#       console.log(`Delay: ${data.settings.delay_hours} hours`);
#   }
#
# --- UPDATING SETTINGS ---
#
#   const response = await fetch('/api/integrations/square/settings', {
#       method: 'PUT',
#       headers: {
#           'Authorization': `Bearer ${accessToken}`,
#           'Content-Type': 'application/json'
#       },
#       body: JSON.stringify({
#           delay_hours: 4,
#           auto_send_enabled: true
#       })
#   });
#
# --- DISCONNECTING ---
#
#   const response = await fetch('/api/integrations/square/disconnect', {
#       method: 'POST',
#       headers: {
#           'Authorization': `Bearer ${accessToken}`
#       }
#   });
