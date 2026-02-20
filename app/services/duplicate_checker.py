"""
Duplicate prevention service for review requests.

Checks if a customer has already received a review request within
the business's cooldown window before allowing another send.
"""

import logging
from datetime import datetime, timezone, timedelta

from app.services.supabase_service import supabase_admin

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_DAYS = 30


def get_cooldown_setting(business_id: str) -> int:
    """Get a business's review request cooldown setting in days.

    Args:
        business_id: The business UUID.

    Returns:
        Number of cooldown days, or 30 as default.
    """
    try:
        result = supabase_admin.table("businesses") \
            .select("review_request_cooldown_days") \
            .eq("id", business_id) \
            .limit(1) \
            .execute()

        if result.data and result.data[0].get("review_request_cooldown_days") is not None:
            return int(result.data[0]["review_request_cooldown_days"])

        return DEFAULT_COOLDOWN_DAYS

    except Exception as e:
        logger.error(f"Error fetching cooldown setting for business {business_id}: {e}")
        return DEFAULT_COOLDOWN_DAYS


def can_send_review_request(business_id: str, customer_email: str = None, customer_phone: str = None) -> dict:
    """Check if it's safe to send a review request to this customer.

    Looks for recent requests in both review_requests and queued_review_requests
    tables, matching on email OR phone within the cooldown window.

    Args:
        business_id: The business UUID.
        customer_email: Customer's email address (optional).
        customer_phone: Customer's phone number (optional).

    Returns:
        Dict with 'can_send' bool and details if blocked.
    """
    if not customer_email and not customer_phone:
        logger.warning(f"No email or phone provided for duplicate check (business {business_id})")
        return {"can_send": True, "blocked": False}

    try:
        cooldown_days = get_cooldown_setting(business_id)

        # Business disabled duplicate prevention
        if cooldown_days == 0:
            logger.info(f"Cooldown disabled for business {business_id}, allowing send")
            return {"can_send": True, "blocked": False}

        cutoff = (datetime.now(timezone.utc) - timedelta(days=cooldown_days)).isoformat()

        # Check review_requests table
        last_sent = _check_review_requests(business_id, customer_email, customer_phone, cutoff)

        # Check queued_review_requests table
        if not last_sent:
            last_sent = _check_queued_requests(business_id, customer_email, customer_phone, cutoff)

        if last_sent:
            sent_at = last_sent["sent_at"]
            days_ago = (datetime.now(timezone.utc) - sent_at).days

            logger.info(
                f"Duplicate blocked for business {business_id}: "
                f"email={customer_email}, phone={customer_phone}, "
                f"last sent {days_ago} days ago"
            )

            return {
                "can_send": False,
                "blocked": True,
                "reason": f"Already sent {days_ago} days ago",
                "last_sent_at": sent_at.isoformat(),
                "last_request_id": last_sent["id"],
                "cooldown_days": cooldown_days,
                "days_remaining": max(0, cooldown_days - days_ago),
                "allow_override": True,
            }

        return {"can_send": True, "blocked": False}

    except Exception as e:
        logger.error(f"Error checking duplicates for business {business_id}: {e}")
        return {"can_send": True, "blocked": False}


def _check_review_requests(business_id: str, email: str, phone: str, cutoff: str) -> dict | None:
    """Check the review_requests table for a recent send."""
    try:
        query = supabase_admin.table("review_requests") \
            .select("id, sent_at, customer_name, method") \
            .eq("business_id", business_id) \
            .neq("status", "failed") \
            .gte("sent_at", cutoff) \
            .order("sent_at", desc=True) \
            .limit(1)

        if email and phone:
            query = query.or_(f"customer_email.eq.{email},customer_phone.eq.{phone}")
        elif email:
            query = query.eq("customer_email", email)
        else:
            query = query.eq("customer_phone", phone)

        result = query.execute()

        if result.data:
            row = result.data[0]
            return {
                "id": row["id"],
                "sent_at": datetime.fromisoformat(row["sent_at"].replace("Z", "+00:00")),
                "customer_name": row.get("customer_name"),
                "method": row.get("method"),
            }

        return None

    except Exception as e:
        logger.error(f"Error querying review_requests: {e}")
        return None


def _check_queued_requests(business_id: str, email: str, phone: str, cutoff: str) -> dict | None:
    """Check the queued_review_requests table for a recent or pending send."""
    try:
        query = supabase_admin.table("queued_review_requests") \
            .select("id, scheduled_send_at, customer_name") \
            .eq("business_id", business_id) \
            .neq("status", "cancelled") \
            .neq("status", "failed") \
            .gte("scheduled_send_at", cutoff) \
            .order("scheduled_send_at", desc=True) \
            .limit(1)

        if email and phone:
            query = query.or_(f"customer_email.eq.{email},customer_phone.eq.{phone}")
        elif email:
            query = query.eq("customer_email", email)
        else:
            query = query.eq("customer_phone", phone)

        result = query.execute()

        if result.data:
            row = result.data[0]
            return {
                "id": row["id"],
                "sent_at": datetime.fromisoformat(row["scheduled_send_at"].replace("Z", "+00:00")),
                "customer_name": row.get("customer_name"),
            }

        return None

    except Exception as e:
        logger.error(f"Error querying queued_review_requests: {e}")
        return None


def check_bulk_duplicates(business_id: str, customers_list: list) -> dict:
    """Check an entire list of customers for duplicates.

    Used for CSV imports and bulk sends. Separates customers into
    safe_to_send and duplicates based on the business's cooldown window.

    Args:
        business_id: The business UUID.
        customers_list: List of dicts with 'email', 'phone', and 'name' keys.

    Returns:
        Dict with safe_to_send list, duplicates list, and summary counts.
    """
    safe_to_send = []
    duplicates = []
    total_count = len(customers_list)

    logger.info(f"Bulk duplicate check for business {business_id}: {total_count} customers")

    for customer in customers_list:
        email = customer.get("email")
        phone = customer.get("phone")
        name = customer.get("name", "")

        result = can_send_review_request(business_id, email, phone)

        if result.get("can_send"):
            safe_to_send.append({
                "email": email,
                "phone": phone,
                "name": name,
            })
        else:
            duplicates.append({
                "email": email,
                "phone": phone,
                "name": name,
                "last_sent_at": result.get("last_sent_at"),
                "days_ago": (datetime.now(timezone.utc) - datetime.fromisoformat(result["last_sent_at"])).days
                    if result.get("last_sent_at") else None,
            })

    safe_count = len(safe_to_send)
    duplicate_count = len(duplicates)

    logger.info(
        f"Bulk check complete for business {business_id}: "
        f"{safe_count} safe, {duplicate_count} duplicates"
    )

    return {
        "safe_to_send": safe_to_send,
        "duplicates": duplicates,
        "safe_count": safe_count,
        "duplicate_count": duplicate_count,
        "total_count": total_count,
        "summary": f"Found {duplicate_count} duplicates out of {total_count} customers",
    }
