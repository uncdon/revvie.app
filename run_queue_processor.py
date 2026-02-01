#!/usr/bin/env python3
"""
Queue Processor Runner - runs the review request queue processor.

This script runs as a separate background process that continuously
checks for queued review requests and sends them at the scheduled time.

USAGE:
======
    python run_queue_processor.py

    # Or with custom interval (in seconds):
    PROCESS_INTERVAL=300 python run_queue_processor.py

    # Run in background (Unix/Mac):
    nohup python run_queue_processor.py > queue_processor.log 2>&1 &

    # Check if running:
    ps aux | grep run_queue_processor

    # Stop gracefully:
    kill -SIGTERM <pid>

PRODUCTION DEPLOYMENT:
======================
Use a process manager like:
- systemd (Linux)
- supervisord
- PM2
- Docker container

Example systemd service file (/etc/systemd/system/revvie-queue.service):

    [Unit]
    Description=Revvie Queue Processor
    After=network.target

    [Service]
    User=www-data
    WorkingDirectory=/var/www/revvie
    ExecStart=/var/www/revvie/venv/bin/python run_queue_processor.py
    Restart=always
    RestartSec=10

    [Install]
    WantedBy=multi-user.target

Then:
    sudo systemctl enable revvie-queue
    sudo systemctl start revvie-queue
"""

import os
import sys
import signal
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger('queue_runner')

# Flag for graceful shutdown
shutdown_requested = False


def signal_handler(signum, frame):
    """
    Handle shutdown signals (SIGTERM, SIGINT) gracefully.

    When you press Ctrl+C or send a kill signal, this function
    sets a flag that tells the processor to stop after the
    current batch completes.
    """
    global shutdown_requested
    signal_name = signal.Signals(signum).name
    logger.info(f"\nReceived {signal_name} signal - shutting down gracefully...")
    shutdown_requested = True


def main():
    """
    Main entry point for the queue processor.
    """
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)  # kill command
    signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C

    logger.info("=" * 60)
    logger.info("REVVIE QUEUE PROCESSOR")
    logger.info("=" * 60)
    logger.info(f"Python version: {sys.version}")
    logger.info(f"Working directory: {os.getcwd()}")
    logger.info("")

    # Import here to ensure .env is loaded first
    try:
        from app.services.queue_processor import process_queued_requests, PROCESS_INTERVAL
    except ImportError as e:
        logger.error(f"Failed to import queue processor: {e}")
        logger.error("Make sure you're running from the project root directory")
        sys.exit(1)

    # Allow overriding interval via environment variable
    interval = int(os.environ.get('PROCESS_INTERVAL', PROCESS_INTERVAL))

    logger.info(f"Process interval: {interval} seconds ({interval // 60} minutes)")
    logger.info("Press Ctrl+C to stop")
    logger.info("=" * 60)
    logger.info("")

    import time

    # Main processing loop
    while not shutdown_requested:
        try:
            # Process the queue
            logger.info("Starting queue processing run...")
            result = process_queued_requests()

            # Log summary
            if result['processed'] > 0:
                logger.info(
                    f"Batch complete: {result['sent']} sent, "
                    f"{result['failed']} failed, {result['skipped']} skipped"
                )

            # Check if shutdown was requested during processing
            if shutdown_requested:
                break

            # Wait for next run
            logger.info(f"Sleeping for {interval} seconds until next run...")

            # Sleep in small increments so we can respond to signals quickly
            sleep_remaining = interval
            while sleep_remaining > 0 and not shutdown_requested:
                time.sleep(min(sleep_remaining, 5))  # Sleep max 5 seconds at a time
                sleep_remaining -= 5

        except KeyboardInterrupt:
            # This shouldn't happen since we handle SIGINT, but just in case
            logger.info("Interrupted by user")
            break
        except Exception as e:
            # Log error but don't crash - continue processing
            logger.exception(f"Error in processing loop: {e}")
            logger.info("Recovering and continuing in 60 seconds...")

            # Wait a bit before retrying
            sleep_remaining = 60
            while sleep_remaining > 0 and not shutdown_requested:
                time.sleep(min(sleep_remaining, 5))
                sleep_remaining -= 5

    logger.info("")
    logger.info("=" * 60)
    logger.info("Queue processor stopped")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
