"""
Referral Service - handles the *revvie refer-and-earn program.

HOW IT WORKS:
=============
1. Every business gets a unique referral code (e.g., REV4K7XP)
2. They share their link: https://app.revvie.app/signup?ref=REV4K7XP
3. New business signs up via that link -> both get $40 credit
4. Credit is applied after the referred business makes first payment

REWARD AMOUNTS:
===============
- Referrer:  $40 account credit (applied when referred business pays)
- Referred:  $40 discount on first month (applied at signup)

TABLES USED:
============
- businesses:          referral_code, account_credit, discount_applied
- referrals:           tracks each referral relationship and status
- credit_transactions: audit log of all credit changes
"""

import os
import logging
import secrets
import string
from datetime import datetime, timezone

from app.services.supabase_service import supabase_admin

logger = logging.getLogger(__name__)

APP_BASE_URL = os.getenv('APP_BASE_URL', 'http://localhost:5000')

# Reward amounts in dollars
REFERRER_CREDIT = 40.00
REFERRED_CREDIT = 40.00

# Code generation settings
CODE_LENGTH = 8
CODE_PREFIX = 'REV'
# Exclude confusing characters: 0, O, 1, I
CODE_ALPHABET = ''.join(
    c for c in string.ascii_uppercase + string.digits
    if c not in '0O1I'
)
MAX_CODE_ATTEMPTS = 10


# =============================================================================
# FUNCTION 1: GENERATE REFERRAL CODE
# =============================================================================

def generate_referral_code() -> str:
    """
    Generate a unique 8-character uppercase referral code.

    Format: 3-letter prefix + 5 random chars (e.g., REV4K7XP).
    Uses A-Z, 0-9 excluding confusing characters (0, O, 1, I).
    Checks the businesses table for uniqueness, retries up to 10 times.

    Returns:
        Unique 8-character string like "REV4K7XP"

    Raises:
        RuntimeError: If a unique code can't be generated after 10 attempts
    """
    suffix_length = CODE_LENGTH - len(CODE_PREFIX)

    for attempt in range(1, MAX_CODE_ATTEMPTS + 1):
        suffix = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(suffix_length))
        code = f"{CODE_PREFIX}{suffix}"

        # Check uniqueness
        result = supabase_admin.table('businesses') \
            .select('id') \
            .eq('referral_code', code) \
            .limit(1) \
            .execute()

        if not result.data:
            logger.debug(f"Generated unique referral code on attempt {attempt}: {code}")
            return code

        logger.warning(f"Referral code collision on attempt {attempt}: {code}")

    raise RuntimeError(
        f"Failed to generate unique referral code after {MAX_CODE_ATTEMPTS} attempts"
    )


# =============================================================================
# FUNCTION 2: GET OR CREATE REFERRAL CODE
# =============================================================================

def get_or_create_referral_code(business_id: str) -> str | None:
    """
    Get the existing referral code for a business, or create a new one.

    Args:
        business_id: UUID of the business

    Returns:
        The referral code string (e.g., "REV4K7XP"), or None on error
    """
    try:
        result = supabase_admin.table('businesses') \
            .select('referral_code') \
            .eq('id', business_id) \
            .limit(1) \
            .execute()

        if not result.data:
            logger.error(f"Business {business_id} not found")
            return None

        existing_code = result.data[0].get('referral_code')
        if existing_code:
            return existing_code

        # Generate and save a new code
        new_code = generate_referral_code()

        supabase_admin.table('businesses') \
            .update({'referral_code': new_code}) \
            .eq('id', business_id) \
            .execute()

        logger.info(f"Created referral code {new_code} for business {business_id}")
        return new_code

    except Exception as e:
        logger.error(f"Failed to get/create referral code for business {business_id}: {e}")
        return None


# =============================================================================
# FUNCTION 3: GET REFERRAL LINK
# =============================================================================

