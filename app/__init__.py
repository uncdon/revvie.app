"""
Application factory for the Flask app.
This pattern allows creating multiple instances of the app with different configurations.
"""

import os
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from flask import Flask, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from config import config

# Initialize Sentry for error tracking
sentry_sdk.init(
    dsn="https://703fca9893ad4d814c03ffad9b035bc0@o4510814684184576.ingest.us.sentry.io/4510814685364224",
    integrations=[FlaskIntegration()],
    traces_sample_rate=1.0
)

# Initialize extensions (without binding to app yet)
db = SQLAlchemy()

# Path to frontend folder
FRONTEND_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend')


def create_app(config_name='default'):
    """
    Create and configure the Flask application.

    Args:
        config_name: The configuration to use ('development', 'production', 'testing')

    Returns:
        The configured Flask application instance
    """
    app = Flask(__name__, static_folder=FRONTEND_FOLDER)

    # Load configuration
    app.config.from_object(config[config_name])

    # Initialize extensions with the app
    db.init_app(app)
    CORS(app)  # Enable Cross-Origin Resource Sharing for API access

    # Register blueprints (route modules)
    from app.routes.health import health_bp
    from app.routes.reviews import reviews_bp
    from app.routes.test import test_bp
    from app.routes.auth import auth_bp
    from app.routes.businesses import businesses_bp
    from app.routes.frontend import frontend_bp
    from app.routes.sms import sms_bp
    from app.routes.email import email_bp
    from app.routes.review_requests import review_requests_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.customers import customers_bp
    from app.routes.csv_import import csv_import_bp
    from app.routes.square_integration import square_bp as square_integration_bp
    from app.routes.square_webhooks import square_webhooks_bp
    from app.routes.square_logs import square_logs_bp
    from app.routes.telnyx_webhooks import telnyx_webhooks_bp
    from app.routes.places import places_bp
    from app.routes.analytics import analytics_bp
    from app.routes.link_redirect import link_redirect_bp
    from app.routes.billing import billing_bp, billing_pages_bp
    from app.routes.integrations import integrations_bp
    app.register_blueprint(link_redirect_bp)  # No prefix - /r/<code> at root
    app.register_blueprint(health_bp, url_prefix='/api')
    app.register_blueprint(reviews_bp, url_prefix='/api')
    app.register_blueprint(test_bp, url_prefix='/api')
    app.register_blueprint(auth_bp, url_prefix='/api')
    app.register_blueprint(businesses_bp, url_prefix='/api')
    app.register_blueprint(sms_bp, url_prefix='/api')
    app.register_blueprint(email_bp, url_prefix='/api')
    app.register_blueprint(review_requests_bp, url_prefix='/api')
    app.register_blueprint(dashboard_bp, url_prefix='/api')
    app.register_blueprint(customers_bp, url_prefix='/api')
    app.register_blueprint(csv_import_bp, url_prefix='/api/customers')
    app.register_blueprint(frontend_bp)  # No prefix - serves at root

    # Square Integration routes
    app.register_blueprint(square_integration_bp, url_prefix='/api/integrations/square')
    app.register_blueprint(square_webhooks_bp, url_prefix='/webhooks')
    app.register_blueprint(square_logs_bp, url_prefix='/api/integrations/square')

    # Google Places API
    app.register_blueprint(places_bp, url_prefix='/api')

    # Analytics
    app.register_blueprint(analytics_bp, url_prefix='/api/analytics')

    # Telnyx SMS webhook routes
    app.register_blueprint(telnyx_webhooks_bp, url_prefix='/webhooks')

    # Billing / Stripe
    app.register_blueprint(billing_bp, url_prefix='/api')
    app.register_blueprint(billing_pages_bp)  # No prefix - /billing/success and /billing/canceled

    # General integrations (waitlist, etc.)
    app.register_blueprint(integrations_bp, url_prefix='/api')

    # Referrals
    from app.routes.referrals import referrals_bp
    app.register_blueprint(referrals_bp, url_prefix='/api/referrals')

    # Admin
    from app.routes.admin import admin_bp
    app.register_blueprint(admin_bp, url_prefix='/api/admin')

    # Start the queue processor scheduler (for processing delayed review requests)
    start_queue_scheduler(app)

    return app


def start_queue_scheduler(app):
    """Start the background scheduler for processing queued review requests."""
    import logging

    logger = logging.getLogger(__name__)

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        from app.services.queue_processor import process_queued_requests
        from datetime import datetime, timezone

        scheduler = BackgroundScheduler()

        # Run every 5 minutes
        scheduler.add_job(
            func=process_queued_requests,
            trigger=IntervalTrigger(minutes=5),
            id='queue_processor',
            name='Process queued review requests',
            replace_existing=True,
            next_run_time=datetime.now(timezone.utc)  # Run immediately on startup (timezone-aware)
        )

        scheduler.start()
        logger.info("Queue processor scheduler started - running every 5 minutes")

    except ImportError:
        logger.warning("APScheduler not installed - queue processor won't run automatically")
    except Exception as e:
        logger.error(f"Failed to start queue scheduler: {e}")
