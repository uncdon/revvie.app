"""
Email API endpoints.

This file defines the HTTP routes (URLs) for email functionality.
Think of routes as "doors" into your application - when someone visits
a URL or makes an API request, the route handles it.

Endpoints:
- POST /api/email/send - Send an email (requires authentication)
"""

from flask import Blueprint, jsonify, request
from app.services.auth_service import require_auth
from app.services.email_service import send_email

# Create a Blueprint - this groups related routes together
# 'email' is the name, used internally by Flask
email_bp = Blueprint('email', __name__)


@email_bp.route('/email/send', methods=['POST'])
@require_auth  # This decorator ensures only logged-in users can access this endpoint
def send_email_route():
    """
    Send an email via SendGrid.

    This endpoint requires authentication - you must include a valid JWT token
    in the Authorization header.

    Request:
        Headers:
            Authorization: Bearer <your_access_token>

        Body (JSON):
        {
            "recipient_email": "customer@example.com",
            "subject": "How was your visit?",
            "body": "<p>Please leave us a review!</p>"
        }

    Response (success):
        {
            "success": true,
            "message": "Email sent successfully to customer@example.com"
        }

    Response (error):
        {
            "success": false,
            "message": "Description of what went wrong"
        }

    How to test with curl:
        curl -X POST http://localhost:5001/api/email/send \
            -H "Authorization: Bearer YOUR_TOKEN_HERE" \
            -H "Content-Type: application/json" \
            -d '{"recipient_email": "test@example.com", "subject": "Test", "body": "<p>Hello!</p>"}'
    """
    try:
        # Get the JSON data from the request body
        # When someone sends a POST request, they include data in the "body"
        data = request.get_json()

        # Validate that we received data
        if not data:
            return jsonify({
                "success": False,
                "message": "No data provided. Send JSON with recipient_email, subject, and body."
            }), 400  # 400 = Bad Request

        # Extract the fields we need
        recipient_email = data.get('recipient_email')
        subject = data.get('subject')
        body = data.get('body')

        # Validate required fields
        # We check each field and return a helpful error if missing
        if not recipient_email:
            return jsonify({
                "success": False,
                "message": "recipient_email is required"
            }), 400

        if not subject:
            return jsonify({
                "success": False,
                "message": "subject is required"
            }), 400

        if not body:
            return jsonify({
                "success": False,
                "message": "body is required"
            }), 400

        # Basic email format validation
        # A real app might use a library for more thorough validation
        if '@' not in recipient_email or '.' not in recipient_email:
            return jsonify({
                "success": False,
                "message": "recipient_email must be a valid email address"
            }), 400

        # Call our email service to actually send the email
        # The service handles all the SendGrid API communication
        result = send_email(
            to_email=recipient_email,
            subject=subject,
            html_body=body
        )

        # Return the result from the email service
        # We use different HTTP status codes based on success/failure
        if result["success"]:
            return jsonify({
                "success": True,
                "message": result["message"]
            }), 200  # 200 = OK
        else:
            return jsonify({
                "success": False,
                "message": result["message"]
            }), 500  # 500 = Internal Server Error

    except Exception as e:
        # Catch any unexpected errors
        # In production, you'd log this error for debugging
        return jsonify({
            "success": False,
            "message": f"An unexpected error occurred: {str(e)}"
        }), 500
