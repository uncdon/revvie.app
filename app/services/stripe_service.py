"""
Stripe Billing Service - handles subscriptions, checkout, and billing events.

Stripe is used for:
- Creating customers and linking them to businesses
- Generating Checkout sessions for new subscriptions (with 14-day trial)
- Customer Portal for self-service billing management
- Processing webhook events (subscription changes, payments)

FLOW:
=====
1. Business signs up -> free trial starts automatically
2. After onboarding, we create a Stripe customer + Checkout session
3. Stripe handles the trial period and first charge
4. Webhooks keep our database in sync with Stripe's state
5. Business can manage billing via Stripe Customer Portal

ENVIRONMENT VARIABLES:
======================
    STRIPE_SECRET_KEY       - Stripe API secret key
    STRIPE_PRICE_ID         - Price ID for the subscription plan
    APP_BASE_URL            - Base URL for redirect URLs
"""

import os
import logging
from datetime import datetime, timezone

import stripe
from dotenv import load_dotenv

from app.services.supabase_service import supabase_admin as supabase
from app.services.email_service import (
    send_trial_welcome_email,
    send_trial_ending_email,
    send_payment_failed_email,
    send_subscription_canceled_email,
)

load_dotenv()

logger = logging.getLogger(__name__)

# Configure Stripe
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

PRICE_ID = os.getenv('STRIPE_PRICE_ID')
APP_BASE_URL = os.getenv('APP_BASE_URL', 'http://localhost:5000')


# =============================================================================
# FUNCTION 1: CREATE CUSTOMER
# =============================================================================

def create_customer(business_id: str, email: str, business_name: str):
    """
    Create a Stripe customer for a business and save the ID to the database.

    Args:
        business_id: The business UUID
        email: Business owner's email
        business_name: Name of the business

    Returns:
        stripe.Customer object, or None on error
    """
    try:
        customer = stripe.Customer.create(
            email=email,
            name=business_name,
            metadata={
                'business_id': business_id,
                'source': 'revvie'
            }
        )

        logger.info(f"Created Stripe customer {customer.id} for business {business_id}")

        # Save stripe_customer_id to businesses table
        supabase.table('businesses').update({
            'stripe_customer_id': customer.id
        }).eq('id', business_id).execute()

        return customer

    except Exception as e:
        logger.error(f"Failed to create Stripe customer for business {business_id}: {e}")
        return None


# =============================================================================
# FUNCTION 2: CREATE CHECKOUT SESSION
# =============================================================================

