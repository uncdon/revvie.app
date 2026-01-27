"""
Application factory for the Flask app.
This pattern allows creating multiple instances of the app with different configurations.
"""

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from config import config

# Initialize extensions (without binding to app yet)
db = SQLAlchemy()


def create_app(config_name='default'):
    """
    Create and configure the Flask application.

    Args:
        config_name: The configuration to use ('development', 'production', 'testing')

    Returns:
        The configured Flask application instance
    """
    app = Flask(__name__)

    # Load configuration
    app.config.from_object(config[config_name])

    # Initialize extensions with the app
    db.init_app(app)
    CORS(app)  # Enable Cross-Origin Resource Sharing for API access

    # Register blueprints (route modules)
    from app.routes.health import health_bp
    from app.routes.reviews import reviews_bp
    from app.routes.test import test_bp
    app.register_blueprint(health_bp, url_prefix='/api')
    app.register_blueprint(reviews_bp, url_prefix='/api')
    app.register_blueprint(test_bp, url_prefix='/api')

    return app
