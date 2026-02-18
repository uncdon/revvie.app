"""
Usage Tracking Service - monitors SMS/email volume against monthly caps.

Each business has monthly limits:
- SMS: 750/month (default)
- Email: 1,000/month (default)

Counters reset automatically on the 1st of each month.
The usage_month column tracks which month the current counts are for.
When we detect a new month, counters reset to 0.

Usage:
    from app.services import usage_tracker

    # Before sending SMS
    check = usage_tracker.can_send_sms(business_id)
    if not check['can_send']:
        return error("SMS limit reached")

    # After sending
    usage_tracker.increment_sms_count(business_id)

    # Dashboard display
    stats = usage_tracker.get_usage_stats(business_id)
"""

import logging
from datetime import datetime, timezone
from app.services.supabase_service import supabase_admin as supabase

logger = logging.getLogger(__name__)

# Default caps
DEFAULT_SMS_CAP = 750
DEFAULT_EMAIL_CAP = 1000


def check_and_reset_if_new_month(business_id: str) -> dict:
    """
    Check if we've rolled into a new month and reset counters if so.

    Args:
        business_id: The business to check

    Returns:
        dict with current usage after any reset:
        {
            'sms_sent_this_month': int,
            'email_sent_this_month': int,
            'usage_month': str,
            'was_reset': bool
        }
    """
    try:
        result = supabase.table('businesses').select(
            'usage_month, sms_sent_this_month, email_sent_this_month'
        ).eq('id', business_id).limit(1).execute()

        if not result.data:
            logger.warning(f"Business not found for usage check: {business_id}")
            return {
                'sms_sent_this_month': 0,
                'email_sent_this_month': 0,
                'usage_month': None,
                'was_reset': False
            }

        biz = result.data[0]
        usage_month = biz.get('usage_month')
        current_month = datetime.now(timezone.utc).strftime('%Y-%m-01')

        # Check if we need to reset
        needs_reset = False
        if not usage_month:
            needs_reset = True
        elif usage_month < current_month:
            needs_reset = True

        if needs_reset:
            update_result = supabase.table('businesses').update({
                'sms_sent_this_month': 0,
                'email_sent_this_month': 0,
                'usage_month': current_month
            }).eq('id', business_id).execute()

            logger.info(f"Usage counters reset for new month: business={business_id}, month={current_month}")

            return {
                'sms_sent_this_month': 0,
                'email_sent_this_month': 0,
                'usage_month': current_month,
                'was_reset': True
            }

        return {
            'sms_sent_this_month': biz.get('sms_sent_this_month') or 0,
            'email_sent_this_month': biz.get('email_sent_this_month') or 0,
            'usage_month': usage_month,
            'was_reset': False
        }

    except Exception as e:
        logger.error(f"Error checking/resetting usage month for {business_id}: {e}")
        return {
            'sms_sent_this_month': 0,
            'email_sent_this_month': 0,
            'usage_month': None,
            'was_reset': False
        }


def _get_next_month_first() -> str:
    """Get the first day of next month as a date string."""
    now = datetime.now(timezone.utc)
    if now.month == 12:
        return f"{now.year + 1}-01-01"
    return f"{now.year}-{now.month + 1:02d}-01"


def can_send_sms(business_id: str) -> dict:
    """
    Check if a business can send an SMS (under monthly cap).

    Automatically resets counters if it's a new month.

    Args:
        business_id: The business to check

    Returns:
        dict: {
            'can_send': bool,
            'current_usage': int,
            'monthly_cap': int,
            'remaining': int,
            'reason': str (only if can_send is False),
            'resets_on': str (only if can_send is False)
        }
    """
    try:
        check_and_reset_if_new_month(business_id)

        result = supabase.table('businesses').select(
            'sms_sent_this_month, sms_monthly_cap'
        ).eq('id', business_id).limit(1).execute()

        if not result.data:
            return {'can_send': True, 'current_usage': 0, 'monthly_cap': DEFAULT_SMS_CAP, 'remaining': DEFAULT_SMS_CAP}

        biz = result.data[0]
        sent = biz.get('sms_sent_this_month') or 0
        cap = biz.get('sms_monthly_cap') or DEFAULT_SMS_CAP
        remaining = max(0, cap - sent)

        if sent >= cap:
            return {
                'can_send': False,
                'reason': 'Monthly SMS limit reached',
                'current_usage': sent,
                'monthly_cap': cap,
                'remaining': 0,
                'resets_on': _get_next_month_first()
            }

        return {
            'can_send': True,
            'current_usage': sent,
            'monthly_cap': cap,
            'remaining': remaining
        }

    except Exception as e:
        logger.error(f"Error checking SMS cap for {business_id}: {e}")
        return {'can_send': True, 'current_usage': 0, 'monthly_cap': DEFAULT_SMS_CAP, 'remaining': DEFAULT_SMS_CAP}


