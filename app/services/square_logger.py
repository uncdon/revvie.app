"""
Square Integration Logger - centralized logging for all Square-related operations.

This module provides a pre-configured logger for the Square integration with:
- Daily rotating log files stored in logs/square_integration.log
- Console output for development
- Consistent formatting across all Square modules

Log levels used:
- INFO: Normal operations (OAuth connected, webhook received, email sent)
- WARNING: Recoverable issues (token refresh needed, missing customer email)
- ERROR: Failed operations (API errors, token expired, network issues)

Usage:
    from app.services.square_logger import get_square_logger

    logger = get_square_logger('oauth')      # For OAuth operations
    logger = get_square_logger('webhooks')   # For webhook processing
    logger = get_square_logger('queue')      # For queue processing
    logger = get_square_logger('api')        # For Square API calls

    logger.info("User connected Square account")
    logger.warning("Token expires in 24 hours, refresh needed")
    logger.error("Failed to exchange OAuth code", exc_info=True)
"""

import os
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime

# Create logs directory if it doesn't exist
LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'logs')
os.makedirs(LOGS_DIR, exist_ok=True)

# Log file path
LOG_FILE = os.path.join(LOGS_DIR, 'square_integration.log')

# Flag to track if handlers have been set up
_handlers_configured = False


