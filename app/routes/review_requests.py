"""
Review Requests API endpoints.

This is the CORE feature of Revvie - sending review requests to customers!

THE FULL FLOW:
==============
1. Business owner logs into your app (gets JWT token)
2. They enter customer info (name, email/phone) in your frontend
3. Frontend calls POST /api/review-requests/send with the customer info
4. This endpoint:
   a. Verifies the business owner is authenticated
   b. Gets the business info (name, google_place_id) from their account
   c. Generates the Google review URL
   d. Sends email and/or SMS based on the 'method' parameter
   e. Saves a record in the database (for tracking/analytics)
   f. Returns success/failure to the frontend
5. Customer receives email/SMS, clicks the link, leaves a review!

Endpoints:
- POST /api/review-requests/send - Send a review request (email, SMS, or both)
- POST /api/review-requests/bulk - Send review requests to multiple customers
- GET /api/review-requests - Get all review requests
"""

import logging
from datetime import datetime, timezone, timedelta
from flask import Blueprint, jsonify, request
from app.services.auth_service import require_auth
from app.services.email_service import send_review_request_email, generate_google_review_url
from app.services.google_places import get_review_url as places_review_url
from app.services.sms_service import send_review_request_sms, validate_phone_number
from app.services.supabase_service import supabase, supabase_admin

# Set up logging
logger = logging.getLogger(__name__)

# Create the blueprint
review_requests_bp = Blueprint('review_requests', __name__)

# Valid methods for sending review requests
VALID_METHODS = ['email', 'sms', 'both']