def get_referral_link(business_id: str) -> dict | None:
    """
    Build the full referral URL for a business.

    Args:
        business_id: UUID of the business

    Returns:
        {
            'referral_code': 'REV4K7XP',
            'referral_link': 'https://app.revvie.app/signup?ref=REV4K7XP'
        }
        or None on error
    """
    try:
        code = get_or_create_referral_code(business_id)
        if not code:
            return None

        return {
            'referral_code': code,
            'referral_link': f'{APP_BASE_URL}/signup?ref={code}',
        }

    except Exception as e:
        logger.error(f"Failed to build referral link for business {business_id}: {e}")
        return None


# =============================================================================
# FUNCTION 4: RECORD REFERRAL SIGNUP
# =============================================================================

def record_referral_signup(referral_code: str, referred_business_id: str) -> dict | None:
    """
    Record a new referral when a business signs up via a referral link.

    Looks up the referrer by code, validates the referral, inserts the
    referral record, applies the $40 discount to the referred business,
    and logs the credit transaction.

    Args:
        referral_code: The referral code from the signup URL (e.g., "REV4K7XP")
        referred_business_id: UUID of the newly signed-up business

    Returns:
        The referral record dict, or None if invalid/error
    """
    try:
        # Safety: block if business has already consumed a referral credit.
        # This prevents re-subscribing with a new referral code to get infinite $40 credits.
        credit_check = supabase_admin.table('businesses') \
            .select('referral_credit_used') \
            .eq('id', referred_business_id) \
            .limit(1) \
            .execute()

        if credit_check.data and credit_check.data[0].get('referral_credit_used'):
            logger.warning(
                f"Business {referred_business_id} tried to use referral code '{referral_code}' "
                f"but referral_credit_used=true — blocked"
            )
            return None

        # Look up the referrer
        referrer_result = supabase_admin.table('businesses') \
            .select('id') \
            .eq('referral_code', referral_code) \
            .limit(1) \
            .execute()

        if not referrer_result.data:
            logger.warning(f"Referral code not found: {referral_code}")
            return None

        referrer_id = referrer_result.data[0]['id']

        # Safety: can't refer yourself
        if referrer_id == referred_business_id:
            logger.warning(f"Self-referral attempted: {referrer_id}")
            return None

        # Safety: check for duplicate referral
        dup_result = supabase_admin.table('referrals') \
            .select('id') \
            .eq('referred_business_id', referred_business_id) \
            .limit(1) \
            .execute()

        if dup_result.data:
            logger.warning(f"Duplicate referral for business {referred_business_id}")
            return None

        # Insert referral record
        referral_result = supabase_admin.table('referrals').insert({
            'referrer_business_id': referrer_id,
            'referred_business_id': referred_business_id,
            'referral_code': referral_code,
            'status': 'pending',
            'referrer_credit': REFERRER_CREDIT,
            'referred_credit': REFERRED_CREDIT,
        }).execute()

        if not referral_result.data:
            logger.error(f"Referral insert returned no data for {referred_business_id}")
            return None

        referral = referral_result.data[0]
        referral_id = referral['id']

        logger.info(
            f"Recorded referral {referral_id}: "
            f"{referrer_id} referred {referred_business_id} via {referral_code}"
        )

        # Apply $40 discount to referred business
        try:
            biz_credit = supabase_admin.table('businesses') \
                .select('account_credit') \
                .eq('id', referred_business_id) \
                .limit(1) \
                .execute()
            current = float((biz_credit.data[0].get('account_credit') or 0)) if biz_credit.data else 0.0

            supabase_admin.table('businesses') \
                .update({
                    'discount_applied': REFERRED_CREDIT,
                    'account_credit': current + REFERRED_CREDIT,
                }) \
                .eq('id', referred_business_id) \
                .execute()
        except Exception as e:
            logger.error(f"Failed to apply discount for {referred_business_id}: {e}")

        # Log credit transaction
        try:
            supabase_admin.table('credit_transactions').insert({
                'business_id': referred_business_id,
                'amount': REFERRED_CREDIT,
                'type': 'referral_discount',
                'description': '$40 referral credit applied to first month',
                'referral_id': referral_id,
            }).execute()
        except Exception as e:
            logger.error(f"Failed to log credit transaction for {referred_business_id}: {e}")

        # Send welcome email to referred business
        try:
            _send_referral_welcome(referred_business_id, referral_code)
        except Exception as e:
            logger.error(f"Failed to send referral welcome email for {referred_business_id}: {e}")

        return referral

    except Exception as e:
        logger.error(f"Failed to record referral signup for code {referral_code}: {e}")
        return None


