"""
Queue Processor Service - sends scheduled review requests via email and/or SMS.

This service runs continuously in the background, checking the queue for
review requests that are ready to be sent.

HOW IT WORKS:
=============
1. Square webhook receives payment → queues a review request with delay
2. This processor runs every 15 minutes
3. It finds all queued requests where scheduled_send_at <= NOW
4. For each one, it checks the 'method' field:
   - 'email': sends review request via email
   - 'sms': sends review request via SMS (Telnyx)
   - 'both': sends via both email AND SMS
5. Updates the queue status (sent/failed)
6. Creates a record in review_requests table for tracking

WHY A QUEUE?
============
- We don't want to spam customers immediately after payment
- Businesses can configure delay (e.g., 2 hours after purchase)
- If sending fails, we can retry later
- Provides a record of what was sent and when

RUNNING THE PROCESSOR:
======================
Option 1: Run as a separate process
    python -c "from app.services.queue_processor import run_processor_continuously; run_processor_continuously()"

Option 2: Use APScheduler in your Flask app (see integration example at bottom)

Option 3: Use a cron job
    */15 * * * * cd /path/to/revvie && source venv/bin/activate && python -c "from app.services.queue_processor import process_queued_requests; process_queued_requests()"
"""

import time
from datetime import datetime, timezone
from dotenv import load_dotenv

from app.services.supabase_service import supabase_admin as supabase  # Use admin client to bypass RLS
from app.services.email_service import send_review_request_email
from app.services.google_places import get_review_url as generate_google_review_url
from app.services.sms_service import send_review_request_sms
from app.services.square_logger import get_square_logger, log_queue_event
from app.services import link_tracker
from app.services import duplicate_checker

# Load environment variables
load_dotenv()

# Get logger from centralized Square logging
logger = get_square_logger('queue')

# How many requests to process in each batch
BATCH_SIZE = 50

# How often to run the processor (in seconds)
# 900 seconds = 15 minutes
PROCESS_INTERVAL = 900


