"""
Entry point to run the Flask application.
Run this file to start the development server.
"""

import os
from app import create_app, db

# Get configuration from environment variable, default to 'development'
config_name = os.environ.get('FLASK_ENV') or 'development'
app = create_app(config_name)


if __name__ == '__main__':
    # Create database tables if they don't exist
    with app.app_context():
        db.create_all()

    # Run the development server
    # host='0.0.0.0' makes it accessible from other machines
    # debug=True enables auto-reload and detailed error pages
    app.run(host='0.0.0.0', port=5001, debug=True)
