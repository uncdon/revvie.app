"""
Production entry point for the Flask application.
This file is used by gunicorn: gunicorn main:app
"""

import os
from dotenv import load_dotenv

# Load environment variables before importing app
load_dotenv()

from app import create_app

# Create the Flask app instance
# This must be called 'app' for gunicorn to find it
# Default to 'production' since this is the gunicorn entry point
config_name = os.environ.get('FLASK_ENV', 'production')
app = create_app(config_name)

if __name__ == '__main__':
    # For local development, use PORT from environment or default to 5001
    port = int(os.environ.get('PORT', 5001))
    debug = os.environ.get('FLASK_ENV') == 'development'

    app.run(host='0.0.0.0', port=port, debug=debug)