def can_send_email(business_id: str) -> dict:
    """
    Check if a business can send an email (under monthly cap).

    Automatically resets counters if it's a new month.

    Args:
        business_id: The business to check

    Returns:
        dict: {
            'can_send': bool,
            'current_usage': int,
            'monthly_cap': int,
            'remaining': int,
            'reason': str (only if can_send is False),
            'resets_on': str (only if can_send is False)
        }
    """
    try:
        check_and_reset_if_new_month(business_id)

        result = supabase.table('businesses').select(
            'email_sent_this_month, email_monthly_cap'
        ).eq('id', business_id).limit(1).execute()

        if not result.data:
            return {'can_send': True, 'current_usage': 0, 'monthly_cap': DEFAULT_EMAIL_CAP, 'remaining': DEFAULT_EMAIL_CAP}

        biz = result.data[0]
        sent = biz.get('email_sent_this_month') or 0
        cap = biz.get('email_monthly_cap') or DEFAULT_EMAIL_CAP
        remaining = max(0, cap - sent)

        if sent >= cap:
            return {
                'can_send': False,
                'reason': 'Monthly email limit reached',
                'current_usage': sent,
                'monthly_cap': cap,
                'remaining': 0,
                'resets_on': _get_next_month_first()
            }

        return {
            'can_send': True,
            'current_usage': sent,
            'monthly_cap': cap,
            'remaining': remaining
        }

    except Exception as e:
        logger.error(f"Error checking email cap for {business_id}: {e}")
        return {'can_send': True, 'current_usage': 0, 'monthly_cap': DEFAULT_EMAIL_CAP, 'remaining': DEFAULT_EMAIL_CAP}


def increment_sms_count(business_id: str, count: int = 1) -> dict:
    """
    Increment SMS counter after successfully sending.

    Args:
        business_id: The business that sent SMS
        count: Number of SMS sent (default 1)

    Returns:
        dict: {'sms_sent_this_month': int, 'sms_monthly_cap': int}
    """
    try:
        # Get current value and increment in Python (Supabase client doesn't support SQL increment)
        result = supabase.table('businesses').select(
            'sms_sent_this_month, sms_monthly_cap'
        ).eq('id', business_id).limit(1).execute()

        if not result.data:
            logger.warning(f"Business not found for SMS increment: {business_id}")
            return {'sms_sent_this_month': count, 'sms_monthly_cap': DEFAULT_SMS_CAP}

        current = result.data[0].get('sms_sent_this_month') or 0
        new_total = current + count

        supabase.table('businesses').update({
            'sms_sent_this_month': new_total
        }).eq('id', business_id).execute()

        cap = result.data[0].get('sms_monthly_cap') or DEFAULT_SMS_CAP
        logger.debug(f"SMS count incremented: business={business_id}, total={new_total}/{cap}")

        return {'sms_sent_this_month': new_total, 'sms_monthly_cap': cap}

    except Exception as e:
        logger.error(f"Error incrementing SMS count for {business_id}: {e}")
        return {'sms_sent_this_month': 0, 'sms_monthly_cap': DEFAULT_SMS_CAP}


def increment_email_count(business_id: str, count: int = 1) -> dict:
    """
    Increment email counter after successfully sending.

    Args:
        business_id: The business that sent email
        count: Number of emails sent (default 1)

    Returns:
        dict: {'email_sent_this_month': int, 'email_monthly_cap': int}
    """
    try:
        result = supabase.table('businesses').select(
            'email_sent_this_month, email_monthly_cap'
        ).eq('id', business_id).limit(1).execute()

        if not result.data:
            logger.warning(f"Business not found for email increment: {business_id}")
            return {'email_sent_this_month': count, 'email_monthly_cap': DEFAULT_EMAIL_CAP}

        current = result.data[0].get('email_sent_this_month') or 0
        new_total = current + count

        supabase.table('businesses').update({
            'email_sent_this_month': new_total
        }).eq('id', business_id).execute()

        cap = result.data[0].get('email_monthly_cap') or DEFAULT_EMAIL_CAP
        logger.debug(f"Email count incremented: business={business_id}, total={new_total}/{cap}")

        return {'email_sent_this_month': new_total, 'email_monthly_cap': cap}

    except Exception as e:
        logger.error(f"Error incrementing email count for {business_id}: {e}")
        return {'email_sent_this_month': 0, 'email_monthly_cap': DEFAULT_EMAIL_CAP}


