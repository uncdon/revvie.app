"""
Link Tracking Service - generates short tracking URLs and logs clicks.

HOW IT WORKS:
=============
1. When a review request is sent, we generate a short tracking URL
   e.g., https://app.revvie.app/r/a3Fx9Kp
2. The email/SMS contains this short URL instead of the raw Google URL
3. Customer clicks the short URL -> hits our redirect endpoint
4. We log the click (timestamp, device, source) then redirect to Google
5. Business owner sees click analytics on their dashboard

TABLES USED:
============
- tracking_links: maps short_code -> destination_url, linked to review_request
- link_clicks: one row per click, stores device_type, user_agent, etc.
"""

import os
import logging
import secrets
import string
from datetime import datetime, timezone, timedelta

from app.services.supabase_service import supabase_admin

logger = logging.getLogger(__name__)

# Base URL for building short links (e.g., https://app.revvie.app)
APP_BASE_URL = os.getenv('APP_BASE_URL', 'http://localhost:5001')

# Alphabet excluding confusing characters: 0, O, 1, l, I
ALPHABET = ''.join(
    c for c in string.ascii_letters + string.digits
    if c not in '0O1lI'
)

SHORT_CODE_LENGTH = 7
MAX_GENERATION_ATTEMPTS = 5


# ============================================================================
# FUNCTION 1: generate_short_code
# ============================================================================

def generate_short_code() -> str:
    """
    Generate a unique 7-character alphanumeric short code.

    Uses a-z, A-Z, 0-9 minus confusing characters (0, O, 1, l, I).
    Checks the tracking_links table to guarantee uniqueness.
    Retries up to 5 times on collision.

    Returns:
        A unique 7-char string like "a3Fx9Kp"

    Raises:
        RuntimeError: If a unique code can't be generated after 5 attempts
    """
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        code = ''.join(secrets.choice(ALPHABET) for _ in range(SHORT_CODE_LENGTH))

        # Check uniqueness against the database
        result = supabase_admin.table('tracking_links') \
            .select('id') \
            .eq('short_code', code) \
            .limit(1) \
            .execute()

        if not result.data:
            logger.debug(f"Generated unique short code on attempt {attempt}: {code}")
            return code

        logger.warning(f"Short code collision on attempt {attempt}: {code}")

    raise RuntimeError(
        f"Failed to generate unique short code after {MAX_GENERATION_ATTEMPTS} attempts"
    )


# ============================================================================
# FUNCTION 2: create_tracking_link
# ============================================================================

def create_tracking_link(
    business_id: str,
    destination_url: str,
    review_request_id: str = None,
    queued_request_id: str = None,
) -> dict | None:
    """
    Create a short tracking URL for a review request.

    Args:
        business_id: UUID of the business sending the request
        destination_url: Where the link ultimately goes (Google review page)
        review_request_id: Optional FK to review_requests table
        queued_request_id: Optional FK to queued_review_requests table

    Returns:
        dict with id, short_code, short_url, destination_url — or None on error

    Example:
        link = create_tracking_link(
            business_id="d03c6025-...",
            destination_url="https://search.google.com/local/writereview?placeid=ChIJ...",
            review_request_id="3c0ad49e-..."
        )
        # link['short_url'] -> "https://app.revvie.app/r/a3Fx9Kp"
    """
    try:
        short_code = generate_short_code()

        record = {
            'business_id': business_id,
            'short_code': short_code,
            'destination_url': destination_url,
        }
        if review_request_id:
            record['review_request_id'] = review_request_id
        if queued_request_id:
            record['queued_request_id'] = queued_request_id

        result = supabase_admin.table('tracking_links').insert(record).execute()

        if not result.data:
            logger.error("tracking_links insert returned no data")
            return None

        row = result.data[0]
        short_url = f"{APP_BASE_URL}/r/{short_code}"

        logger.info(f"Created tracking link {short_code} -> {destination_url[:60]}...")

        return {
            'id': row['id'],
            'short_code': short_code,
            'short_url': short_url,
            'destination_url': destination_url,
        }

    except Exception as e:
        logger.error(f"Failed to create tracking link: {e}")
        return None


# ============================================================================
# FUNCTION 3: get_tracking_link
# ============================================================================

def get_tracking_link(short_code: str) -> dict | None:
    """
    Look up a tracking link by its short code.

    Args:
        short_code: The 7-char code from the URL (e.g., "a3Fx9Kp")

    Returns:
        Full tracking_links record as dict, or None if not found
    """
    try:
        result = supabase_admin.table('tracking_links') \
            .select('*') \
            .eq('short_code', short_code) \
            .limit(1) \
            .execute()

        if result.data:
            return result.data[0]

        logger.warning(f"Tracking link not found for short code: {short_code}")
        return None

    except Exception as e:
        logger.error(f"Error looking up tracking link {short_code}: {e}")
        return None


# ============================================================================
# FUNCTION 4: log_click
# ============================================================================

def _detect_device_type(user_agent: str | None) -> str:
    """Detect device type from user agent string."""
    if not user_agent:
        return 'unknown'

    ua = user_agent.lower()

    # Check tablet before mobile (iPad contains 'mobile' in some UAs)
    if 'ipad' in ua or 'tablet' in ua:
        return 'tablet'

    mobile_keywords = ['iphone', 'android', 'mobile', 'webos', 'blackberry']
    if any(kw in ua for kw in mobile_keywords):
        return 'mobile'

    return 'desktop'


def _detect_clicked_from(user_agent: str | None) -> str:
    """Detect whether the click came from an email client or SMS/browser."""
    if not user_agent:
        return 'unknown'

    email_clients = [
        'outlook', 'thunderbird', 'apple mail', 'yahoomail',
        'gmail', 'spark', 'airmail',
    ]
    ua_lower = user_agent.lower()

    if any(client in ua_lower for client in email_clients):
        return 'email'

    return 'sms'