@review_requests_bp.route('/review-requests/send', methods=['POST'])
@require_auth  # Only logged-in business owners can send review requests
def send_review_request():
    """
    Send a review request to a customer via email, SMS, or both.

    This is the main endpoint your frontend will call when a business
    owner wants to request a review from a customer.

    Request:
        Headers:
            Authorization: Bearer <access_token>

        Body (JSON):
        {
            "customer_name": "John",
            "customer_email": "john@example.com",  // Required if method is 'email' or 'both'
            "customer_phone": "+14155551234",      // Required if method is 'sms' or 'both'
            "method": "email"                      // Options: 'email', 'sms', 'both' (default: 'email')
        }

    Response (success):
        {
            "success": true,
            "message": "Review request sent!",
            "data": {
                "customer_name": "John",
                "customer_email": "john@example.com",
                "customer_phone": "+14155551234",
                "business_name": "Joe's Coffee",
                "review_url": "https://search.google.com/local/writereview?placeid=...",
                "method": "both",
                "email_sent": true,
                "sms_sent": true,
                "sms_sid": "msg_abc123"
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
                "message": "No data provided. Send JSON with customer_name and customer_email/customer_phone."
            }), 400

        customer_name = data.get('customer_name')
        customer_email = data.get('customer_email')
        customer_phone = data.get('customer_phone')
        method = data.get('method', 'email').lower()  # Default to email

        # Validate method
        if method not in VALID_METHODS:
            return jsonify({
                "success": False,
                "message": f"Invalid method '{method}'. Must be one of: {', '.join(VALID_METHODS)}"
            }), 400

        # Validate required fields
        if not customer_name:
            return jsonify({
                "success": False,
                "message": "customer_name is required"
            }), 400

        # Validate based on method
        if method in ['email', 'both']:
            if not customer_email:
                return jsonify({
                    "success": False,
                    "message": "customer_email is required when method is 'email' or 'both'"
                }), 400
            # Basic email validation
            if '@' not in customer_email or '.' not in customer_email:
                return jsonify({
                    "success": False,
                    "message": "customer_email must be a valid email address"
                }), 400

        if method in ['sms', 'both']:
            if not customer_phone:
                return jsonify({
                    "success": False,
                    "message": "customer_phone is required when method is 'sms' or 'both'"
                }), 400
            # Validate phone number format
            is_valid, phone_result = validate_phone_number(customer_phone)
            if not is_valid:
                return jsonify({
                    "success": False,
                    "message": f"Invalid phone number: {phone_result}"
                }), 400
            customer_phone = phone_result  # Use formatted phone number

        # ============================================================
        # STEP 2: Get business info from the authenticated user
        # ============================================================
        business = request.business

        if not business:
            return jsonify({
                "success": False,
                "message": "Business profile not found. Please complete your profile."
            }), 404

        business_id = business.get('id')
        business_name = business.get('business_name')
        google_place_id = business.get('google_place_id')
        google_review_url = business.get('google_review_url')

        # Check if the business has set up their Google Place ID or review URL
        if not google_place_id and not google_review_url:
            return jsonify({
                "error": "Please connect your Google Business Profile first",
                "action": "Go to Settings \u2192 Connect Google Business",
                "redirect": "/onboarding"
            }), 400

        # ============================================================
        # STEP 3: Generate the Google review URL
        # ============================================================
        review_url = google_review_url or places_review_url(google_place_id)

        # ============================================================
        # STEP 4: Send the review request(s)
        # ============================================================
        email_sent = False
        email_error = None
        sms_sent = False
        sms_sid = None
        sms_status = None
        sms_error = None

        # Send email if method is 'email' or 'both'
        if method in ['email', 'both']:
            logger.info(f"Sending review request email to {customer_email}")
            email_result = send_review_request_email(
                customer_name=customer_name,
                customer_email=customer_email,
                business_name=business_name,
                review_url=review_url
            )
            email_sent = email_result['success']
            if not email_sent:
                email_error = email_result.get('message', 'Unknown email error')
                logger.error(f"Email failed: {email_error}")

        # Send SMS if method is 'sms' or 'both'
        if method in ['sms', 'both']:
            logger.info(f"Sending review request SMS to {customer_phone}")
            sms_result = send_review_request_sms(
                customer_name=customer_name,
                customer_phone=customer_phone,
                business_name=business_name,
                review_url=review_url
            )
            sms_sent = sms_result['success']
            if sms_sent:
                sms_sid = sms_result.get('message_id')
                sms_status = 'sent'
            else:
                sms_error = sms_result.get('error', 'Unknown SMS error')
                sms_status = 'failed'
                logger.error(f"SMS failed: {sms_error}")

        # ============================================================
        # STEP 5: Determine overall success
        # ============================================================
        # For 'both' method, we consider it a success if at least one worked
        if method == 'email':
            overall_success = email_sent
        elif method == 'sms':
            overall_success = sms_sent
        else:  # both
            overall_success = email_sent or sms_sent

        if not overall_success:
            error_parts = []
            if method in ['email', 'both'] and not email_sent:
                error_parts.append(f"Email: {email_error}")
            if method in ['sms', 'both'] and not sms_sent:
                error_parts.append(f"SMS: {sms_error}")
            return jsonify({
                "success": False,
                "message": f"Failed to send review request. {' | '.join(error_parts)}"
            }), 500

        # ============================================================
        # STEP 6: Save record to database for tracking
        # ============================================================
        review_request_data = {
            "business_id": business_id,
            "customer_name": customer_name,
            "customer_email": customer_email,
            "customer_phone": customer_phone,
            "status": "sent",
            "method": method,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            # SMS tracking fields
            "sms_sid": sms_sid,
            "sms_status": sms_status,
            "sms_error": sms_error if not sms_sent and method in ['sms', 'both'] else None
        }

        # Insert into the review_requests table
        db_result = supabase.table("review_requests").insert(review_request_data).execute()

        if not db_result.data:
            logger.warning(f"Failed to save review request to database for {customer_email or customer_phone}")

        # ============================================================
        # STEP 7: Return success response
        # ============================================================
        response_data = {
            "customer_name": customer_name,
            "business_name": business_name,
            "review_url": review_url,
            "method": method
        }

        # Include email info if applicable
        if method in ['email', 'both']:
            response_data["customer_email"] = customer_email
            response_data["email_sent"] = email_sent
            if not email_sent:
                response_data["email_error"] = email_error

        # Include SMS info if applicable
        if method in ['sms', 'both']:
            response_data["customer_phone"] = customer_phone
            response_data["sms_sent"] = sms_sent
            if sms_sent:
                response_data["sms_sid"] = sms_sid
            else:
                response_data["sms_error"] = sms_error

        # Build appropriate message
        if method == 'both':
            if email_sent and sms_sent:
                message = "Review request sent via email and SMS!"
            elif email_sent:
                message = "Review request sent via email. SMS failed."
            else:
                message = "Review request sent via SMS. Email failed."
        elif method == 'email':
            message = "Review request sent via email!"
        else:
            message = "Review request sent via SMS!"

        return jsonify({
            "success": True,
            "message": message,
            "data": response_data
        }), 200

    except Exception as e:
        logger.exception(f"Unexpected error in send_review_request: {e}")
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
                    "customer_phone": "+14155551234",
                    "status": "sent",
                    "method": "both",
                    "sms_sid": "msg_abc123",
                    "sms_status": "delivered",
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


@review_requests_bp.route('/review-requests/bulk', methods=['POST'])
@require_auth
def send_bulk_review_requests():
    """
    Send review request emails/SMS to multiple customers at once.

    Request:
        Headers:
            Authorization: Bearer <access_token>

        Body (JSON):
        {
            "customer_ids": ["uuid1", "uuid2", "uuid3"],
            "method": "email"  // Options: 'email', 'sms', 'both' (default: 'email')
        }

    Response:
        {
            "success": true,
            "success_count": 3,
            "error_count": 1,
            "errors": [{"customer_id": "uuid4", "message": "Email failed"}]
        }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({
                "success": False,
                "message": "No data provided"
            }), 400

        customer_ids = data.get('customer_ids', [])
        method = data.get('method', 'email').lower()

        if not customer_ids:
            return jsonify({
                "success": False,
                "message": "No customer IDs provided"
            }), 400

        if not isinstance(customer_ids, list):
            return jsonify({
                "success": False,
                "message": "customer_ids must be an array"
            }), 400

        if method not in VALID_METHODS:
            return jsonify({
                "success": False,
                "message": f"Invalid method '{method}'. Must be one of: {', '.join(VALID_METHODS)}"
            }), 400

        # Get business info
        business = request.business

        if not business:
            return jsonify({
                "success": False,
                "message": "Business profile not found"
            }), 404

        business_id = business.get('id')
        business_name = business.get('business_name')
        google_place_id = business.get('google_place_id')
        google_review_url = business.get('google_review_url')

        if not google_place_id and not google_review_url:
            return jsonify({
                "error": "Please connect your Google Business Profile first",
                "action": "Go to Settings \u2192 Connect Google Business",
                "redirect": "/onboarding"
            }), 400

        # Generate review URL once (same for all customers)
        review_url = google_review_url or places_review_url(google_place_id)

        # Fetch all customers at once
        customers_result = supabase.table("customers") \
            .select("*") \
            .eq("business_id", business_id) \
            .in_("id", customer_ids) \
            .execute()

        if not customers_result.data:
            return jsonify({
                "success": False,
                "message": "No customers found with the provided IDs"
            }), 404

        customers = {c['id']: c for c in customers_result.data}

        success_count = 0
        error_count = 0
        errors = []
        review_requests_to_insert = []

        for customer_id in customer_ids:
            customer = customers.get(customer_id)

            if not customer:
                errors.append({
                    "customer_id": customer_id,
                    "message": "Customer not found"
                })
                error_count += 1
                continue

            customer_name = customer.get('name')
            customer_email = customer.get('email')
            customer_phone = customer.get('phone')

            # Validate contact info based on method
            if method in ['email', 'both'] and not customer_email:
                if method == 'email':
                    errors.append({
                        "customer_id": customer_id,
                        "message": "Customer has no email address"
                    })
                    error_count += 1
                    continue

            if method in ['sms', 'both'] and not customer_phone:
                if method == 'sms':
                    errors.append({
                        "customer_id": customer_id,
                        "message": "Customer has no phone number"
                    })
                    error_count += 1
                    continue

            # Send based on method
            email_sent = False
            sms_sent = False
            sms_sid = None
            sms_status = None
            sms_error = None

            try:
                # Send email
                if method in ['email', 'both'] and customer_email:
                    email_result = send_review_request_email(
                        customer_name=customer_name,
                        customer_email=customer_email,
                        business_name=business_name,
                        review_url=review_url
                    )
                    email_sent = email_result['success']

                # Send SMS
                if method in ['sms', 'both'] and customer_phone:
                    sms_result = send_review_request_sms(
                        customer_name=customer_name,
                        customer_phone=customer_phone,
                        business_name=business_name,
                        review_url=review_url
                    )
                    sms_sent = sms_result['success']
                    if sms_sent:
                        sms_sid = sms_result.get('message_id')
                        sms_status = 'sent'
                    else:
                        sms_error = sms_result.get('error')
                        sms_status = 'failed'

                # Check if at least one method succeeded
                if method == 'email' and email_sent:
                    success_count += 1
                elif method == 'sms' and sms_sent:
                    success_count += 1
                elif method == 'both' and (email_sent or sms_sent):
                    success_count += 1
                else:
                    errors.append({
                        "customer_id": customer_id,
                        "message": "Failed to send via any method"
                    })
                    error_count += 1
                    continue

                # Prepare review request record
                review_requests_to_insert.append({
                    "business_id": business_id,
                    "customer_id": customer_id,
                    "customer_name": customer_name,
                    "customer_email": customer_email,
                    "customer_phone": customer_phone,
                    "status": "sent",
                    "method": method,
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                    "sms_sid": sms_sid,
                    "sms_status": sms_status,
                    "sms_error": sms_error
                })

            except Exception as e:
                errors.append({
                    "customer_id": customer_id,
                    "message": str(e)
                })
                error_count += 1

        # Bulk insert all successful review requests
        if review_requests_to_insert:
            try:
                supabase.table("review_requests").insert(review_requests_to_insert).execute()
            except Exception as e:
                logger.warning(f"Failed to save some review requests to database: {e}")

        return jsonify({
            "success": True,
            "success_count": success_count,
            "error_count": error_count,
            "errors": errors
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"An error occurred: {str(e)}"
        }), 500


