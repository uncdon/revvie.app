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
    from app.routes.square_integration import square_bp as square_integration_bp
    from app.routes.square_webhooks import square_webhooks_bp
    from app.routes.square_logs import square_logs_bp
    from app.routes.telnyx_webhooks import telnyx_webhooks_bp
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
    app.register_blueprint(frontend_bp)  # No prefix - serves at root

    # Square Integration routes
    app.register_blueprint(square_integration_bp, url_prefix='/api/integrations/square')
    app.register_blueprint(square_webhooks_bp, url_prefix='/webhooks')
    app.register_blueprint(square_logs_bp, url_prefix='/api/integrations/square')

    # Telnyx SMS webhook routes
    app.register_blueprint(telnyx_webhooks_bp, url_prefix='/webhooks')

    return app