# =============================================================================
# FUNCTION 5: COMPLETE REFERRAL
# =============================================================================

def complete_referral(referral_id: str) -> dict | None:
    """
    Complete a referral after the referred business makes their first payment.

    Marks the referral as completed, adds $40 credit to the referrer's
    account, logs the transaction, and sends a reward notification email.

    Args:
        referral_id: UUID of the referral record

    Returns:
        The updated referral record, or None if not pending/error
    """
    try:
        # Get referral and verify it's pending
        ref_result = supabase_admin.table('referrals') \
            .select('*') \
            .eq('id', referral_id) \
            .limit(1) \
            .execute()

        if not ref_result.data:
            logger.warning(f"Referral {referral_id} not found")
            return None

        referral = ref_result.data[0]

        if referral['status'] != 'pending':
            logger.warning(f"Referral {referral_id} is not pending (status: {referral['status']})")
            return None

        referrer_id = referral['referrer_business_id']
        referred_id = referral['referred_business_id']

        # Get referred business name for the credit description
        referred_name = 'a new business'
        try:
            biz_result = supabase_admin.table('businesses') \
                .select('business_name') \
                .eq('id', referred_id) \
                .limit(1) \
                .execute()
            if biz_result.data and biz_result.data[0].get('business_name'):
                referred_name = biz_result.data[0]['business_name']
        except Exception:
            pass

        # Mark referral as completed
        supabase_admin.table('referrals') \
            .update({
                'status': 'completed',
                'completed_at': datetime.now(timezone.utc).isoformat(),
            }) \
            .eq('id', referral_id) \
            .execute()

        logger.info(f"Referral {referral_id} completed: rewarding referrer {referrer_id}")

        # Add $40 credit to referrer
        # First get current credit to add to it
        try:
            biz_result = supabase_admin.table('businesses') \
                .select('account_credit') \
                .eq('id', referrer_id) \
                .limit(1) \
                .execute()

            current_credit = 0.0
            if biz_result.data:
                current_credit = float(biz_result.data[0].get('account_credit') or 0)

            supabase_admin.table('businesses') \
                .update({'account_credit': current_credit + REFERRER_CREDIT}) \
                .eq('id', referrer_id) \
                .execute()
        except Exception as e:
            logger.error(f"Failed to add credit to referrer {referrer_id}: {e}")

        # Log credit transaction
        try:
            supabase_admin.table('credit_transactions').insert({
                'business_id': referrer_id,
                'amount': REFERRER_CREDIT,
                'type': 'referral_credit',
                'description': f'$40 referral credit earned - {referred_name} joined *revvie',
                'referral_id': referral_id,
            }).execute()
        except Exception as e:
            logger.error(f"Failed to log credit transaction for referrer {referrer_id}: {e}")

        # Send reward email to referrer
        try:
            _send_referral_reward(referrer_id, referred_name)
        except Exception as e:
            logger.error(f"Failed to send referral reward email for {referrer_id}: {e}")

        # Return the updated referral
        updated = supabase_admin.table('referrals') \
            .select('*') \
            .eq('id', referral_id) \
            .limit(1) \
            .execute()

        return updated.data[0] if updated.data else referral

    except Exception as e:
        logger.error(f"Failed to complete referral {referral_id}: {e}")
        return None


# =============================================================================
# FUNCTION 6: COMPLETE REFERRAL BY BUSINESS
# =============================================================================

def complete_referral_by_business(referred_business_id: str) -> dict | None:
    """
    Complete a referral by looking up the referred business ID.

    Called by the Stripe webhook after the referred business makes
    their first payment.

    Args:
        referred_business_id: UUID of the business that was referred

    Returns:
        The completed referral record, or None if no pending referral found
    """
    try:
        result = supabase_admin.table('referrals') \
            .select('id') \
            .eq('referred_business_id', referred_business_id) \
            .eq('status', 'pending') \
            .limit(1) \
            .execute()

        if not result.data:
            logger.debug(f"No pending referral found for business {referred_business_id}")
            return None

        referral_id = result.data[0]['id']
        return complete_referral(referral_id)

    except Exception as e:
        logger.error(f"Failed to complete referral by business {referred_business_id}: {e}")
        return None