@review_requests_bp.route('/review-requests/queue-bulk', methods=['POST'])
@require_auth
def queue_bulk_review_requests():
    """
    Queue review requests for multiple customers to be sent later.

    This is used after CSV import to schedule review requests.

    Request body:
    {
        "customer_ids": ["uuid1", "uuid2", ...],
        "delay_hours": 2  // How many hours from now to send (default: 2)
    }

    Returns:
    {
        "success": true,
        "queued_count": 47,
        "error_count": 0,
        "errors": []
    }
    """
    try:
        data = request.get_json() or {}
        customer_ids = data.get('customer_ids', [])
        delay_hours = data.get('delay_hours', 2)

        if not customer_ids:
            return jsonify({
                "success": False,
                "message": "No customer IDs provided"
            }), 400

        if not isinstance(customer_ids, list):
            return jsonify({
                "success": False,
                "message": "customer_ids must be an array"
            }), 400

        # Validate delay_hours
        try:
            delay_hours = int(delay_hours)
            if delay_hours < 0:
                delay_hours = 0
            if delay_hours > 168:  # Max 1 week
                delay_hours = 168
        except (ValueError, TypeError):
            delay_hours = 2

        business_id = request.business.get('id')
        business_name = request.business.get('name')

        # Fetch all customers
        customers_result = supabase.table("customers") \
            .select("id, name, email, phone") \
            .in_("id", customer_ids) \
            .eq("business_id", business_id) \
            .execute()

        customers = customers_result.data or []

        if not customers:
            return jsonify({
                "success": False,
                "message": "No valid customers found"
            }), 404

        # Calculate scheduled send time
        scheduled_time = datetime.now(timezone.utc) + timedelta(hours=delay_hours)

        queued_count = 0
        error_count = 0
        errors = []
        queue_records = []

        for customer in customers:
            # Must have email or phone
            if not customer.get('email') and not customer.get('phone'):
                errors.append({
                    "customer_id": customer['id'],
                    "message": "Customer has no email or phone"
                })
                error_count += 1
                continue

            # Determine method based on available contact info
            if customer.get('email') and customer.get('phone'):
                method = 'both'
            elif customer.get('email'):
                method = 'email'
            else:
                method = 'sms'

            queue_records.append({
                "business_id": business_id,
                "customer_name": customer.get('name', 'Customer'),
                "customer_email": customer.get('email'),
                "customer_phone": customer.get('phone'),
                "status": "queued",
                "method": method,
                "scheduled_send_at": scheduled_time.isoformat(),
                "integration_source": "csv_import",
                "created_at": datetime.now(timezone.utc).isoformat()
            })
            queued_count += 1

        # Bulk insert into queue (use admin client to bypass RLS)
        if queue_records:
            try:
                supabase_admin.table("queued_review_requests").insert(queue_records).execute()
                logger.info(f"Queued {queued_count} review requests for business {business_id}")
            except Exception as e:
                logger.error(f"Failed to queue review requests: {e}")
                return jsonify({
                    "success": False,
                    "message": f"Failed to queue requests: {str(e)}"
                }), 500

        return jsonify({
            "success": True,
            "queued_count": queued_count,
            "error_count": error_count,
            "errors": errors,
            "scheduled_for": scheduled_time.isoformat()
        }), 200

    except Exception as e:
        logger.error(f"Queue bulk error: {e}")
        return jsonify({
            "success": False,
            "message": f"An error occurred: {str(e)}"
        }), 500