def process_queued_requests() -> dict:
    """
    Process all queued review requests that are ready to be sent.

    This function:
    1. Queries for queued requests where scheduled_send_at <= NOW
    2. For each request, checks the 'method' field and sends accordingly
    3. Updates the queue status
    4. Records the sent request in review_requests table

    Returns:
        dict: Summary of processing results
        {
            "processed": 10,    # Total requests processed
            "sent": 8,          # Successfully sent (at least one method)
            "failed": 2,        # Failed to send
            "skipped": 0,       # Skipped (no Google Place ID, etc.)
            "errors": [...]     # List of error messages
        }

    Example:
        result = process_queued_requests()
        print(f"Sent {result['sent']} review requests")
    """
    log_queue_event('processing_started', details={'batch_size': BATCH_SIZE})

    # Track results
    results = {
        "processed": 0,
        "sent": 0,
        "failed": 0,
        "skipped": 0,
        "errors": []
    }

    try:
        # Get current time in UTC
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        # Query for queued requests that are ready to send
        logger.info(f"Queue processor running - current time: {now_iso}")

        # First, let's see how many are queued total
        total_queued_result = supabase.table('queued_review_requests').select(
            'id, scheduled_send_at', count='exact'
        ).eq(
            'status', 'queued'
        ).execute()

        total_queued = len(total_queued_result.data) if total_queued_result.data else 0
        logger.info(f"Total queued requests: {total_queued}")

        # Log some sample scheduled times for debugging
        if total_queued_result.data and total_queued > 0:
            sample = total_queued_result.data[0]
            logger.info(f"Sample queued item - scheduled_send_at: {sample.get('scheduled_send_at')}")

        # Query for queued requests that are ready to send
        query_result = supabase.table('queued_review_requests').select(
            '*'
        ).eq(
            'status', 'queued'
        ).lte(
            'scheduled_send_at', now_iso
        ).order(
            'scheduled_send_at', desc=False  # Oldest first
        ).limit(
            BATCH_SIZE
        ).execute()

        queued_requests = query_result.data or []
        logger.info(f"Requests ready to send (scheduled_send_at <= {now_iso}): {len(queued_requests)}")
        log_queue_event('batch_query', details={'found': len(queued_requests), 'total_queued': total_queued})

        if not queued_requests:
            # Extra debugging: check if there are ANY queued items and why they're not ready
            if total_queued > 0:
                logger.info(f"Found {total_queued} queued items but none ready to send yet")
                for item in total_queued_result.data[:3]:  # Log first 3
                    logger.info(f"  - ID {item['id']}: scheduled for {item.get('scheduled_send_at')}")
            return results

        # Process each request
        for req in queued_requests:
            request_id = req['id']

            results["processed"] += 1
            customer_name = req['customer_name']
            customer_email = req.get('customer_email')
            customer_phone = req.get('customer_phone')
            business_id = req['business_id']
            method = req.get('method', 'email').lower()  # Default to email for backwards compatibility

            log_queue_event('request_processing', request_id=request_id,
                          business_id=business_id, customer_email=customer_email,
                          details={'method': method})

            try:
                # Get business details
                business_result = supabase.table('businesses').select(
                    'id, business_name, google_place_id, google_review_url'
                ).eq('id', business_id).execute()

                if not business_result.data:
                    log_queue_event('request_failed', request_id=request_id,
                                  business_id=business_id, success=False,
                                  error="Business not found")
                    mark_request_failed(request_id, "Business not found")
                    results["failed"] += 1
                    results["errors"].append(f"Business not found: {business_id}")
                    continue

                business = business_result.data[0]
                business_name = business['business_name']
                google_place_id = business.get('google_place_id')
                google_review_url = business.get('google_review_url')

                logger.debug(f"Processing request for business: {business_name}")

                # Check if still safe to send (customer may have been contacted since queuing)
                dup_check = duplicate_checker.can_send_review_request(
                    business_id=business_id,
                    customer_email=customer_email,
                    customer_phone=customer_phone
                )

                if not dup_check['can_send']:
                    reason = f"Duplicate detected at send time: {dup_check.get('reason', 'recently contacted')}"
                    logger.info(
                        f"Queue processor: Skipping request {request_id} for "
                        f"{customer_email or customer_phone} - {reason}"
                    )
                    log_queue_event('request_skipped', request_id=request_id,
                                  business_id=business_id, customer_email=customer_email,
                                  details={'reason': 'duplicate_detected_at_send_time'})
                    mark_request_cancelled(request_id, reason)
                    results["skipped"] += 1
                    continue

                # Generate or use existing review URL
                if google_review_url:
                    review_url = google_review_url
                elif google_place_id:
                    review_url = generate_google_review_url(google_place_id)
                else:
                    log_queue_event('request_skipped', request_id=request_id,
                                  business_id=business_id,
                                  details={'reason': 'No Google review URL configured'})
                    mark_request_skipped(request_id, "No Google review URL configured")
                    results["skipped"] += 1
                    continue

                # Create tracking link (use short URL for click tracking)
                tracking_link = link_tracker.create_tracking_link(
                    business_id=business_id,
                    destination_url=review_url,
                    queued_request_id=req['id'],
                )
                if tracking_link:
                    send_url = tracking_link['short_url']
                else:
                    logger.warning(f"Failed to create tracking link for queued request {request_id}, using direct review URL")
                    send_url = review_url

                # Send based on method
                email_sent = False
                email_error = None
                sms_sent = False
                sms_sid = None
                sms_status = None
                sms_error = None

                # Send email if method is 'email' or 'both'
                if method in ['email', 'both'] and customer_email:
                    logger.debug(f"Sending email to {customer_email}")
                    email_result = send_review_request_email(
                        customer_name=customer_name,
                        customer_email=customer_email,
                        business_name=business_name,
                        review_url=send_url
                    )
                    email_sent = email_result['success']
                    if not email_sent:
                        email_error = email_result.get('message', 'Unknown error')
                        logger.error(f"Email failed for {customer_email}: {email_error}")

                # Send SMS if method is 'sms' or 'both'
                if method in ['sms', 'both'] and customer_phone:
                    logger.debug(f"Sending SMS to {customer_phone}")
                    sms_result = send_review_request_sms(
                        customer_name=customer_name,
                        customer_phone=customer_phone,
                        business_name=business_name,
                        review_url=send_url
                    )
                    sms_sent = sms_result['success']
                    if sms_sent:
                        sms_sid = sms_result.get('message_id')
                        sms_status = 'sent'
                    else:
                        sms_error = sms_result.get('error', 'Unknown error')
                        sms_status = 'failed'
                        logger.error(f"SMS failed for {customer_phone}: {sms_error}")

                # Determine overall success
                if method == 'email':
                    overall_success = email_sent
                elif method == 'sms':
                    overall_success = sms_sent
                else:  # both - success if at least one worked
                    overall_success = email_sent or sms_sent

                if overall_success:
                    log_queue_event('request_sent', request_id=request_id,
                                  business_id=business_id, customer_email=customer_email,
                                  details={
                                      'method': method,
                                      'email_sent': email_sent,
                                      'sms_sent': sms_sent
                                  })

                    # Mark as sent in queue with SMS tracking info
                    # CRITICAL: Verify the status update succeeded
                    if not mark_request_sent(request_id, sms_sid=sms_sid, sms_status=sms_status):
                        # Status update failed - this is critical because email was already sent
                        # but the record is still marked as 'queued', leading to potential duplicates
                        logger.error(f"CRITICAL: Email sent but status update failed for {request_id}. "
                                   f"Customer: {customer_email or customer_phone}")
                        results["errors"].append(f"Status update failed for {request_id} (email was sent)")

                    # Create record in review_requests table
                    create_review_request_record(
                        business_id=business_id,
                        customer_name=customer_name,
                        customer_email=customer_email,
                        customer_phone=customer_phone,
                        method=method,
                        sms_sid=sms_sid,
                        sms_status=sms_status,
                        sms_error=sms_error if not sms_sent else None
                    )

                    # Link tracking record to the queued request
                    if tracking_link:
                        try:
                            supabase.table('tracking_links').update(
                                {'queued_request_id': req['id']}
                            ).eq('id', tracking_link['id']).execute()
                        except Exception as e:
                            logger.warning(f"Failed to link tracking record to queued request {request_id}: {e}")

                    results["sent"] += 1
                else:
                    # Both methods failed
                    error_msg = f"Email: {email_error or 'not sent'}, SMS: {sms_error or 'not sent'}"
                    log_queue_event('request_failed', request_id=request_id,
                                  business_id=business_id, customer_email=customer_email,
                                  success=False, error=error_msg)
                    if not mark_request_failed(request_id, error_msg, sms_error=sms_error):
                        logger.error(f"Failed to update status to 'failed' for request {request_id}")
                    results["failed"] += 1
                    results["errors"].append(f"{customer_email or customer_phone}: {error_msg}")

            except Exception as e:
                log_queue_event('request_failed', request_id=request_id,
                              success=False, error=str(e))
                logger.exception(f"Exception processing request {request_id}")
                if not mark_request_failed(request_id, str(e)):
                    logger.error(f"Failed to update status to 'failed' for request {request_id}")
                results["failed"] += 1
                results["errors"].append(f"Request {request_id}: {str(e)}")

        # Log summary
        log_queue_event('processing_completed', details={
            'processed': results['processed'],
            'sent': results['sent'],
            'failed': results['failed'],
            'skipped': results['skipped']
        })

        return results

    except Exception as e:
        log_queue_event('processing_error', success=False, error=str(e))
        logger.exception("Fatal error during queue processing")
        results["errors"].append(f"Fatal error: {str(e)}")
        return results