# =============================================================================
# FUNCTION 7: CANCEL REFERRAL
# =============================================================================

def cancel_referral(referral_id: str) -> dict | None:
    """
    Cancel a referral if the referred business churns before paying.

    Marks the referral as cancelled, removes the discount from the
    referred business, and deletes the associated credit transaction.

    Args:
        referral_id: UUID of the referral record

    Returns:
        The updated referral record, or None on error
    """
    try:
        # Get referral
        ref_result = supabase_admin.table('referrals') \
            .select('*') \
            .eq('id', referral_id) \
            .limit(1) \
            .execute()

        if not ref_result.data:
            logger.warning(f"Referral {referral_id} not found for cancellation")
            return None

        referral = ref_result.data[0]

        if referral['status'] != 'pending':
            logger.warning(f"Referral {referral_id} is not pending (status: {referral['status']}), cannot cancel")
            return None

        referred_id = referral['referred_business_id']

        # Mark as cancelled
        supabase_admin.table('referrals') \
            .update({'status': 'cancelled'}) \
            .eq('id', referral_id) \
            .execute()

        logger.info(f"Referral {referral_id} cancelled")

        # Remove credit from referred business
        try:
            biz_result = supabase_admin.table('businesses') \
                .select('account_credit') \
                .eq('id', referred_id) \
                .limit(1) \
                .execute()

            current_credit = 0.0
            if biz_result.data:
                current_credit = float(biz_result.data[0].get('account_credit') or 0)

            new_credit = max(0.0, current_credit - REFERRED_CREDIT)

            supabase_admin.table('businesses') \
                .update({
                    'account_credit': new_credit,
                    'discount_applied': 0,
                }) \
                .eq('id', referred_id) \
                .execute()
        except Exception as e:
            logger.error(f"Failed to remove credit from {referred_id}: {e}")

        # Delete the credit transaction for this referral
        try:
            supabase_admin.table('credit_transactions') \
                .delete() \
                .eq('referral_id', referral_id) \
                .eq('business_id', referred_id) \
                .execute()
        except Exception as e:
            logger.error(f"Failed to delete credit transaction for referral {referral_id}: {e}")

        # Return updated referral
        updated = supabase_admin.table('referrals') \
            .select('*') \
            .eq('id', referral_id) \
            .limit(1) \
            .execute()

        return updated.data[0] if updated.data else referral

    except Exception as e:
        logger.error(f"Failed to cancel referral {referral_id}: {e}")
        return None


# =============================================================================
# FUNCTION 8: GET REFERRAL STATS
# =============================================================================