@review_requests_bp.route('/review-requests/queue/debug', methods=['GET'])
@require_auth
def debug_queue():
    """
    Debug endpoint to inspect the queue status.

    Returns current time, queued items, and why they might not be processing.
    """
    try:
        business_id = request.business.get('id')
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        # Get all queued items for this business
        queued_result = supabase.table("queued_review_requests") \
            .select("*") \
            .eq("business_id", business_id) \
            .eq("status", "queued") \
            .order("scheduled_send_at", desc=False) \
            .limit(50) \
            .execute()

        queued_items = queued_result.data or []

        # Analyze each item
        analysis = []
        for item in queued_items:
            scheduled_at = item.get('scheduled_send_at')
            is_ready = scheduled_at <= now_iso if scheduled_at else False

            analysis.append({
                "id": item['id'],
                "customer_name": item.get('customer_name'),
                "customer_email": item.get('customer_email'),
                "customer_phone": item.get('customer_phone'),
                "method": item.get('method'),
                "scheduled_send_at": scheduled_at,
                "created_at": item.get('created_at'),
                "is_ready_to_send": is_ready,
                "reason_not_ready": None if is_ready else f"Scheduled for {scheduled_at}, current time is {now_iso}"
            })

        # Count how many are ready
        ready_count = sum(1 for a in analysis if a['is_ready_to_send'])

        return jsonify({
            "success": True,
            "server_time_utc": now_iso,
            "total_queued": len(queued_items),
            "ready_to_send": ready_count,
            "items": analysis
        }), 200

    except Exception as e:
        logger.error(f"Queue debug error: {e}")
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@review_requests_bp.route('/review-requests/queue/process', methods=['POST'])
@require_auth
def manually_process_queue():
    """
    Manually trigger the queue processor.

    This is useful for debugging or forcing immediate processing.
    """
    try:
        from app.services.queue_processor import process_queued_requests

        logger.info("Manually triggered queue processing")
        result = process_queued_requests()

        return jsonify({
            "success": True,
            "message": "Queue processing completed",
            "result": result
        }), 200

    except Exception as e:
        logger.error(f"Manual queue process error: {e}")
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500