def log_click(
    tracking_link_id: str,
    user_agent: str = None,
    ip_address: str = None,
) -> str | None:
    """
    Record a click on a tracking link.

    Detects device type and click source from the user agent, then
    inserts a row into link_clicks. Never raises — a failed click log
    must never break the redirect.

    Args:
        tracking_link_id: UUID of the tracking_links row
        user_agent: Raw User-Agent header string
        ip_address: Client IP address

    Returns:
        The click record UUID, or None on error
    """
    try:
        device_type = _detect_device_type(user_agent)
        clicked_from = _detect_clicked_from(user_agent)

        record = {
            'tracking_link_id': tracking_link_id,
            'user_agent': user_agent,
            'ip_address': ip_address,
            'device_type': device_type,
            'clicked_from': clicked_from,
        }

        result = supabase_admin.table('link_clicks').insert(record).execute()

        if result.data:
            click_id = result.data[0]['id']
            logger.info(
                f"Logged click {click_id} on link {tracking_link_id} "
                f"(device={device_type}, from={clicked_from})"
            )
            return click_id

        logger.error(f"link_clicks insert returned no data for link {tracking_link_id}")
        return None

    except Exception as e:
        logger.error(f"Failed to log click for link {tracking_link_id}: {e}")
        return None


# ============================================================================
# FUNCTION 5: get_stats_for_business
# ============================================================================

def get_stats_for_business(business_id: str, days: int = 30) -> dict:
    """
    Get click analytics for a business's review requests.

    Joins review_requests -> tracking_links -> link_clicks to produce
    a summary and per-request breakdown.

    Args:
        business_id: UUID of the business
        days: How many days back to look (default 30)

    Returns:
        {
            'summary': {
                'total_sent': int,
                'total_clicked': int,
                'click_rate': float,
                'mobile_clicks': int,
                'desktop_clicks': int,
                'tablet_clicks': int
            },
            'recent_requests': [ ... ]
        }
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    summary = {
        'total_sent': 0,
        'total_clicked': 0,
        'click_rate': 0.0,
        'mobile_clicks': 0,
        'desktop_clicks': 0,
        'tablet_clicks': 0,
    }
    recent_requests = []

    try:
        # ── Get recent review requests for this business ──
        rr_result = supabase_admin.table('review_requests') \
            .select('id, customer_name, customer_email, customer_phone, sent_at, method') \
            .eq('business_id', business_id) \
            .gte('sent_at', cutoff) \
            .order('sent_at', desc=True) \
            .limit(50) \
            .execute()

        requests_list = rr_result.data or []
        summary['total_sent'] = len(requests_list)

        if not requests_list:
            logger.debug(f"No review requests in last {days} days for business {business_id}")
            return {'summary': summary, 'recent_requests': []}

        request_ids = [r['id'] for r in requests_list]

        # ── Get tracking links for those requests ──
        tl_result = supabase_admin.table('tracking_links') \
            .select('id, review_request_id') \
            .in_('review_request_id', request_ids) \
            .execute()

        tracking_links = tl_result.data or []

        # Map review_request_id -> tracking_link_id
        rr_to_tl = {}
        tl_ids = []
        for tl in tracking_links:
            rr_to_tl[tl['review_request_id']] = tl['id']
            tl_ids.append(tl['id'])

        # ── Get clicks for those tracking links ──
        clicks_by_link = {}  # tracking_link_id -> list of click records
        if tl_ids:
            clicks_result = supabase_admin.table('link_clicks') \
                .select('id, tracking_link_id, device_type, clicked_from, clicked_at') \
                .in_('tracking_link_id', tl_ids) \
                .order('clicked_at', desc=False) \
                .execute()

            for click in (clicks_result.data or []):
                tlid = click['tracking_link_id']
                clicks_by_link.setdefault(tlid, []).append(click)

                # Aggregate device counts
                dt = click.get('device_type', 'unknown')
                if dt == 'mobile':
                    summary['mobile_clicks'] += 1
                elif dt == 'desktop':
                    summary['desktop_clicks'] += 1
                elif dt == 'tablet':
                    summary['tablet_clicks'] += 1

        # ── Build per-request breakdown ──
        clicked_set = set()
        for rr in requests_list:
            rr_id = rr['id']
            tl_id = rr_to_tl.get(rr_id)
            clicks = clicks_by_link.get(tl_id, []) if tl_id else []
            clicked = len(clicks) > 0

            if clicked:
                clicked_set.add(rr_id)

            recent_requests.append({
                'id': rr_id,
                'customer_name': rr.get('customer_name'),
                'customer_email': rr.get('customer_email'),
                'customer_phone': rr.get('customer_phone'),
                'sent_at': rr.get('sent_at'),
                'method': rr.get('method'),
                'clicked': clicked,
                'click_count': len(clicks),
                'first_clicked_at': clicks[0]['clicked_at'] if clicks else None,
                'device_type': clicks[0].get('device_type') if clicks else None,
            })

        summary['total_clicked'] = len(clicked_set)
        if summary['total_sent'] > 0:
            summary['click_rate'] = round(
                summary['total_clicked'] / summary['total_sent'], 4
            )

        logger.info(
            f"Stats for business {business_id}: "
            f"{summary['total_sent']} sent, {summary['total_clicked']} clicked "
            f"({summary['click_rate']:.1%})"
        )

    except Exception as e:
        logger.error(f"Error fetching stats for business {business_id}: {e}")

    return {'summary': summary, 'recent_requests': recent_requests}