def create_checkout_session(business_id: str, email: str, business_name: str, discount_percent: int = None):
    """
    Create a Stripe Checkout session for a new subscription.

    Trial eligibility: only granted to businesses that have never had a trial
    (has_had_trial = false). Returning subscribers are charged immediately.

    Referral discount eligibility: only applied if the business has not already
    used their one-time referral credit (referral_credit_used = false).

    Args:
        business_id: The business UUID
        email: Business owner's email
        business_name: Name of the business
        discount_percent: Optional one-time referral discount (e.g., 50 for 50% off)

    Returns:
        stripe.checkout.Session object, or None on error
    """
    try:
        # Fetch Stripe customer ID + abuse-prevention flags in one query
        biz_result = supabase.table('businesses').select(
            'stripe_customer_id, has_had_trial, referral_credit_used'
        ).eq('id', business_id).execute()

        if not biz_result.data:
            logger.error(f"Business {business_id} not found")
            return None

        biz = biz_result.data[0]
        stripe_customer_id = biz.get('stripe_customer_id')

        if not stripe_customer_id:
            customer = create_customer(business_id, email, business_name)
            if not customer:
                logger.error(f"Cannot create checkout session: failed to create customer for {business_id}")
                return None
            stripe_customer_id = customer.id

        # --- Trial eligibility ---
        if not biz.get('has_had_trial', False):
            trial_days = 14
            logger.info(f"Business {business_id} eligible for 14-day trial")
        else:
            trial_days = 0
            logger.info(f"Business {business_id} not eligible for trial (has_had_trial=true) — charging immediately")

        # --- Referral discount eligibility ---
        discounts = []
        if discount_percent and not biz.get('referral_credit_used', False):
            coupon = stripe.Coupon.create(
                percent_off=discount_percent,
                duration='once',
                name='Referral Discount'
            )
            discounts = [{'coupon': coupon.id}]
            logger.info(f"Applied {discount_percent}% referral discount coupon {coupon.id} for business {business_id}")
        elif discount_percent and biz.get('referral_credit_used', False):
            logger.warning(f"Business {business_id} attempted to reuse referral discount — blocked")

        # --- Build checkout session ---
        # Only pass discounts kwarg when there's actually a coupon — passing an
        # empty list can cause Stripe API errors in some configurations.
        session_kwargs = dict(
            customer=stripe_customer_id,
            payment_method_types=['card'],
            line_items=[{'price': PRICE_ID, 'quantity': 1}],
            mode='subscription',
            subscription_data={
                'trial_period_days': trial_days,
                'metadata': {'business_id': business_id},
                'invoice_settings': {
                    'metadata': {'business_id': business_id}
                },
            },
            success_url=f'{APP_BASE_URL}/billing/success?session_id={{CHECKOUT_SESSION_ID}}',
            cancel_url=f'{APP_BASE_URL}/billing/canceled',
            metadata={'business_id': business_id}
        )
        if discounts:
            session_kwargs['discounts'] = discounts

        session = stripe.checkout.Session.create(**session_kwargs)

        logger.info(
            f"Created checkout session {session.id} for business {business_id} "
            f"(trial_days={trial_days}, discount={'yes' if discounts else 'no'})"
        )
        return session

    except Exception as e:
        logger.error(f"Failed to create checkout session for business {business_id}: {e}")
        return None


# =============================================================================
# FUNCTION 3: CREATE PORTAL SESSION
# =============================================================================

def create_portal_session(business_id: str):
    """
    Create a Stripe Customer Portal session.

    The portal lets the business owner:
    - Update their credit card
    - Cancel their subscription
    - View past invoices

    Args:
        business_id: The business UUID

    Returns:
        stripe.billing_portal.Session object, or None on error
    """
    try:
        biz_result = supabase.table('businesses').select(
            'stripe_customer_id'
        ).eq('id', business_id).execute()

        if not biz_result.data:
            logger.warning(f"Business {business_id} not found for portal session")
            return None

        stripe_customer_id = biz_result.data[0].get('stripe_customer_id')
        if not stripe_customer_id:
            logger.warning(f"Business {business_id} has no Stripe customer ID - billing not set up")
            return None

        session = stripe.billing_portal.Session.create(
            customer=stripe_customer_id,
            return_url=f'{APP_BASE_URL}/dashboard'
        )

        logger.info(f"Created portal session for business {business_id}")
        return session

    except Exception as e:
        logger.error(f"Failed to create portal session for business {business_id}: {e}")
        return None


# =============================================================================
# FUNCTION 4: GET SUBSCRIPTION STATUS
# =============================================================================

