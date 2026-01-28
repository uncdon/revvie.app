"""
Review Requests API endpoints.

This is the CORE feature of Revvie - sending review requests to customers!

THE FULL FLOW:
==============
1. Business owner logs into your app (gets JWT token)
2. They enter customer info (name, email) in your frontend
3. Frontend calls POST /api/review-requests/send with the customer info
4. This endpoint:
   a. Verifies the business owner is authenticated
   b. Gets the business info (name, google_place_id) from their account
   c. Generates the Google review URL
   d. Sends a beautiful email to the customer
   e. Saves a record in the database (for tracking/analytics)
   f. Returns success/failure to the frontend
5. Customer receives email, clicks the button, leaves a review!

Endpoints:
- POST /api/review-requests/send - Send a review request email
"""

from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from app.services.auth_service import require_auth
from app.services.email_service import send_review_request_email, generate_google_review_url
from app.services.supabase_service import supabase

# Create the blueprint
review_requests_bp = Blueprint('review_requests', __name__)


@review_requests_bp.route('/review-requests/send', methods=['POST'])
@require_auth  # Only logged-in business owners can send review requests
def send_review_request():
    """
    Send a review request email to a customer.

    This is the main endpoint your frontend will call when a business
    owner wants to request a review from a customer.

    Request:
        Headers:
            Authorization: Bearer <access_token>

        Body (JSON):
        {
            "customer_name": "John",
            "customer_email": "john@example.com"
        }

    Response (success):
        {
            "success": true,
            "message": "Review request sent!",
            "data": {
                "customer_name": "John",
                "customer_email": "john@example.com",
                "business_name": "Joe's Coffee",
                "review_url": "https://search.google.com/local/writereview?placeid=..."
            }
        }

    Response (error):
        {
            "success": false,
            "message": "Description of what went wrong"
        }
    """
    try:
        # ============================================================
        # STEP 1: Get and validate the request data
        # ============================================================
        data = request.get_json()

        if not data:
            return jsonify({
                "success": False,
                "message": "No data provided. Send JSON with customer_name and customer_email."
            }), 400

        customer_name = data.get('customer_name')
        customer_email = data.get('customer_email')

        # Validate required fields
        if not customer_name:
            return jsonify({
                "success": False,
                "message": "customer_name is required"
            }), 400

        if not customer_email:
            return jsonify({
                "success": False,
                "message": "customer_email is required"
            }), 400

        # Basic email validation
        if '@' not in customer_email or '.' not in customer_email:
            return jsonify({
                "success": False,
                "message": "customer_email must be a valid email address"
            }), 400

        # ============================================================
        # STEP 2: Get business info from the authenticated user
        # ============================================================
        # request.business is set by the @require_auth decorator
        # It contains the business data from the database
        business = request.business

        if not business:
            return jsonify({
                "success": False,
                "message": "Business profile not found. Please complete your profile."
            }), 404

        business_id = business.get('id')
        business_name = business.get('business_name')
        google_place_id = business.get('google_place_id')

        # Check if the business has set up their Google Place ID
        if not google_place_id:
            return jsonify({
                "success": False,
                "message": "Google Place ID not configured. Please add your Google Place ID in settings."
            }), 400

        # ============================================================
        # STEP 3: Generate the Google review URL
        # ============================================================
        review_url = generate_google_review_url(google_place_id)

        # ============================================================
        # STEP 4: Send the email
        # ============================================================
        email_result = send_review_request_email(
            customer_name=customer_name,
            customer_email=customer_email,
            business_name=business_name,
            review_url=review_url
        )

        # Check if email was sent successfully
        if not email_result['success']:
            return jsonify({
                "success": False,
                "message": f"Failed to send email: {email_result['message']}"
            }), 500

        # ============================================================
        # STEP 5: Save record to database for tracking
        # ============================================================
        # This creates a record so you can:
        # - See how many requests were sent
        # - Track which customers were contacted
        # - Build analytics dashboards later

        review_request_data = {
            "business_id": business_id,
            "customer_name": customer_name,
            "customer_email": customer_email,
            "status": "sent",           # Status: sent, opened, clicked, reviewed
            "method": "email",          # Method: email, sms
            "sent_at": datetime.now(timezone.utc).isoformat()
        }

        # Insert into the review_requests table
        db_result = supabase.table("review_requests").insert(review_request_data).execute()

        if not db_result.data:
            # Email was sent but database save failed
            # We still return success because the customer got the email
            # But we log this for debugging
            print(f"Warning: Failed to save review request to database for {customer_email}")

        # ============================================================
        # STEP 6: Return success response
        # ============================================================
        return jsonify({
            "success": True,
            "message": "Review request sent!",
            "data": {
                "customer_name": customer_name,
                "customer_email": customer_email,
                "business_name": business_name,
                "review_url": review_url
            }
        }), 200

    except Exception as e:
        # Catch any unexpected errors
        return jsonify({
            "success": False,
            "message": f"An unexpected error occurred: {str(e)}"
        }), 500


@review_requests_bp.route('/review-requests', methods=['GET'])
@require_auth
def get_review_requests():
    """
    Get all review requests for the authenticated business.

    This endpoint lets business owners see their sent review requests.

    Response:
        {
            "success": true,
            "data": [
                {
                    "id": 1,
                    "customer_name": "John",
                    "customer_email": "john@example.com",
                    "status": "sent",
                    "method": "email",
                    "sent_at": "2024-01-15T10:30:00Z"
                },
                ...
            ]
        }
    """
    try:
        business_id = request.business.get('id')

        # Query review requests for this business, newest first
        result = supabase.table("review_requests") \
            .select("*") \
            .eq("business_id", business_id) \
            .order("sent_at", desc=True) \
            .execute()

        return jsonify({
            "success": True,
            "data": result.data
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Failed to fetch review requests: {str(e)}"
        }), 500
