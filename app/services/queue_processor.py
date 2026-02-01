"""
Queue Processor Service - sends scheduled review requests.

This service runs continuously in the background, checking the queue for
review requests that are ready to be sent.

HOW IT WORKS:
=============
1. Square webhook receives payment → queues a review request with delay
2. This processor runs every 15 minutes
3. It finds all queued requests where scheduled_send_at <= NOW
4. For each one, it sends the review request email
5. Updates the queue status (sent/failed)
6. Creates a record in review_requests table for tracking

WHY A QUEUE?
============
- We don't want to spam customers immediately after payment
- Businesses can configure delay (e.g., 2 hours after purchase)
- If email fails, we can retry later
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
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv

from app.services.supabase_service import supabase
from app.services.email_service import send_review_request_email, generate_google_review_url

# Load environment variables
load_dotenv()

# Configure logging
# This creates logs that help debug issues in production
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Log to console
        # Uncomment below to also log to file:
        # logging.FileHandler('queue_processor.log'),
    ]
)
logger = logging.getLogger('queue_processor')

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
    2. For each request, sends the review email
    3. Updates the queue status
    4. Records the sent request in review_requests table

    Returns:
        dict: Summary of processing results
        {
            "processed": 10,    # Total requests processed
            "sent": 8,          # Successfully sent
            "failed": 2,        # Failed to send
            "skipped": 0,       # Skipped (no Google Place ID, etc.)
            "errors": [...]     # List of error messages
        }

    Example:
        result = process_queued_requests()
        print(f"Sent {result['sent']} review requests")
    """
    logger.info("=" * 60)
    logger.info("QUEUE PROCESSOR: Starting batch processing")
    logger.info("=" * 60)

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
        # scheduled_send_at <= NOW means "should have been sent by now"
        logger.info(f"Querying for requests ready to send (before {now})")

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
        logger.info(f"Found {len(queued_requests)} requests to process")

        if not queued_requests:
            logger.info("No queued requests ready to send")
            return results

        # Process each request
        for request in queued_requests:
            results["processed"] += 1
            request_id = request['id']
            customer_name = request['customer_name']
            customer_email = request['customer_email']
            business_id = request['business_id']

            logger.info(f"\nProcessing request {request_id}")
            logger.info(f"  Customer: {customer_name} ({customer_email})")
            logger.info(f"  Business ID: {business_id}")

            try:
                # Get business details
                business_result = supabase.table('businesses').select(
                    'id, business_name, google_place_id, google_review_url'
                ).eq('id', business_id).execute()

                if not business_result.data:
                    logger.warning(f"  Business not found: {business_id}")
                    mark_request_failed(request_id, "Business not found")
                    results["failed"] += 1
                    results["errors"].append(f"Business not found: {business_id}")
                    continue

                business = business_result.data[0]
                business_name = business['business_name']
                google_place_id = business.get('google_place_id')
                google_review_url = business.get('google_review_url')

                logger.info(f"  Business: {business_name}")

                # Generate or use existing review URL
                if google_review_url:
                    review_url = google_review_url
                elif google_place_id:
                    review_url = generate_google_review_url(google_place_id)
                else:
                    logger.warning(f"  No Google Place ID or review URL for business")
                    mark_request_skipped(request_id, "No Google review URL configured")
                    results["skipped"] += 1
                    continue

                logger.info(f"  Review URL: {review_url[:50]}...")

                # Send the review request email
                logger.info(f"  Sending email to {customer_email}...")
                email_result = send_review_request_email(
                    customer_name=customer_name,
                    customer_email=customer_email,
                    business_name=business_name,
                    review_url=review_url
                )

                if email_result['success']:
                    logger.info(f"  ✓ Email sent successfully!")

                    # Mark as sent in queue
                    mark_request_sent(request_id)

                    # Create record in review_requests table
                    create_review_request_record(
                        business_id=business_id,
                        customer_name=customer_name,
                        customer_email=customer_email,
                        method='email'
                    )

                    results["sent"] += 1
                else:
                    logger.error(f"  ✗ Email failed: {email_result['message']}")
                    mark_request_failed(request_id, email_result['message'])
                    results["failed"] += 1
                    results["errors"].append(f"{customer_email}: {email_result['message']}")

            except Exception as e:
                logger.exception(f"  ✗ Exception processing request {request_id}")
                mark_request_failed(request_id, str(e))
                results["failed"] += 1
                results["errors"].append(f"Request {request_id}: {str(e)}")

        # Log summary
        logger.info("\n" + "=" * 60)
        logger.info("QUEUE PROCESSOR: Batch complete")
        logger.info(f"  Processed: {results['processed']}")
        logger.info(f"  Sent:      {results['sent']}")
        logger.info(f"  Failed:    {results['failed']}")
        logger.info(f"  Skipped:   {results['skipped']}")
        logger.info("=" * 60 + "\n")

        return results

    except Exception as e:
        logger.exception("QUEUE PROCESSOR: Fatal error during processing")
        results["errors"].append(f"Fatal error: {str(e)}")
        return results


