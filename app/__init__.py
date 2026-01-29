"""
Application factory for the Flask app.
This pattern allows creating multiple instances of the app with different configurations.
"""

import os
from flask import Flask, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from config import config

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

    return app
