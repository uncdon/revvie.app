"""
Square Integration Service - handles OAuth and API calls to Square.

Square is a payment processing platform. When a business connects their Square
account to Revvie, we can automatically detect new payments and send review
requests to their customers.

HOW SQUARE OAUTH WORKS (simplified):
1. User clicks "Connect to Square" button
2. We redirect them to Square's authorization page (get_authorization_url)
3. User logs into Square and approves our app
4. Square redirects back to our app with an "authorization code"
5. We exchange that code for access tokens (exchange_code_for_token)
6. We store those tokens (encrypted!) in our database
7. We use the access token to make API calls on behalf of the user
8. When the token expires, we refresh it (refresh_access_token)

SECURITY NOTE:
- Access tokens are like passwords - NEVER log them or expose them!
- We encrypt tokens before storing in database using Fernet encryption
- The encryption key (TOKEN_ENCRYPTION_KEY) must be kept secret
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from dotenv import load_dotenv

# Square SDK - the official library for Square API
# Note: SDK v44+ uses 'Square' class instead of 'Client'
from square import Square
from square.environment import SquareEnvironment

# Cryptography library for encrypting tokens before storing in database
# Fernet is a symmetric encryption method (same key encrypts and decrypts)
from cryptography.fernet import Fernet, InvalidToken

from app.services.square_logger import get_square_logger, log_api_event

# Load environment variables from .env file
load_dotenv()

# Get logger for API operations
logger = get_square_logger('api')


# ============================================================================
# CONFIGURATION - Load from environment variables
# ============================================================================

# Square app credentials (from Square Developer Dashboard)
# Use sandbox or production credentials based on environment
SQUARE_ENVIRONMENT = os.environ.get("SQUARE_ENVIRONMENT", "sandbox")

if SQUARE_ENVIRONMENT == "sandbox":
    SQUARE_APP_ID = os.environ.get("SQUARE_SANDBOX_APP_ID")
    SQUARE_APP_SECRET = os.environ.get("SQUARE_SANDBOX_APP_SECRET")
else:
    SQUARE_APP_ID = os.environ.get("SQUARE_PRODUCTION_APP_ID")
    SQUARE_APP_SECRET = os.environ.get("SQUARE_PRODUCTION_APP_SECRET")

# For testing - Square provides a sandbox environment
SQUARE_SANDBOX_ACCESS_TOKEN = os.environ.get("SQUARE_SANDBOX_ACCESS_TOKEN")

# Where Square redirects after user authorizes (must match Square dashboard)
SQUARE_REDIRECT_URI = os.environ.get("SQUARE_REDIRECT_URI")

# Secret key for encrypting tokens before storing in database
# Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TOKEN_ENCRYPTION_KEY = os.environ.get("TOKEN_ENCRYPTION_KEY")

# Square OAuth URLs - must match the environment
if SQUARE_ENVIRONMENT == "sandbox":
    SQUARE_OAUTH_URL = "https://connect.squareupsandbox.com/oauth2/authorize"
    SQUARE_TOKEN_URL = "https://connect.squareupsandbox.com/oauth2/token"
else:
    SQUARE_OAUTH_URL = "https://connect.squareup.com/oauth2/authorize"
    SQUARE_TOKEN_URL = "https://connect.squareup.com/oauth2/token"

# The permissions our app needs from Square
# See: https://developer.squareup.com/docs/oauth-api/square-permissions
SQUARE_SCOPES = [
    "MERCHANT_PROFILE_READ",  # Read business name, address, etc.
    "PAYMENTS_READ",          # Read payment transactions
    "CUSTOMERS_READ",         # Read customer info (email, phone, name)
]


# ============================================================================
# TOKEN ENCRYPTION FUNCTIONS
# ============================================================================
# We encrypt access tokens before storing them in the database.
# This adds a layer of security - even if someone gets database access,
# they can't use the tokens without the encryption key.

def get_fernet() -> Optional[Fernet]:
    """
    Create a Fernet encryption object using our secret key.

    Returns:
        Fernet object for encryption/decryption, or None if key not configured

    Example:
        fernet = get_fernet()
        encrypted = fernet.encrypt(b"my secret data")
    """
    if not TOKEN_ENCRYPTION_KEY:
        logger.warning("TOKEN_ENCRYPTION_KEY not set! Tokens will not be encrypted.")
        return None

    try:
        # Fernet expects bytes, so encode the string key
        return Fernet(TOKEN_ENCRYPTION_KEY.encode())
    except Exception as e:
        logger.error(f"Invalid TOKEN_ENCRYPTION_KEY format: {e}")
        return None


def encrypt_token(token_string: str) -> str:
    """
    Encrypt a token before storing in database.

    Args:
        token_string: The plain text token (e.g., Square access token)

    Returns:
        Encrypted token as a string (safe to store in database)
        If encryption fails, returns the original token (not recommended!)

    Example:
        encrypted = encrypt_token("sq0atp-xxxxx")
        # Store 'encrypted' in database, not the original token!
    """
    fernet = get_fernet()

    if not fernet:
        # No encryption key configured - return as-is (not secure!)
        logger.warning("Storing token without encryption!")
        return token_string

    try:
        # encrypt() returns bytes, decode to string for database storage
        encrypted_bytes = fernet.encrypt(token_string.encode())
        return encrypted_bytes.decode()
    except Exception as e:
        logger.error(f"Failed to encrypt token: {e}")
        return token_string


def decrypt_token(encrypted_token: str) -> str:
    """
    Decrypt a token retrieved from database.

    Args:
        encrypted_token: The encrypted token string from database

    Returns:
        The original plain text token
        If decryption fails, returns the input (might already be plain text)

    Example:
        plain_token = decrypt_token(encrypted_from_db)
        # Use plain_token to make API calls
    """
    fernet = get_fernet()

    if not fernet:
        # No encryption key - assume token is already plain text
        return encrypted_token

    try:
        # decrypt() returns bytes, decode to string
        decrypted_bytes = fernet.decrypt(encrypted_token.encode())
        return decrypted_bytes.decode()
    except InvalidToken:
        # Token might not be encrypted (legacy data or test data)
        logger.warning("Token decryption failed - might not be encrypted")
        return encrypted_token
    except Exception as e:
        logger.error(f"Failed to decrypt token: {e}")
        return encrypted_token


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def check_token_expiry(expires_at: datetime) -> bool:
    """
    Check if a token is expired or will expire soon (within 24 hours).

    We check 24 hours in advance because:
    - Gives us buffer time to refresh before it actually expires
    - Avoids failed API calls due to expired tokens
    - Background jobs might not run exactly on time

    Args:
        expires_at: The datetime when the token expires

    Returns:
        True if token is expired or expires within 24 hours
        False if token is still valid for more than 24 hours

    Example:
        if check_token_expiry(integration.token_expires_at):
            # Token needs refresh!
            new_tokens = refresh_access_token(integration.refresh_token)
    """
    if expires_at is None:
        return True  # No expiry set, assume expired

    # Make sure we're comparing timezone-aware datetimes
    now = datetime.now(timezone.utc)

    # If expires_at doesn't have timezone info, assume UTC
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    # Check if expired or will expire in the next 24 hours
    buffer_time = timedelta(hours=24)
    return now >= (expires_at - buffer_time)


def get_square_client(access_token: str) -> Square:
    """
    Create a Square API client with the given access token.

    The Square Client is the main object for making API calls.
    It handles authentication, request formatting, and error handling.

    Args:
        access_token: A valid Square access token

    Returns:
        Configured Square Client ready to make API calls

    Example:
        client = get_square_client(decrypted_token)
        result = client.merchants.list()
    """
    # Set environment based on config
    env = SquareEnvironment.SANDBOX if SQUARE_ENVIRONMENT == "sandbox" else SquareEnvironment.PRODUCTION

    return Square(
        token=access_token,
        environment=env
    )


# ============================================================================
# AUTOMATIC TOKEN REFRESH
# ============================================================================
# These functions handle automatic token refresh to ensure API calls never fail
# due to expired tokens.

def ensure_valid_token(integration_id: str) -> dict:
    """
    Ensure we have a valid access token for the given integration.

    This function should be called BEFORE any Square API call. It:
    1. Retrieves the integration from the database
    2. Checks if the token expires within the next 24 hours
    3. If yes, refreshes the token and updates the database
    4. Returns the valid (possibly refreshed) access token

    Args:
        integration_id: The ID of the integration record in the database

    Returns:
        Dictionary with token information:
        {
            "success": True/False,
            "access_token": "decrypted_token",  # Ready to use for API calls
            "refreshed": True/False,             # Whether token was refreshed
            "error": "error message"             # Only if success=False
        }

    Example:
        token_result = ensure_valid_token(integration['id'])
        if token_result['success']:
            merchant = get_merchant_info(token_result['access_token'])
        else:
            # Handle error - maybe mark integration as failed
            pass
    """
    from app.services.supabase_service import supabase

    log_api_event('ensure_valid_token', details={'integration_id': integration_id})

    try:
        # Get the integration from database
        result = supabase.table('integrations').select('*').eq(
            'id', integration_id
        ).execute()

        if not result.data:
            log_api_event('ensure_valid_token', success=False,
                        error="Integration not found",
                        details={'integration_id': integration_id})
            return {
                "success": False,
                "error": "Integration not found"
            }

        integration = result.data[0]

        # Parse token expiration
        token_expires_at = integration.get('token_expires_at')
        if token_expires_at:
            if isinstance(token_expires_at, str):
                token_expires_at = datetime.fromisoformat(
                    token_expires_at.replace('Z', '+00:00')
                )

        # Check if token needs refresh (expires within 24 hours)
        needs_refresh = check_token_expiry(token_expires_at)

        if needs_refresh:
            log_api_event('ensure_valid_token', details={
                'integration_id': integration_id,
                'action': 'refreshing',
                'expires_at': str(token_expires_at)
            })

            # Decrypt refresh token
            refresh_token = decrypt_token(integration['refresh_token'])

            # Refresh the token
            refresh_result = refresh_access_token(refresh_token)

            if not refresh_result['success']:
                # Mark integration as having an error
                supabase.table('integrations').update({
                    'status': 'error'
                }).eq('id', integration_id).execute()

                log_api_event('ensure_valid_token', success=False,
                            error=f"Token refresh failed: {refresh_result.get('error')}",
                            details={'integration_id': integration_id})
                return {
                    "success": False,
                    "error": f"Token refresh failed: {refresh_result.get('error')}"
                }

            # Update database with new tokens
            supabase.table('integrations').update({
                'access_token': encrypt_token(refresh_result['access_token']),
                'refresh_token': encrypt_token(refresh_result['refresh_token']),
                'token_expires_at': refresh_result['expires_at'].isoformat(),
                'status': 'active'  # Reset status if it was in error
            }).eq('id', integration_id).execute()

            log_api_event('ensure_valid_token', details={
                'integration_id': integration_id,
                'action': 'refreshed',
                'new_expires_at': refresh_result['expires_at'].isoformat()
            })

            return {
                "success": True,
                "access_token": refresh_result['access_token'],
                "refreshed": True
            }

        else:
            # Token is still valid, just decrypt and return
            access_token = decrypt_token(integration['access_token'])

            log_api_event('ensure_valid_token', details={
                'integration_id': integration_id,
                'action': 'valid',
                'expires_at': str(token_expires_at)
            })

            return {
                "success": True,
                "access_token": access_token,
                "refreshed": False
            }

    except Exception as e:
        log_api_event('ensure_valid_token', success=False,
                    error=str(e), error_type='exception',
                    details={'integration_id': integration_id})
        return {
            "success": False,
            "error": f"Error ensuring valid token: {str(e)}"
        }


def refresh_all_tokens() -> dict:
    """
    Refresh all Square integration tokens that are expiring soon.

    This function is designed to be called by a cron job (e.g., weekly)
    to proactively refresh tokens before they expire.

    Returns:
        Dictionary with refresh results:
        {
            "total": 10,       # Total integrations checked
            "refreshed": 3,    # Successfully refreshed
            "skipped": 6,      # Still valid, no refresh needed
            "failed": 1,       # Failed to refresh
            "errors": [...]    # List of error details
        }

    Example:
        # Run weekly via cron:
        # 0 0 * * 0 cd /path/to/revvie && python -c "from app.services.square_service import refresh_all_tokens; print(refresh_all_tokens())"
    """
    from app.services.supabase_service import supabase

    log_api_event('refresh_all_tokens', details={'action': 'started'})

    results = {
        "total": 0,
        "refreshed": 0,
        "skipped": 0,
        "failed": 0,
        "errors": []
    }

    try:
        # Get all active Square integrations
        integrations_result = supabase.table('integrations').select('*').eq(
            'integration_type', 'square'
        ).eq('status', 'active').execute()

        integrations = integrations_result.data or []
        results["total"] = len(integrations)

        logger.info(f"Checking {len(integrations)} Square integrations for token refresh")

        for integration in integrations:
            integration_id = integration['id']
            business_id = integration.get('business_id')

            try:
                token_result = ensure_valid_token(integration_id)

                if token_result['success']:
                    if token_result.get('refreshed'):
                        results["refreshed"] += 1
                        logger.info(f"Refreshed token for integration {integration_id} (business: {business_id})")
                    else:
                        results["skipped"] += 1
                else:
                    results["failed"] += 1
                    results["errors"].append({
                        "integration_id": integration_id,
                        "business_id": business_id,
                        "error": token_result.get('error')
                    })

            except Exception as e:
                results["failed"] += 1
                results["errors"].append({
                    "integration_id": integration_id,
                    "business_id": business_id,
                    "error": str(e)
                })

        log_api_event('refresh_all_tokens', details={
            'total': results['total'],
            'refreshed': results['refreshed'],
            'skipped': results['skipped'],
            'failed': results['failed']
        })

        return results

    except Exception as e:
        log_api_event('refresh_all_tokens', success=False, error=str(e))
        results["errors"].append({"error": f"Fatal error: {str(e)}"})
        return results


# ============================================================================
# OAUTH FLOW FUNCTIONS
# ============================================================================
# These functions handle the OAuth "dance" to connect a user's Square account

def get_authorization_url(state: Optional[str] = None) -> str:
    """
    Generate the URL to redirect users to Square's authorization page.

    This is STEP 1 of OAuth:
    - User clicks "Connect to Square" on your site
    - You redirect them to this URL
    - They log into Square and approve your app
    - Square redirects them back to your SQUARE_REDIRECT_URI

    Args:
        state: Optional random string to prevent CSRF attacks.
               You should generate a unique state, store it in session,
               and verify it matches when Square redirects back.

    Returns:
        Full URL to redirect the user to

    Example:
        url = get_authorization_url(state="random123")
        return redirect(url)  # In Flask
    """
    from urllib.parse import urlencode

    # Build the authorization URL with required parameters
    params = {
        "client_id": SQUARE_APP_ID,
        "scope": " ".join(SQUARE_SCOPES),  # Space-separated list of permissions
        "session": "false",  # Don't use Square's session management
        "redirect_uri": SQUARE_REDIRECT_URI,
    }

    # Add state parameter if provided (recommended for security!)
    if state:
        params["state"] = state

    # Build properly URL-encoded query string
    query_string = urlencode(params)

    return f"{SQUARE_OAUTH_URL}?{query_string}"


def exchange_code_for_token(authorization_code: str) -> dict:
    """
    Exchange an authorization code for access and refresh tokens.

    This is STEP 2 of OAuth:
    - Square redirects user back with ?code=xxxxx in the URL
    - You call this function with that code
    - Square gives you the actual access tokens

    Args:
        authorization_code: The 'code' parameter from Square's redirect

    Returns:
        Dictionary with token information:
        {
            "success": True/False,
            "access_token": "sq0atp-xxxxx",      # Use this for API calls
            "refresh_token": "sq0rft-xxxxx",     # Use this to get new access token
            "expires_at": datetime,               # When access token expires
            "merchant_id": "xxxxx",              # Square merchant ID
            "error": "error message"             # Only if success=False
        }

    Example:
        # In your callback route:
        code = request.args.get("code")
        result = exchange_code_for_token(code)
        if result["success"]:
            # Save encrypted tokens to database
            save_integration(
                access_token=encrypt_token(result["access_token"]),
                refresh_token=encrypt_token(result["refresh_token"]),
                ...
            )
    """
    import requests

    try:
        # Make direct HTTP request to Square's token endpoint
        # This gives us full control over which URL is called
        log_api_event('token_exchange', details={'action': 'started'})

        response = requests.post(
            SQUARE_TOKEN_URL,
            json={
                "client_id": SQUARE_APP_ID,
                "client_secret": SQUARE_APP_SECRET,
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": SQUARE_REDIRECT_URI,
            },
            headers={
                "Content-Type": "application/json",
                "Square-Version": "2024-01-18"
            }
        )

        logger.debug(f"Token exchange response status: {response.status_code}")

        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            error_msg = error_data.get('message', response.text)
            log_api_event('token_exchange', success=False, error=error_msg, error_type='api_error')
            return {
                "success": False,
                "error": error_msg,
            }

        data = response.json()

        # Calculate when the token expires
        expires_at_str = data.get('expires_at')
        if expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
        else:
            expires_at = datetime.now(timezone.utc) + timedelta(days=30)

        log_api_event('token_exchange', details={
            'merchant_id': data.get('merchant_id'),
            'expires_at': expires_at.isoformat()
        })

        return {
            "success": True,
            "access_token": data.get('access_token'),
            "refresh_token": data.get('refresh_token'),
            "expires_at": expires_at,
            "merchant_id": data.get('merchant_id'),
        }

    except Exception as e:
        error_message = str(e)
        log_api_event('token_exchange', success=False, error=error_message, error_type='network')
        return {
            "success": False,
            "error": f"Connection error: {error_message}",
        }


def refresh_access_token(refresh_token: str) -> dict:
    """
    Get a new access token using a refresh token.

    Access tokens expire (typically after 30 days). When they do,
    use the refresh token to get a new access token without requiring
    the user to re-authorize.

    Args:
        refresh_token: The refresh token (decrypt it first if stored encrypted!)

    Returns:
        Dictionary with new token information:
        {
            "success": True/False,
            "access_token": "sq0atp-xxxxx",      # New access token
            "refresh_token": "sq0rft-xxxxx",     # New refresh token (save this!)
            "expires_at": datetime,               # When new access token expires
            "error": "error message"             # Only if success=False
        }

    Example:
        # Check if token needs refresh
        if check_token_expiry(integration.token_expires_at):
            old_refresh = decrypt_token(integration.refresh_token)
            result = refresh_access_token(old_refresh)
            if result["success"]:
                # Update database with new tokens
                update_integration(
                    access_token=encrypt_token(result["access_token"]),
                    refresh_token=encrypt_token(result["refresh_token"]),
                    token_expires_at=result["expires_at"]
                )
    """
    env = SquareEnvironment.SANDBOX if SQUARE_ENVIRONMENT == "sandbox" else SquareEnvironment.PRODUCTION
    client = Square(environment=env)

    log_api_event('token_refresh', details={'action': 'started'})

    try:
        result = client.o_auth.obtain_token(
            client_id=SQUARE_APP_ID,
            client_secret=SQUARE_APP_SECRET,
            grant_type="refresh_token",
            refresh_token=refresh_token,
        )

        # Calculate expiration
        expires_in = result.expires_at
        if expires_in:
            expires_at = datetime.fromisoformat(expires_in.replace("Z", "+00:00"))
        else:
            expires_at = datetime.now(timezone.utc) + timedelta(days=30)

        log_api_event('token_refresh', details={'expires_at': expires_at.isoformat()})

        return {
            "success": True,
            "access_token": result.access_token,
            "refresh_token": result.refresh_token,
            "expires_at": expires_at,
        }

    except Exception as e:
        error_message = str(e)
        log_api_event('token_refresh', success=False, error=error_message, error_type='api_error')
        return {
            "success": False,
            "error": error_message,
        }


# ============================================================================
# SQUARE API CALLS
# ============================================================================
# These functions call Square's API to get data about merchants, payments, etc.

def get_merchant_info(access_token: str) -> dict:
    """
    Get information about the connected Square merchant (business).

    This retrieves:
    - Business name
    - Business ID
    - Location(s)
    - Country, currency, etc.

    Args:
        access_token: Valid (decrypted) Square access token

    Returns:
        Dictionary with merchant information:
        {
            "success": True/False,
            "merchant_id": "xxxxx",
            "business_name": "Joe's Coffee Shop",
            "country": "US",
            "currency": "USD",
            "locations": [{"id": "xxx", "name": "Main Store"}, ...],
            "error": "error message"  # Only if success=False
        }

    Example:
        token = decrypt_token(integration.access_token)
        info = get_merchant_info(token)
        if info["success"]:
            print(f"Connected to: {info['business_name']}")
    """
    log_api_event('get_merchant', details={'action': 'started'})

    try:
        client = get_square_client(access_token)

        # Get merchant (business) info
        # The SDK returns a pager, so we need to iterate with .items
        merchant_result = client.merchants.list()
        merchants = list(merchant_result.items)

        if not merchants:
            log_api_event('get_merchant', success=False, error="No merchant found")
            return {"success": False, "error": "No merchant found"}

        merchant = merchants[0]

        # Get locations (a merchant can have multiple stores)
        locations_result = client.locations.list()
        locations = []

        if locations_result.locations:
            for loc in locations_result.locations:
                address = loc.address.address_line1 if loc.address else None
                locations.append({
                    "id": loc.id,
                    "name": loc.name,
                    "address": address,
                    "status": loc.status,
                })

        log_api_event('get_merchant', details={
            'merchant_id': merchant.id,
            'business_name': merchant.business_name,
            'location_count': len(locations)
        })

        return {
            "success": True,
            "merchant_id": merchant.id,
            "business_name": merchant.business_name,
            "country": merchant.country,
            "currency": merchant.currency,
            "locations": locations,
        }

    except Exception as e:
        log_api_event('get_merchant', success=False, error=str(e), error_type='network')
        return {
            "success": False,
            "error": f"Connection error: {str(e)}",
        }


def get_payment_details(access_token: str, payment_id: str) -> dict:
    """
    Get details about a specific payment transaction.

    Use this to get:
    - Payment amount
    - Customer ID (to look up their contact info)
    - Payment status
    - When the payment was made

    Args:
        access_token: Valid (decrypted) Square access token
        payment_id: The Square payment ID to look up

    Returns:
        Dictionary with payment information:
        {
            "success": True/False,
            "payment_id": "xxxxx",
            "amount": 1500,              # In cents! $15.00 = 1500
            "currency": "USD",
            "status": "COMPLETED",
            "customer_id": "xxxxx",      # Use this with get_customer_details
            "created_at": datetime,
            "location_id": "xxxxx",
            "error": "error message"     # Only if success=False
        }

    Example:
        payment = get_payment_details(token, "payment_abc123")
        if payment["success"] and payment["customer_id"]:
            customer = get_customer_details(token, payment["customer_id"])
    """
    log_api_event('get_payment', details={'payment_id': payment_id})

    try:
        client = get_square_client(access_token)

        result = client.payments.get(payment_id=payment_id)

        payment = result.payment
        if not payment:
            log_api_event('get_payment', success=False, error="Payment not found",
                        details={'payment_id': payment_id})
            return {"success": False, "error": "Payment not found"}

        amount_money = payment.amount_money

        # Parse the created_at timestamp
        created_at = None
        if payment.created_at:
            created_at = datetime.fromisoformat(
                payment.created_at.replace("Z", "+00:00")
            )

        log_api_event('get_payment', details={
            'payment_id': payment.id,
            'status': payment.status,
            'has_customer': bool(payment.customer_id)
        })

        return {
            "success": True,
            "payment_id": payment.id,
            "amount": amount_money.amount if amount_money else None,  # In cents!
            "currency": amount_money.currency if amount_money else None,
            "status": payment.status,
            "customer_id": payment.customer_id,
            "created_at": created_at,
            "location_id": payment.location_id,
        }

    except Exception as e:
        log_api_event('get_payment', success=False, error=str(e), error_type='network',
                    details={'payment_id': payment_id})
        return {
            "success": False,
            "error": f"Connection error: {str(e)}",
        }


def get_customer_details(access_token: str, customer_id: str) -> dict:
    """
    Get contact information for a customer.

    Use this to get the customer's:
    - Name
    - Email address
    - Phone number

    These are what we need to send review requests!

    Args:
        access_token: Valid (decrypted) Square access token
        customer_id: The Square customer ID (from a payment)

    Returns:
        Dictionary with customer information:
        {
            "success": True/False,
            "customer_id": "xxxxx",
            "name": "John Smith",           # Given name + family name
            "given_name": "John",
            "family_name": "Smith",
            "email": "john@example.com",    # None if not on file
            "phone": "+15551234567",         # None if not on file
            "error": "error message"        # Only if success=False
        }

    Example:
        customer = get_customer_details(token, payment["customer_id"])
        if customer["success"]:
            if customer["email"]:
                send_email_review_request(customer["email"], customer["name"])
            elif customer["phone"]:
                send_sms_review_request(customer["phone"], customer["name"])
    """
    log_api_event('get_customer', details={'customer_id': customer_id})

    try:
        client = get_square_client(access_token)

        result = client.customers.get(customer_id=customer_id)

        customer = result.customer
        if not customer:
            log_api_event('get_customer', success=False, error="Customer not found",
                        details={'customer_id': customer_id})
            return {"success": False, "error": "Customer not found"}

        # Build full name from parts
        given_name = customer.given_name or ""
        family_name = customer.family_name or ""
        full_name = f"{given_name} {family_name}".strip()

        # If no name parts, try the company name or nickname
        if not full_name:
            full_name = customer.company_name or customer.nickname or "Customer"

        log_api_event('get_customer', details={
            'customer_id': customer.id,
            'has_email': bool(customer.email_address),
            'has_phone': bool(customer.phone_number)
        })

        return {
            "success": True,
            "customer_id": customer.id,
            "name": full_name,
            "given_name": given_name,
            "family_name": family_name,
            "email": customer.email_address,
            "phone": customer.phone_number,
        }

    except Exception as e:
        log_api_event('get_customer', success=False, error=str(e), error_type='network',
                    details={'customer_id': customer_id})
        return {
            "success": False,
            "error": f"Connection error: {str(e)}",
        }


# ============================================================================
# USAGE EXAMPLES
# ============================================================================
#
# --- OAUTH FLOW ---
#
# Step 1: Redirect user to Square
#   @app.route("/connect-square")
#   def connect_square():
#       state = generate_random_string()  # Save in session
#       session["oauth_state"] = state
#       url = get_authorization_url(state=state)
#       return redirect(url)
#
# Step 2: Handle callback from Square
#   @app.route("/square/callback")
#   def square_callback():
#       code = request.args.get("code")
#       state = request.args.get("state")
#
#       # Verify state matches (CSRF protection)
#       if state != session.get("oauth_state"):
#           return "Invalid state", 400
#
#       # Exchange code for tokens
#       result = exchange_code_for_token(code)
#       if result["success"]:
#           # Save to database (encrypted!)
#           save_integration(
#               business_id=current_user.business_id,
#               access_token=encrypt_token(result["access_token"]),
#               refresh_token=encrypt_token(result["refresh_token"]),
#               token_expires_at=result["expires_at"],
#               merchant_id=result["merchant_id"],
#               status="active"
#           )
#           return redirect("/dashboard?connected=true")
#       else:
#           return f"Error: {result['error']}", 400
#
# --- MAKING API CALLS ---
#
#   # Get integration from database
#   integration = get_integration(business_id)
#
#   # Check if token needs refresh
#   if check_token_expiry(integration.token_expires_at):
#       old_refresh = decrypt_token(integration.refresh_token)
#       new_tokens = refresh_access_token(old_refresh)
#       if new_tokens["success"]:
#           update_integration(integration.id, new_tokens)
#           access_token = new_tokens["access_token"]
#       else:
#           # Handle error - maybe mark integration as "error" status
#           pass
#   else:
#       access_token = decrypt_token(integration.access_token)
#
#   # Now make API calls
#   merchant = get_merchant_info(access_token)
#   print(f"Connected to: {merchant['business_name']}")
#
# --- GENERATING ENCRYPTION KEY ---
#
#   Run this once to generate your TOKEN_ENCRYPTION_KEY:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#
#   Then add it to your .env file:
#   TOKEN_ENCRYPTION_KEY=your-generated-key-here