def setup_square_logging():
    """
    Set up the logging configuration for Square integration.

    This creates:
    - A rotating file handler that rotates daily and keeps 30 days of logs
    - A console handler for development visibility

    Called automatically when get_square_logger() is first used.
    """
    global _handlers_configured

    if _handlers_configured:
        return

    # Create the root Square logger
    square_root = logging.getLogger('square')
    square_root.setLevel(logging.DEBUG)  # Capture all levels, handlers filter

    # Prevent propagation to root logger (avoids duplicate logs)
    square_root.propagate = False

    # Log format with timestamp, module, level, and message
    log_format = logging.Formatter(
        '%(asctime)s | %(name)s | %(levelname)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # File handler with daily rotation
    # - Rotates at midnight
    # - Keeps 30 days of logs
    # - Creates files like: square_integration.log.2024-01-15
    file_handler = TimedRotatingFileHandler(
        LOG_FILE,
        when='midnight',
        interval=1,
        backupCount=30,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(log_format)
    file_handler.suffix = '%Y-%m-%d'

    # Console handler for development
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(log_format)

    # Add handlers to root Square logger
    square_root.addHandler(file_handler)
    square_root.addHandler(console_handler)

    _handlers_configured = True


def get_square_logger(module_name: str) -> logging.Logger:
    """
    Get a logger for a specific Square integration module.

    Args:
        module_name: The module name (e.g., 'oauth', 'webhooks', 'queue', 'api')

    Returns:
        A configured logger instance

    Example:
        logger = get_square_logger('oauth')
        logger.info("OAuth flow started", extra={'business_id': 'abc123'})
    """
    # Ensure logging is set up
    setup_square_logging()

    # Return a child logger under the square namespace
    return logging.getLogger(f'square.{module_name}')


class SquareLogContext:
    """
    Context manager for adding extra context to log messages.

    Usage:
        with SquareLogContext(logger, business_id='abc123', event='oauth'):
            logger.info("Processing started")
            # All logs in this block will include the context
    """

    def __init__(self, logger: logging.Logger, **context):
        self.logger = logger
        self.context = context
        self.old_factory = None

    def __enter__(self):
        # Store context for access in log messages
        self.old_factory = logging.getLogRecordFactory()
        context = self.context

        def record_factory(*args, **kwargs):
            record = self.old_factory(*args, **kwargs)
            for key, value in context.items():
                setattr(record, key, value)
            return record

        logging.setLogRecordFactory(record_factory)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        logging.setLogRecordFactory(self.old_factory)
        return False


def log_oauth_event(event_type: str, business_id: str = None, success: bool = True,
                    error: str = None, details: dict = None):
    """
    Log an OAuth-related event with structured data.

    Args:
        event_type: Type of event (connect_started, connect_completed,
                    callback_received, token_exchange, token_refresh, disconnect)
        business_id: The business ID involved
        success: Whether the operation succeeded
        error: Error message if failed
        details: Additional details to log
    """
    logger = get_square_logger('oauth')

    message_parts = [f"[{event_type.upper()}]"]
    if business_id:
        message_parts.append(f"business_id={business_id}")

    if details:
        for key, value in details.items():
            if value is not None:
                message_parts.append(f"{key}={value}")

    message = " | ".join(message_parts)

    if success:
        logger.info(message)
    else:
        if error:
            message += f" | error={error}"
        logger.error(message)


def log_webhook_event(event_type: str, event_id: str = None, payment_id: str = None,
                      business_id: str = None, success: bool = True,
                      error: str = None, details: dict = None):
    """
    Log a webhook-related event with structured data.

    Args:
        event_type: Type of event (received, signature_verified, processed,
                    queued, skipped, error)
        event_id: Square event ID
        payment_id: Payment ID from webhook
        business_id: The business ID involved
        success: Whether the operation succeeded
        error: Error message if failed
        details: Additional details to log
    """
    logger = get_square_logger('webhooks')

    message_parts = [f"[{event_type.upper()}]"]
    if event_id:
        message_parts.append(f"event_id={event_id}")
    if payment_id:
        message_parts.append(f"payment_id={payment_id}")
    if business_id:
        message_parts.append(f"business_id={business_id}")

    if details:
        for key, value in details.items():
            if value is not None:
                message_parts.append(f"{key}={value}")

    message = " | ".join(message_parts)

    if success:
        logger.info(message)
    else:
        if error:
            message += f" | error={error}"
        logger.error(message)


def log_queue_event(event_type: str, request_id: str = None, business_id: str = None,
                    customer_email: str = None, success: bool = True,
                    error: str = None, details: dict = None):
    """
    Log a queue processing event with structured data.

    Args:
        event_type: Type of event (processing_started, processing_completed,
                    request_sent, request_failed, request_skipped, batch_summary)
        request_id: Queue request ID
        business_id: The business ID involved
        customer_email: Customer email for the request
        success: Whether the operation succeeded
        error: Error message if failed
        details: Additional details to log
    """
    logger = get_square_logger('queue')

    message_parts = [f"[{event_type.upper()}]"]
    if request_id:
        message_parts.append(f"request_id={request_id}")
    if business_id:
        message_parts.append(f"business_id={business_id}")
    if customer_email:
        # Mask email for privacy in logs
        parts = customer_email.split('@')
        if len(parts) == 2:
            masked = parts[0][:2] + '***@' + parts[1]
            message_parts.append(f"email={masked}")

    if details:
        for key, value in details.items():
            if value is not None:
                message_parts.append(f"{key}={value}")

    message = " | ".join(message_parts)

    if success:
        logger.info(message)
    else:
        if error:
            message += f" | error={error}"
        logger.error(message)


def log_api_event(operation: str, success: bool = True, error: str = None,
                  error_type: str = None, details: dict = None):
    """
    Log a Square API call event.

    Args:
        operation: The API operation (get_merchant, get_customer, get_payment,
                   token_exchange, token_refresh)
        success: Whether the API call succeeded
        error: Error message if failed
        error_type: Type of error (network, auth, rate_limit, validation)
        details: Additional details to log
    """
    logger = get_square_logger('api')

    message_parts = [f"[{operation.upper()}]"]

    if details:
        for key, value in details.items():
            if value is not None:
                # Don't log sensitive values
                if 'token' in key.lower() or 'secret' in key.lower():
                    continue
                message_parts.append(f"{key}={value}")

    message = " | ".join(message_parts)

    if success:
        logger.info(message)
    else:
        if error_type:
            message += f" | error_type={error_type}"
        if error:
            message += f" | error={error}"
        logger.error(message)


def get_recent_logs(lines: int = 100, level: str = None) -> list:
    """
    Get recent log entries from the log file.

    Args:
        lines: Number of recent lines to return (default 100)
        level: Filter by log level (INFO, WARNING, ERROR)

    Returns:
        List of log entries as dictionaries
    """
    entries = []

    if not os.path.exists(LOG_FILE):
        return entries

    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()

        # Get the last N lines
        recent_lines = all_lines[-lines:]

        for line in recent_lines:
            line = line.strip()
            if not line:
                continue

            # Parse the log line
            try:
                parts = line.split(' | ', 3)
                if len(parts) >= 4:
                    entry = {
                        'timestamp': parts[0],
                        'module': parts[1],
                        'level': parts[2],
                        'message': parts[3]
                    }

                    # Filter by level if specified
                    if level and entry['level'] != level:
                        continue

                    entries.append(entry)
            except Exception:
                # If parsing fails, include raw line
                entries.append({
                    'timestamp': '',
                    'module': '',
                    'level': 'UNKNOWN',
                    'message': line
                })

        return entries

    except Exception as e:
        return [{'error': str(e)}]
