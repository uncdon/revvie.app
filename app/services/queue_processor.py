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

import os
import time
from datetime import datetime, timezone
from dotenv import load_dotenv

from app.services.supabase_service import supabase
from app.services.email_service import send_review_request_email, generate_google_review_url
from app.services.sms_service import send_review_request_sms
from app.services.square_logger import get_square_logger, log_queue_event

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
        now = datetime.now(timezone.utc).isoformat()

        # Query for queued requests that are ready to send
        logger.debug(f"Querying for requests ready to send (before {now})")

        query_result = supabase.table('queued_review_requests').select(
            '*'
        ).eq(
            'status', 'queued'
        ).lte(
            'scheduled_send_at', now
        ).order(
            'scheduled_send_at', desc=False  # Oldest first
        ).limit(
            BATCH_SIZE
        ).execute()

        queued_requests = query_result.data or []
        log_queue_event('batch_query', details={'found': len(queued_requests)})

        if not queued_requests:
            logger.info("No queued requests ready to send")
            return results

        # Process each request
        for req in queued_requests:
            results["processed"] += 1
            request_id = req['id']
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
                        review_url=review_url
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
                        review_url=review_url
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
                    mark_request_sent(request_id, sms_sid=sms_sid, sms_status=sms_status)

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

                    results["sent"] += 1
                else:
                    # Both methods failed
                    error_msg = f"Email: {email_error or 'not sent'}, SMS: {sms_error or 'not sent'}"
                    log_queue_event('request_failed', request_id=request_id,
                                  business_id=business_id, customer_email=customer_email,
                                  success=False, error=error_msg)
                    mark_request_failed(request_id, error_msg, sms_error=sms_error)
                    results["failed"] += 1
                    results["errors"].append(f"{customer_email or customer_phone}: {error_msg}")

            except Exception as e:
                log_queue_event('request_failed', request_id=request_id,
                              success=False, error=str(e))
                logger.exception(f"Exception processing request {request_id}")
                mark_request_failed(request_id, str(e))
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


def mark_request_sent(request_id: str, sms_sid: str = None, sms_status: str = None) -> None:
    """
    Update a queued request status to 'sent'.

    Args:
        request_id: The ID of the queued_review_requests record
        sms_sid: Telnyx message ID (if SMS was sent)
        sms_status: SMS delivery status
    """
    try:
        update_data = {'status': 'sent'}
        if sms_sid:
            update_data['sms_sid'] = sms_sid
        if sms_status:
            update_data['sms_status'] = sms_status

        supabase.table('queued_review_requests').update(update_data).eq('id', request_id).execute()
    except Exception as e:
        logger.error(f"Failed to mark request {request_id} as sent: {e}")


def mark_request_failed(request_id: str, error_message: str, sms_error: str = None) -> None:
    """
    Update a queued request status to 'failed'.

    Args:
        request_id: The ID of the queued_review_requests record
        error_message: Description of why it failed
        sms_error: SMS-specific error message
    """
    try:
        update_data = {'status': 'failed'}
        if sms_error:
            update_data['sms_status'] = 'failed'
            update_data['sms_error'] = sms_error

        supabase.table('queued_review_requests').update(update_data).eq('id', request_id).execute()
    except Exception as e:
        logger.error(f"Failed to mark request {request_id} as failed: {e}")


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
            next_run_time=datetime.now()
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