def mark_request_sent(request_id: str, sms_sid: str = None, sms_status: str = None) -> bool:
    """
    Update a queued request status to 'sent'.

    Args:
        request_id: The ID of the queued_review_requests record
        sms_sid: Telnyx message ID (if SMS was sent)
        sms_status: SMS delivery status

    Returns:
        bool: True if update succeeded, False otherwise
    """
    try:
        update_data = {'status': 'sent'}
        if sms_sid:
            update_data['sms_sid'] = sms_sid
        if sms_status:
            update_data['sms_status'] = sms_status

        result = supabase.table('queued_review_requests').update(update_data).eq('id', request_id).eq('status', 'queued').execute()

        # Verify the update actually worked (also serves as duplicate-send prevention)
        if not result.data:
            logger.error(f"mark_request_sent: No rows updated for request {request_id} (may already be processed)")
            return False

        logger.debug(f"Successfully marked request {request_id} as sent")
        return True
    except Exception as e:
        logger.error(f"Failed to mark request {request_id} as sent: {e}")
        return False


def mark_request_failed(request_id: str, error_message: str, sms_error: str = None) -> bool:
    """
    Update a queued request status to 'failed'.

    Args:
        request_id: The ID of the queued_review_requests record
        error_message: Description of why it failed
        sms_error: SMS-specific error message

    Returns:
        bool: True if update succeeded, False otherwise
    """
    try:
        update_data = {'status': 'failed'}
        if sms_error:
            update_data['sms_status'] = 'failed'
            update_data['sms_error'] = sms_error

        result = supabase.table('queued_review_requests').update(update_data).eq('id', request_id).eq('status', 'queued').execute()

        # Verify the update actually worked
        if not result.data:
            logger.error(f"mark_request_failed: No rows updated for request {request_id} (may already be processed)")
            return False

        logger.debug(f"Successfully marked request {request_id} as failed: {error_message}")
        return True
    except Exception as e:
        logger.error(f"Failed to mark request {request_id} as failed: {e}")
        return False