def get_subscription_status(business_id: str) -> dict:
    """
    Get the current subscription status for a business.

    Args:
        business_id: The business UUID

    Returns:
        dict with status info:
        {
            'status': 'trialing' | 'active' | 'past_due' | 'canceled' | 'unpaid' | 'none',
            'trial_ends_at': ISO timestamp or None,
            'subscription_ends_at': ISO timestamp or None,
            'is_active': bool,
            'days_remaining_in_trial': int or None,
            'stripe_customer_id': str or None
        }
    """
    try:
        biz_result = supabase.table('businesses').select(
            'stripe_customer_id, stripe_subscription_id, subscription_status, '
            'trial_ends_at, subscription_ends_at'
        ).eq('id', business_id).execute()

        if not biz_result.data:
            return {
                'status': 'none',
                'trial_ends_at': None,
                'subscription_ends_at': None,
                'is_active': False,
                'days_remaining_in_trial': None,
                'stripe_customer_id': None
            }

        biz = biz_result.data[0]
        status = biz.get('subscription_status') or 'none'
        trial_ends_at = biz.get('trial_ends_at')
        is_active = status in ['trialing', 'active']

        # Calculate days remaining in trial
        days_remaining = None
        if status == 'trialing' and trial_ends_at:
            try:
                trial_end = datetime.fromisoformat(trial_ends_at.replace('Z', '+00:00'))
                delta = trial_end - datetime.now(timezone.utc)
                days_remaining = max(0, delta.days)
            except Exception:
                pass

        return {
            'status': status,
            'trial_ends_at': trial_ends_at,
            'subscription_ends_at': biz.get('subscription_ends_at'),
            'is_active': is_active,
            'days_remaining_in_trial': days_remaining,
            'stripe_customer_id': biz.get('stripe_customer_id')
        }

    except Exception as e:
        logger.error(f"Failed to get subscription status for business {business_id}: {e}")
        return {
            'status': 'none',
            'trial_ends_at': None,
            'subscription_ends_at': None,
            'is_active': False,
            'days_remaining_in_trial': None,
            'stripe_customer_id': None
        }


# =============================================================================
# FUNCTION 5: HANDLE SUBSCRIPTION CREATED
# =============================================================================

def handle_subscription_created(subscription, business_id: str) -> None:
    """
    Update database when a new subscription is created.

    Called by the webhook handler when we receive a
    customer.subscription.created event.

    Args:
        subscription: The Stripe Subscription object
        business_id: The business UUID from subscription metadata
    """
    try:
        # Convert Unix timestamps to ISO format
        trial_ends_at = None
        if subscription.trial_end:
            trial_ends_at = datetime.fromtimestamp(
                subscription.trial_end, tz=timezone.utc
            ).isoformat()

        # current_period_end moved to subscription items in newer Stripe API versions
        subscription_ends_at = None
        period_end = getattr(subscription, 'current_period_end', None)
        if not period_end:
            # Fall back to billing_cycle_anchor
            period_end = getattr(subscription, 'billing_cycle_anchor', None)
        if period_end:
            subscription_ends_at = datetime.fromtimestamp(
                period_end, tz=timezone.utc
            ).isoformat()

        # Update businesses table.
        # has_had_trial=True is set permanently here — even if this subscription
        # is later canceled, the flag stays so they can't get another free trial.
        # first_subscription_at is only written once (COALESCE handled app-side
        # by only updating when not already set).
        biz_check = supabase.table('businesses').select(
            'first_subscription_at'
        ).eq('id', business_id).execute()

        first_sub_at = None
        if biz_check.data and not biz_check.data[0].get('first_subscription_at'):
            first_sub_at = datetime.now(timezone.utc).isoformat()

        update_payload = {
            'stripe_subscription_id': subscription.id,
            'subscription_status': subscription.status,
            'trial_ends_at': trial_ends_at,
            'subscription_ends_at': subscription_ends_at,
            'has_had_trial': True,
        }
        if first_sub_at:
            update_payload['first_subscription_at'] = first_sub_at

        supabase.table('businesses').update(update_payload).eq('id', business_id).execute()

        logger.info(f"Subscription {subscription.id} created for business {business_id} "
                     f"(status: {subscription.status}, has_had_trial stamped)")

        # Log billing event
        _log_billing_event(business_id, 'subscription_created', {
            'subscription_id': subscription.id,
            'status': subscription.status,
            'trial_end': trial_ends_at
        })

        # Send trial welcome email if trialing
        if subscription.status == 'trialing':
            _send_trial_welcome(business_id, subscription)

    except Exception as e:
        logger.error(f"Failed to handle subscription created for business {business_id}: {e}")


