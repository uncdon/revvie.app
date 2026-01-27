"""
Health check endpoint for monitoring the API status.
Used by load balancers, container orchestration, and monitoring tools.
"""

from flask import Blueprint, jsonify

# Create a Blueprint - a way to organize related routes
health_bp = Blueprint('health', __name__)


@health_bp.route('/health', methods=['GET'])
def health_check():
    """
    Health check endpoint.

    Returns:
        JSON response with status "ok" and HTTP 200
    """
    return jsonify({'status': 'ok'}), 200