def mark_request_sent(request_id: str) -> None:
    """
    Update a queued request status to 'sent'.

    Args:
        request_id: The ID of the queued_review_requests record
    """
    try:
        supabase.table('queued_review_requests').update({
            'status': 'sent'
        }).eq('id', request_id).execute()
    except Exception as e:
        logger.error(f"Failed to mark request {request_id} as sent: {e}")


def mark_request_failed(request_id: str, error_message: str) -> None:
    """
    Update a queued request status to 'failed'.

    Args:
        request_id: The ID of the queued_review_requests record
        error_message: Description of why it failed
    """
    try:
        supabase.table('queued_review_requests').update({
            'status': 'failed'
            # Note: You could add an 'error_message' column to track failures
        }).eq('id', request_id).execute()
    except Exception as e:
        logger.error(f"Failed to mark request {request_id} as failed: {e}")


def mark_request_skipped(request_id: str, reason: str) -> None:
    """
    Update a queued request status to 'failed' when skipped.

    We mark skipped requests as 'failed' so they don't get reprocessed.
    In a more sophisticated system, you might have a 'skipped' status.

    Args:
        request_id: The ID of the queued_review_requests record
        reason: Why the request was skipped
    """
    logger.info(f"  Skipping request {request_id}: {reason}")
    mark_request_failed(request_id, reason)


def create_review_request_record(
    business_id: str,
    customer_name: str,
    customer_email: str,
    method: str = 'email'
) -> None:
    """
    Create a record in the review_requests table after sending.

    This is for tracking/analytics purposes - how many review requests
    were sent, click rates, etc.

    Args:
        business_id: The business that sent the request
        customer_name: Customer's name
        customer_email: Customer's email
        method: How the request was sent ('email' or 'sms')
    """
    try:
        supabase.table('review_requests').insert({
            'business_id': business_id,
            'customer_name': customer_name,
            'customer_email': customer_email,
            'status': 'sent',
            'method': method,
            'sent_at': datetime.now(timezone.utc).isoformat()
        }).execute()
        logger.info(f"  Created review_requests record for {customer_email}")
    except Exception as e:
        # Don't fail the whole process if this record fails
        logger.error(f"  Failed to create review_requests record: {e}")


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
            # (don't let one error stop the whole processor)
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

    Usage:
        from app.services.queue_processor import run_processor_with_scheduler
        run_processor_with_scheduler()
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
            # Run immediately on startup too
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


# ============================================================================
# FLASK INTEGRATION EXAMPLE
# ============================================================================
#
# To integrate the scheduler into your Flask app, add this to app/__init__.py:
#
#   def create_app(config_name='default'):
#       app = Flask(__name__)
#       ...
#
#       # Start queue processor in background (only in production)
#       if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
#           from app.services.queue_processor import start_scheduler
#           start_scheduler(app)
#
#       return app
#
# And add this function to this file:
#
#   def start_scheduler(app):
#       from apscheduler.schedulers.background import BackgroundScheduler
#       scheduler = BackgroundScheduler()
#       scheduler.add_job(
#           func=process_queued_requests,
#           trigger='interval',
#           minutes=15,
#           id='queue_processor'
#       )
#       scheduler.start()


# ============================================================================
# TESTING
# ============================================================================
#
# To test the processor manually:
#
#   # First, add a test record to the queue
#   from app.services.supabase_service import supabase
#   from datetime import datetime, timezone
#
#   supabase.table('queued_review_requests').insert({
#       'business_id': 'YOUR_BUSINESS_ID',
#       'customer_name': 'Test Customer',
#       'customer_email': 'your-email@example.com',
#       'scheduled_send_at': datetime.now(timezone.utc).isoformat(),
#       'status': 'queued',
#       'integration_source': 'manual',
#       'payment_id': 'test-payment-001'
#   }).execute()
#
#   # Then run the processor
#   from app.services.queue_processor import process_queued_requests
#   result = process_queued_requests()
#   print(result)


if __name__ == '__main__':
    # When run directly, start the continuous processor
    run_processor_continuously()