# =============================================================================
# FUNCTION 6: HANDLE SUBSCRIPTION UPDATED
# =============================================================================

def handle_subscription_updated(subscription, business_id: str) -> None:
    """
    Update database when a subscription changes.

    Called by the webhook handler for customer.subscription.updated events.
    Handles status transitions like trial -> active, active -> past_due, etc.

    Args:
        subscription: The Stripe Subscription object
        business_id: The business UUID from subscription metadata
    """
    try:
        # Get current status before updating
        biz_result = supabase.table('businesses').select(
            'subscription_status'
        ).eq('id', business_id).execute()

        old_status = None
        if biz_result.data:
            old_status = biz_result.data[0].get('subscription_status')

        new_status = subscription.status

        # Convert timestamps
        trial_ends_at = None
        if subscription.trial_end:
            trial_ends_at = datetime.fromtimestamp(
                subscription.trial_end, tz=timezone.utc
            ).isoformat()

        # current_period_end moved to subscription items in newer Stripe API versions
        subscription_ends_at = None
        period_end = getattr(subscription, 'current_period_end', None)
        if not period_end:
            period_end = getattr(subscription, 'billing_cycle_anchor', None)
        if period_end:
            subscription_ends_at = datetime.fromtimestamp(
                period_end, tz=timezone.utc
            ).isoformat()

        # Update businesses table
        supabase.table('businesses').update({
            'subscription_status': new_status,
            'trial_ends_at': trial_ends_at,
            'subscription_ends_at': subscription_ends_at
        }).eq('id', business_id).execute()

        logger.info(f"Subscription updated for business {business_id}: "
                     f"{old_status} -> {new_status}")

        # Log billing event
        _log_billing_event(business_id, 'subscription_updated', {
            'subscription_id': subscription.id,
            'old_status': old_status,
            'new_status': new_status
        })

        # Handle status transitions
        if old_status != new_status:
            if new_status == 'past_due':
                _send_payment_failed(business_id)
            elif new_status == 'canceled':
                _send_cancellation(business_id)

    except Exception as e:
        logger.error(f"Failed to handle subscription updated for business {business_id}: {e}")


# =============================================================================
# FUNCTION 7: HANDLE SUBSCRIPTION DELETED
# =============================================================================

def handle_subscription_deleted(subscription, business_id: str) -> None:
    """
    Handle subscription fully canceled/deleted.

    Called by the webhook handler for customer.subscription.deleted events.

    Args:
        subscription: The Stripe Subscription object
        business_id: The business UUID from subscription metadata
    """
    try:
        # Business may have already been deleted — update is a no-op if so
        supabase.table('businesses').update({
            'subscription_status': 'canceled'
        }).eq('id', business_id).execute()

        logger.info(f"Subscription deleted for business {business_id}")

        # _log_billing_event checks existence before inserting
        _log_billing_event(business_id, 'subscription_deleted', {
            'subscription_id': subscription.id
        })

        _send_cancellation(business_id)

    except Exception as e:
        logger.warning(f"Failed to handle subscription deleted for business {business_id}: {e}")


# =============================================================================
# FUNCTION 8: HANDLE PAYMENT SUCCEEDED
# =============================================================================