def get_usage_stats(business_id: str) -> dict:
    """
    Get current usage stats for dashboard display.

    Automatically resets counters if it's a new month.

    Args:
        business_id: The business to get stats for

    Returns:
        dict: {
            'sms': {
                'sent_this_month': int,
                'monthly_cap': int,
                'remaining': int,
                'percentage_used': float,
                'resets_on': str
            },
            'email': {
                'sent_this_month': int,
                'monthly_cap': int,
                'remaining': int,
                'percentage_used': float,
                'resets_on': str
            }
        }
    """
    try:
        check_and_reset_if_new_month(business_id)

        result = supabase.table('businesses').select(
            'sms_sent_this_month, sms_monthly_cap, '
            'email_sent_this_month, email_monthly_cap'
        ).eq('id', business_id).limit(1).execute()

        if not result.data:
            resets_on = _get_next_month_first()
            return {
                'sms': {'sent_this_month': 0, 'monthly_cap': DEFAULT_SMS_CAP, 'remaining': DEFAULT_SMS_CAP, 'percentage_used': 0.0, 'resets_on': resets_on},
                'email': {'sent_this_month': 0, 'monthly_cap': DEFAULT_EMAIL_CAP, 'remaining': DEFAULT_EMAIL_CAP, 'percentage_used': 0.0, 'resets_on': resets_on}
            }

        biz = result.data[0]
        resets_on = _get_next_month_first()

        sms_sent = biz.get('sms_sent_this_month') or 0
        sms_cap = biz.get('sms_monthly_cap') or DEFAULT_SMS_CAP
        email_sent = biz.get('email_sent_this_month') or 0
        email_cap = biz.get('email_monthly_cap') or DEFAULT_EMAIL_CAP

        return {
            'sms': {
                'sent_this_month': sms_sent,
                'monthly_cap': sms_cap,
                'remaining': max(0, sms_cap - sms_sent),
                'percentage_used': round((sms_sent / sms_cap * 100), 1) if sms_cap > 0 else 0.0,
                'resets_on': resets_on
            },
            'email': {
                'sent_this_month': email_sent,
                'monthly_cap': email_cap,
                'remaining': max(0, email_cap - email_sent),
                'percentage_used': round((email_sent / email_cap * 100), 1) if email_cap > 0 else 0.0,
                'resets_on': resets_on
            }
        }

    except Exception as e:
        logger.error(f"Error getting usage stats for {business_id}: {e}")
        resets_on = _get_next_month_first()
        return {
            'sms': {'sent_this_month': 0, 'monthly_cap': DEFAULT_SMS_CAP, 'remaining': DEFAULT_SMS_CAP, 'percentage_used': 0.0, 'resets_on': resets_on},
            'email': {'sent_this_month': 0, 'monthly_cap': DEFAULT_EMAIL_CAP, 'remaining': DEFAULT_EMAIL_CAP, 'percentage_used': 0.0, 'resets_on': resets_on}
        }


def check_approaching_limit(business_id: str, threshold: float = 0.8) -> dict:
    """
    Check if a business is approaching their monthly cap.

    Used to trigger warning emails at 80% usage.

    Args:
        business_id: The business to check
        threshold: Percentage threshold (0.8 = 80%)

    Returns:
        dict: {
            'sms_warning': bool,
            'email_warning': bool,
            'sms_percentage': float,
            'email_percentage': float
        }
    """
    try:
        stats = get_usage_stats(business_id)

        sms_pct = stats['sms']['percentage_used']
        email_pct = stats['email']['percentage_used']
        threshold_pct = threshold * 100

        return {
            'sms_warning': sms_pct >= threshold_pct,
            'email_warning': email_pct >= threshold_pct,
            'sms_percentage': sms_pct,
            'email_percentage': email_pct
        }

    except Exception as e:
        logger.error(f"Error checking approaching limit for {business_id}: {e}")
        return {
            'sms_warning': False,
            'email_warning': False,
            'sms_percentage': 0.0,
            'email_percentage': 0.0
        }