def mark_request_skipped(request_id: str, reason: str) -> None:
    """
    Update a queued request status to 'failed' when skipped.

    We mark skipped requests as 'failed' so they don't get reprocessed.

    Args:
        request_id: The ID of the queued_review_requests record
        reason: Why the request was skipped
    """
    logger.info(f"Skipping request {request_id}: {reason}")
    mark_request_failed(request_id, reason)


def mark_request_cancelled(request_id: str, reason: str) -> bool:
    """
    Update a queued request status to 'cancelled' when duplicate detected at send time.

    Args:
        request_id: The ID of the queued_review_requests record
        reason: Why the request was cancelled

    Returns:
        bool: True if update succeeded, False otherwise
    """
    try:
        result = supabase.table('queued_review_requests').update(
            {'status': 'cancelled'}
        ).eq('id', request_id).eq('status', 'queued').execute()

        if not result.data:
            logger.error(f"mark_request_cancelled: No rows updated for request {request_id}")
            return False

        logger.info(f"Cancelled request {request_id}: {reason}")
        return True
    except Exception as e:
        logger.error(f"Failed to mark request {request_id} as cancelled: {e}")
        return False


def create_review_request_record(
    business_id: str,
    customer_name: str,
    customer_email: str = None,
    customer_phone: str = None,
    method: str = 'email',
    sms_sid: str = None,
    sms_status: str = None,
    sms_error: str = None
) -> None:
    """
    Create a record in the review_requests table after sending.

    This is for tracking/analytics purposes - how many review requests
    were sent, click rates, etc.

    Args:
        business_id: The business that sent the request
        customer_name: Customer's name
        customer_email: Customer's email (if sent via email)
        customer_phone: Customer's phone (if sent via SMS)
        method: How the request was sent ('email', 'sms', or 'both')
        sms_sid: Telnyx message ID for tracking
        sms_status: SMS delivery status
        sms_error: SMS error message if failed
    """
    try:
        record = {
            'business_id': business_id,
            'customer_name': customer_name,
            'customer_email': customer_email,
            'customer_phone': customer_phone,
            'status': 'sent',
            'method': method,
            'sent_at': datetime.now(timezone.utc).isoformat(),
            'sms_sid': sms_sid,
            'sms_status': sms_status,
            'sms_error': sms_error
        }

        supabase.table('review_requests').insert(record).execute()
        logger.info(f"Created review_requests record for {customer_email or customer_phone}")
    except Exception as e:
        # Don't fail the whole process if this record fails
        logger.error(f"Failed to create review_requests record: {e}")