def handle_payment_succeeded(invoice, business_id: str) -> None:
    """
    Handle successful payment.

    Called by the webhook handler for invoice.payment_succeeded events.

    Args:
        invoice: The Stripe Invoice object
        business_id: The business UUID
    """
    try:
        amount = (invoice.amount_paid or 0) / 100
        billing_reason = getattr(invoice, 'billing_reason', None)

        logger.info(f"[REFERRAL_DEBUG] handle_payment_succeeded called: "
                    f"business_id={business_id} amount=${amount:.2f} billing_reason={billing_reason}")

        _log_billing_event(business_id, 'payment_succeeded', {
            'invoice_id': invoice.id,
            'amount': amount,
            'description': f'Payment of ${amount:.2f}'
        })

        logger.info(f"Payment succeeded for business {business_id}: ${amount:.2f}")

        # If was past_due, update to active
        biz_result = supabase.table('businesses').select(
            'subscription_status'
        ).eq('id', business_id).execute()

        if biz_result.data and biz_result.data[0].get('subscription_status') == 'past_due':
            supabase.table('businesses').update({
                'subscription_status': 'active'
            }).eq('id', business_id).execute()
            logger.info(f"Business {business_id} status updated from past_due to active")

        # Handle first subscription payment (subscription_create)
        # This is when Stripe applies the referral coupon discount.
        # IMPORTANT: Only process when amount > 0 — a $0 invoice with
        # billing_reason='subscription_create' is fired at trial start and
        # must NOT trigger credit deduction or referral completion.
        if billing_reason == 'subscription_create' and amount > 0:
            logger.info(f"[REFERRAL_DEBUG] First payment (subscription_create) detected for {business_id}")
            # Issue 1: Deduct credit if business used referral discount
            # Stripe already applied it as a coupon - sync the DB to reflect that
            try:
                biz_credit_result = supabase.table('businesses').select(
                    'account_credit, discount_applied'
                ).eq('id', business_id).execute()

                logger.info(f"[REFERRAL_DEBUG] Business credit data: {biz_credit_result.data}")

                if biz_credit_result.data:
                    business = biz_credit_result.data[0]
                    credit_used = float(business.get('discount_applied') or 0)
                    if credit_used > 0:
                        current_credit = float(business.get('account_credit') or 0)
                        new_credit = max(0.0, current_credit - credit_used)
                        supabase.table('businesses').update({
                            'account_credit': new_credit,
                            'discount_applied': 0,
                            'referral_credit_used': True,
                        }).eq('id', business_id).execute()
                        logger.info(
                            f"Deducted ${credit_used} referral credit from business "
                            f"{business_id} and marked referral_credit_used=true"
                        )
            except Exception as e:
                logger.error(f"Failed to deduct referral credit for {business_id}: {e}")

            # Issue 2: Complete referral so referrer earns their $40 credit
            try:
                from app.services import referral_service
                logger.info(f"[REFERRAL_DEBUG] Calling complete_referral_by_business for {business_id}")
                result = referral_service.complete_referral_by_business(business_id)
                logger.info(f"[REFERRAL_DEBUG] complete_referral_by_business result: {result}")
                if result:
                    logger.info(f"Referral completed: referrer earned $40 for business {business_id}")
                else:
                    logger.info(f"No pending referral found for {business_id}")
            except Exception as e:
                logger.error(f"Referral completion failed for {business_id}: {e}", exc_info=True)

        # Fallback: also complete referral on subscription_cycle (first charge after trial)
        elif billing_reason == 'subscription_cycle' and amount > 0:
            logger.info(f"[REFERRAL_DEBUG] subscription_cycle payment detected for {business_id}, checking referral")
            try:
                from app.services import referral_service
                logger.info(f"[REFERRAL_DEBUG] Calling complete_referral_by_business for {business_id}")
                result = referral_service.complete_referral_by_business(business_id)
                logger.info(f"[REFERRAL_DEBUG] complete_referral_by_business result: {result}")
                if result:
                    logger.info(f"Referral completed: referrer earned $40 for business {business_id} (subscription_cycle)")
                else:
                    logger.info(f"No pending referral found for {business_id} on subscription_cycle")
            except Exception as e:
                logger.error(f"Referral completion failed for {business_id}: {e}", exc_info=True)

        else:
            logger.info(f"[REFERRAL_DEBUG] No referral action: billing_reason={billing_reason} amount=${amount:.2f}")

    except Exception as e:
        logger.error(f"Failed to handle payment succeeded for business {business_id}: {e}")