def get_referral_stats(business_id: str) -> dict | None:
    """
    Get full referral stats for a business's dashboard.

    Returns the referral code/link, account credit balance, counts
    by status, pending credit amount, and a list of all referrals
    with referred business names.

    Args:
        business_id: UUID of the business

    Returns:
        {
            'referral_code': 'REV4K7XP',
            'referral_link': 'https://app.revvie.app/signup?ref=REV4K7XP',
            'account_credit': 80.00,
            'total_referrals': 3,
            'pending_referrals': 1,
            'completed_referrals': 2,
            'cancelled_referrals': 0,
            'pending_credit': 40.00,
            'referrals': [
                {
                    'id': 'uuid',
                    'referred_business_name': 'Bella Hair Salon',
                    'status': 'completed',
                    'created_at': 'ISO timestamp',
                    'completed_at': 'ISO timestamp',
                    'referrer_credit': 40.00
                }
            ]
        }
        or None on error
    """
    try:
        # Get referral code and link
        link_info = get_referral_link(business_id)
        if not link_info:
            return None

        # Get account credit balance
        biz_result = supabase_admin.table('businesses') \
            .select('account_credit') \
            .eq('id', business_id) \
            .limit(1) \
            .execute()

        account_credit = 0.0
        if biz_result.data:
            account_credit = float(biz_result.data[0].get('account_credit') or 0)

        # Check if this business was itself referred by someone
        referred_check = supabase_admin.table('referrals') \
            .select('status') \
            .eq('referred_business_id', business_id) \
            .limit(1) \
            .execute()
        was_referred = bool(referred_check.data)

        # Get all referrals where this business is the referrer
        # Join with businesses to get referred business name
        ref_result = supabase_admin.table('referrals') \
            .select('id, referred_business_id, status, created_at, completed_at, referrer_credit') \
            .eq('referrer_business_id', business_id) \
            .order('created_at', desc=True) \
            .execute()

        referrals_raw = ref_result.data or []

        # Look up referred business names
        referred_ids = [r['referred_business_id'] for r in referrals_raw]
        names_map = {}
        if referred_ids:
            try:
                names_result = supabase_admin.table('businesses') \
                    .select('id, business_name') \
                    .in_('id', referred_ids) \
                    .execute()
                for biz in (names_result.data or []):
                    names_map[biz['id']] = biz.get('business_name', 'Unknown Business')
            except Exception as e:
                logger.error(f"Failed to look up referred business names: {e}")

        # Build referral list and counts
        referrals = []
        pending_count = 0
        completed_count = 0
        cancelled_count = 0

        for r in referrals_raw:
            status = r['status']
            if status == 'pending':
                pending_count += 1
            elif status == 'completed':
                completed_count += 1
            elif status == 'cancelled':
                cancelled_count += 1

            referrals.append({
                'id': r['id'],
                'referred_business_name': names_map.get(r['referred_business_id'], 'Unknown Business'),
                'status': status,
                'created_at': r.get('created_at'),
                'completed_at': r.get('completed_at'),
                'referrer_credit': float(r.get('referrer_credit') or 0),
            })

        return {
            'referral_code': link_info['referral_code'],
            'referral_link': link_info['referral_link'],
            'account_credit': account_credit,
            'total_referrals': len(referrals_raw),
            'pending_referrals': pending_count,
            'completed_referrals': completed_count,
            'cancelled_referrals': cancelled_count,
            'pending_credit': pending_count * REFERRER_CREDIT,
            'was_referred': was_referred,
            'referrals': referrals,
        }

    except Exception as e:
        logger.error(f"Failed to get referral stats for business {business_id}: {e}")
        return None


# =============================================================================
# PRIVATE HELPERS
# =============================================================================

def _get_business_info(business_id: str) -> dict | None:
    """Get business email and name for sending emails."""
    try:
        result = supabase_admin.table('businesses') \
            .select('email, business_name') \
            .eq('id', business_id) \
            .limit(1) \
            .execute()
        if result.data:
            return result.data[0]
    except Exception as e:
        logger.error(f"Failed to get business info for {business_id}: {e}")
    return None


def _send_referral_welcome(referred_business_id: str, referral_code: str) -> None:
    """Send welcome email to the referred business about their $40 credit."""
    try:
        from app.services.email_service import send_referral_welcome_email
        biz = _get_business_info(referred_business_id)
        if not biz or not biz.get('email'):
            return

        send_referral_welcome_email(
            email=biz['email'],
            business_name=biz.get('business_name', 'your business'),
            credit_amount=REFERRED_CREDIT,
        )
        logger.info(f"Sent referral welcome email to {referred_business_id}")
    except ImportError:
        logger.warning("send_referral_welcome_email not yet implemented in email_service")
    except Exception as e:
        logger.error(f"Failed to send referral welcome email for {referred_business_id}: {e}")


def _send_referral_reward(referrer_business_id: str, referred_name: str) -> None:
    """Send reward notification email to the referrer."""
    try:
        from app.services.email_service import send_referral_reward_email
        biz = _get_business_info(referrer_business_id)
        if not biz or not biz.get('email'):
            return

        send_referral_reward_email(
            email=biz['email'],
            business_name=biz.get('business_name', 'your business'),
            referred_name=referred_name,
            credit_amount=REFERRER_CREDIT,
        )
        logger.info(f"Sent referral reward email to {referrer_business_id}")
    except ImportError:
        logger.warning("send_referral_reward_email not yet implemented in email_service")
    except Exception as e:
        logger.error(f"Failed to send referral reward email for {referrer_business_id}: {e}")