def diagnose_stuck_items() -> dict:
    """
    Diagnose items that might be stuck in 'queued' status.

    This function helps debug issues where items remain queued despite
    being past their scheduled send time.

    Returns:
        dict: Diagnostic information about stuck items
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    results = {
        "current_time_utc": now_iso,
        "total_queued": 0,
        "ready_to_send": 0,
        "not_yet_ready": 0,
        "stuck_items": [],
        "sample_items": []
    }

    try:
        # Get all queued items
        queued_result = supabase.table('queued_review_requests').select(
            'id, customer_email, customer_phone, scheduled_send_at, created_at, status'
        ).eq(
            'status', 'queued'
        ).order(
            'scheduled_send_at', desc=False
        ).limit(100).execute()

        queued_items = queued_result.data or []
        results["total_queued"] = len(queued_items)

        for item in queued_items:
            scheduled_at = item.get('scheduled_send_at')
            is_ready = scheduled_at and scheduled_at <= now_iso

            if is_ready:
                results["ready_to_send"] += 1
                # These are potentially stuck - should have been processed
                results["stuck_items"].append({
                    "id": item['id'],
                    "customer": item.get('customer_email') or item.get('customer_phone'),
                    "scheduled_send_at": scheduled_at,
                    "created_at": item.get('created_at'),
                    "minutes_overdue": round((now - datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))).total_seconds() / 60, 1) if scheduled_at else None
                })
            else:
                results["not_yet_ready"] += 1
                if len(results["sample_items"]) < 5:
                    results["sample_items"].append({
                        "id": item['id'],
                        "customer": item.get('customer_email') or item.get('customer_phone'),
                        "scheduled_send_at": scheduled_at
                    })

        logger.info(f"Diagnosis: {results['total_queued']} queued, {results['ready_to_send']} ready, {results['not_yet_ready']} pending")

    except Exception as e:
        logger.error(f"Error during diagnosis: {e}")
        results["error"] = str(e)

    return results


def run_processor_continuously(interval: int = PROCESS_INTERVAL) -> None:
    """
    Run the queue processor continuously in a loop.

    This function runs forever, processing the queue every `interval` seconds.
    It's designed to be run as a background process or in a separate thread.

    Args:
        interval: Seconds between processing runs (default: 900 = 15 minutes)

    Usage:
        # Run in terminal:
        python -c "from app.services.queue_processor import run_processor_continuously; run_processor_continuously()"

        # Or with custom interval (5 minutes):
        python -c "from app.services.queue_processor import run_processor_continuously; run_processor_continuously(300)"
    """
    logger.info("=" * 60)
    logger.info("QUEUE PROCESSOR: Starting continuous processing")
    logger.info(f"Interval: {interval} seconds ({interval // 60} minutes)")
    logger.info("Press Ctrl+C to stop")
    logger.info("=" * 60 + "\n")

    while True:
        try:
            # Process the queue
            process_queued_requests()

            # Wait for next run
            logger.info(f"Next run in {interval // 60} minutes...")
            time.sleep(interval)

        except KeyboardInterrupt:
            logger.info("\nQueue processor stopped by user")
            break
        except Exception as e:
            # If something crashes, log it and continue
            logger.exception(f"Error in processor loop: {e}")
            logger.info("Recovering and continuing...")
            time.sleep(60)  # Wait a minute before retrying


def run_processor_with_scheduler() -> None:
    """
    Run the queue processor using APScheduler.

    APScheduler is more robust than a simple while loop:
    - Handles missed jobs (if server was down)
    - Can be integrated into Flask app
    - Supports multiple job types (interval, cron, etc.)

    This is the recommended approach for production.
    """
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger

        # Create scheduler
        scheduler = BackgroundScheduler()

        # Add job to run every 15 minutes
        scheduler.add_job(
            func=process_queued_requests,
            trigger=IntervalTrigger(minutes=15),
            id='queue_processor',
            name='Process queued review requests',
            replace_existing=True,
            next_run_time=datetime.now(timezone.utc)
        )

        # Start the scheduler
        scheduler.start()
        logger.info("APScheduler started - queue processor running every 15 minutes")

        # Keep the main thread alive
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down scheduler...")
            scheduler.shutdown()

    except ImportError:
        logger.error("APScheduler not installed. Run: pip install APScheduler")
        logger.info("Falling back to simple loop...")
        run_processor_continuously()


if __name__ == '__main__':
    # When run directly, start the continuous processor
    run_processor_continuously()