# =============================================================================
# FUNCTION 9: HANDLE PAYMENT FAILED
# =============================================================================

def handle_payment_failed(invoice, business_id: str) -> None:
    """
    Handle failed payment.

    Called by the webhook handler for invoice.payment_failed events.

    Args:
        invoice: The Stripe Invoice object
        business_id: The business UUID
    """
    try:
        supabase.table('businesses').update({
            'subscription_status': 'past_due'
        }).eq('id', business_id).execute()

        _log_billing_event(business_id, 'payment_failed', {
            'invoice_id': invoice.id,
            'amount': (invoice.amount_due or 0) / 100
        })

        logger.info(f"Payment failed for business {business_id}")

        _send_payment_failed(business_id)

    except Exception as e:
        logger.error(f"Failed to handle payment failed for business {business_id}: {e}")


# =============================================================================
# PRIVATE HELPERS
# =============================================================================

def _log_billing_event(business_id: str, event_type: str, details: dict) -> None:
    """Log a billing event to the billing_events table.

    Fails gracefully if the business no longer exists — handles the race
    condition where a Stripe webhook fires after account deletion.
    """
    try:
        exists = supabase.table('businesses').select('id').eq('id', business_id).execute()
        if not exists.data:
            logger.info(f"Skipping billing event '{event_type}' for deleted business {business_id}")
            return

        supabase.table('billing_events').insert({
            'business_id': business_id,
            'event_type': event_type,
            'description': details.get('description', ''),
            'amount': details.get('amount'),
            'status': details.get('status'),
            'raw_event': details,
        }).execute()
    except Exception as e:
        logger.warning(f"Failed to log billing event '{event_type}' for business {business_id}: {e}")


def _get_business_info(business_id: str) -> dict | None:
    """Get the business owner's email and name from the database."""
    try:
        result = supabase.table('businesses').select(
            'email, business_name'
        ).eq('id', business_id).execute()
        if result.data:
            return result.data[0]
    except Exception as e:
        logger.error(f"Failed to get business info for {business_id}: {e}")
    return None


def _format_trial_end(subscription) -> str:
    """Format the trial end date from a Stripe subscription as a readable string."""
    if subscription.trial_end:
        dt = datetime.fromtimestamp(subscription.trial_end, tz=timezone.utc)
        return dt.strftime('%B %d, %Y')
    return 'the end of your trial'


def _send_trial_welcome(business_id: str, subscription) -> None:
    """Send a welcome email when a trial subscription starts."""
    try:
        biz = _get_business_info(business_id)
        if not biz or not biz.get('email'):
            return

        trial_end_date = _format_trial_end(subscription)

        send_trial_welcome_email(
            email=biz['email'],
            business_name=biz.get('business_name', 'your business'),
            trial_end_date=trial_end_date
        )
        logger.info(f"Sent trial welcome email to business {business_id}")
    except Exception as e:
        logger.error(f"Failed to send trial welcome email for business {business_id}: {e}")


def _send_payment_failed(business_id: str) -> None:
    """Send email when a payment fails."""
    try:
        biz = _get_business_info(business_id)
        if not biz or not biz.get('email'):
            return

        send_payment_failed_email(
            email=biz['email'],
            business_name=biz.get('business_name', 'your business')
        )
        logger.info(f"Sent payment failed email to business {business_id}")
    except Exception as e:
        logger.error(f"Failed to send payment failed email for business {business_id}: {e}")


def _send_cancellation(business_id: str) -> None:
    """Send email when subscription is canceled."""
    try:
        biz = _get_business_info(business_id)
        if not biz or not biz.get('email'):
            return

        send_subscription_canceled_email(
            email=biz['email'],
            business_name=biz.get('business_name', 'your business')
        )
        logger.info(f"Sent cancellation email to business {business_id}")
    except Exception as e:
        logger.error(f"Failed to send cancellation email for business {business_id}: {e}")
