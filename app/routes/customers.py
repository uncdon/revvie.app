"""
Customers API endpoints.

Endpoints:
- POST /api/customers - Create a new customer
- GET /api/customers - Get all customers for the business
- PUT /api/customers/<id> - Update a customer
- DELETE /api/customers/<id> - Delete a customer
- POST /api/customers/import - Bulk import customers from CSV
"""

import re
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from app.services.auth_service import require_auth
from app.services.supabase_service import supabase

customers_bp = Blueprint('customers', __name__)


@customers_bp.route('/customers', methods=['POST'])
@require_auth
def create_customer():
    """
    Create a new customer for the authenticated business.

    Request:
        Headers:
            Authorization: Bearer <access_token>

        Body (JSON):
        {
            "name": "John Smith",
            "email": "john@example.com",
            "phone": "555-123-4567"  // optional
        }

    Response (success):
        {
            "success": true,
            "message": "Customer created successfully",
            "data": {
                "id": "uuid",
                "business_id": "uuid",
                "name": "John Smith",
                "email": "john@example.com",
                "phone": "555-123-4567",
                "created_at": "2024-01-15T10:30:00Z"
            }
        }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({
                "success": False,
                "message": "No data provided"
            }), 400

        name = data.get('name', '').strip()
        email = data.get('email', '').strip()
        phone = data.get('phone', '').strip() if data.get('phone') else None

        # Validate required fields
        if not name:
            return jsonify({
                "success": False,
                "message": "Name is required"
            }), 400

        if not email:
            return jsonify({
                "success": False,
                "message": "Email is required"
            }), 400

        # Basic email validation
        if '@' not in email or '.' not in email:
            return jsonify({
                "success": False,
                "message": "Please enter a valid email address"
            }), 400

        business_id = request.business.get('id')

        customer_data = {
            "business_id": business_id,
            "name": name,
            "email": email,
            "phone": phone,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        result = supabase.table("customers").insert(customer_data).execute()

        if not result.data:
            return jsonify({
                "success": False,
                "message": "Failed to create customer"
            }), 500

        return jsonify({
            "success": True,
            "message": "Customer created successfully",
            "data": result.data[0]
        }), 201

    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"An error occurred: {str(e)}"
        }), 500


@customers_bp.route('/customers', methods=['GET'])
@require_auth
def get_customers():
    """
    Get all customers for the authenticated business.

    Response:
        {
            "success": true,
            "data": [
                {
                    "id": "uuid",
                    "name": "John Smith",
                    "email": "john@example.com",
                    "phone": "555-123-4567",
                    "created_at": "2024-01-15T10:30:00Z"
                },
                ...
            ]
        }
    """
    try:
        business_id = request.business.get('id')

        result = supabase.table("customers") \
            .select("*") \
            .eq("business_id", business_id) \
            .order("created_at", desc=True) \
            .execute()

        return jsonify({
            "success": True,
            "data": result.data
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Failed to fetch customers: {str(e)}"
        }), 500


@customers_bp.route('/customers/<customer_id>', methods=['PUT'])
@require_auth
def update_customer(customer_id):
    """
    Update an existing customer.

    Request:
        Headers:
            Authorization: Bearer <access_token>

        Body (JSON):
        {
            "name": "John Smith",
            "email": "john@example.com",
            "phone": "555-123-4567"
        }

    Response (success):
        {
            "success": true,
            "message": "Customer updated successfully",
            "data": { ... }
        }
    """
    try:
        data = request.get_json()
        business_id = request.business.get('id')

        if not data:
            return jsonify({
                "success": False,
                "message": "No data provided"
            }), 400

        # Verify customer belongs to this business
        existing = supabase.table("customers") \
            .select("*") \
            .eq("id", customer_id) \
            .eq("business_id", business_id) \
            .execute()

        if not existing.data:
            return jsonify({
                "success": False,
                "message": "Customer not found"
            }), 404

        # Build update data
        update_data = {}

        if 'name' in data:
            name = data['name'].strip()
            if not name:
                return jsonify({
                    "success": False,
                    "message": "Name cannot be empty"
                }), 400
            update_data['name'] = name

        if 'email' in data:
            email = data['email'].strip()
            if not email:
                return jsonify({
                    "success": False,
                    "message": "Email cannot be empty"
                }), 400
            if '@' not in email or '.' not in email:
                return jsonify({
                    "success": False,
                    "message": "Please enter a valid email address"
                }), 400
            update_data['email'] = email

        if 'phone' in data:
            update_data['phone'] = data['phone'].strip() if data['phone'] else None

        if not update_data:
            return jsonify({
                "success": False,
                "message": "No fields to update"
            }), 400

        result = supabase.table("customers") \
            .update(update_data) \
            .eq("id", customer_id) \
            .eq("business_id", business_id) \
            .execute()

        if not result.data:
            return jsonify({
                "success": False,
                "message": "Failed to update customer"
            }), 500

        return jsonify({
            "success": True,
            "message": "Customer updated successfully",
            "data": result.data[0]
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"An error occurred: {str(e)}"
        }), 500


@customers_bp.route('/customers/<customer_id>', methods=['DELETE'])
@require_auth
def delete_customer(customer_id):
    """
    Delete a customer.

    Response (success):
        {
            "success": true,
            "message": "Customer deleted successfully"
        }
    """
    try:
        business_id = request.business.get('id')

        # Verify customer belongs to this business
        existing = supabase.table("customers") \
            .select("id") \
            .eq("id", customer_id) \
            .eq("business_id", business_id) \
            .execute()

        if not existing.data:
            return jsonify({
                "success": False,
                "message": "Customer not found"
            }), 404

        # Delete customer
        supabase.table("customers") \
            .delete() \
            .eq("id", customer_id) \
            .eq("business_id", business_id) \
            .execute()

        return jsonify({
            "success": True,
            "message": "Customer deleted successfully"
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"An error occurred: {str(e)}"
        }), 500


def validate_email(email):
    """Validate email format."""
    pattern = r'^[^\s@]+@[^\s@]+\.[^\s@]+$'
    return bool(re.match(pattern, email))


def validate_phone(phone):
    """Validate phone format (optional, allows various formats)."""
    if not phone:
        return True
    # Remove common separators and check if remaining chars are valid
    cleaned = re.sub(r'[\s\-\(\)\.\+]', '', phone)
    return cleaned.isdigit() and 7 <= len(cleaned) <= 15


@customers_bp.route('/customers/import', methods=['POST'])
@require_auth
def import_customers():
    """
    Bulk import customers from CSV data.

    Request:
        Headers:
            Authorization: Bearer <access_token>

        Body (JSON):
        {
            "customers": [
                {"name": "John Doe", "email": "john@example.com", "phone": "+12345678900"},
                {"name": "Jane Smith", "email": "jane@example.com", "phone": null}
            ]
        }

    Response:
        {
            "success": true,
            "success_count": 2,
            "skipped_count": 0,
            "error_count": 0,
            "errors": []
        }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({
                "success": False,
                "message": "No data provided"
            }), 400

        customers = data.get('customers', [])

        if not customers:
            return jsonify({
                "success": False,
                "message": "No customers to import"
            }), 400

        if not isinstance(customers, list):
            return jsonify({
                "success": False,
                "message": "Customers must be an array"
            }), 400

        business_id = request.business.get('id')

        # Get existing customer emails for this business
        existing_result = supabase.table("customers") \
            .select("email") \
            .eq("business_id", business_id) \
            .execute()

        existing_emails = {c['email'].lower() for c in existing_result.data if c.get('email')}

        success_count = 0
        skipped_count = 0
        error_count = 0
        errors = []
        customers_to_insert = []

        for index, customer in enumerate(customers):
            row_num = index + 1  # 1-based row number for user display

            # Get and clean fields
            name = customer.get('name', '').strip() if customer.get('name') else ''
            email = customer.get('email', '').strip().lower() if customer.get('email') else ''
            phone = customer.get('phone', '').strip() if customer.get('phone') else None

            # Validate name
            if not name:
                errors.append({"row": row_num, "message": "Name is required"})
                error_count += 1
                continue

            # Validate email
            if not email:
                errors.append({"row": row_num, "message": "Email is required"})
                error_count += 1
                continue

            if not validate_email(email):
                errors.append({"row": row_num, "message": f"Invalid email format: {email}"})
                error_count += 1
                continue

            # Validate phone (if provided)
            if phone and not validate_phone(phone):
                errors.append({"row": row_num, "message": f"Invalid phone format: {phone}"})
                error_count += 1
                continue

            # Check for duplicates
            if email in existing_emails:
                skipped_count += 1
                continue

            # Add to existing emails set to catch duplicates within the import
            existing_emails.add(email)

            # Prepare customer for insertion
            customers_to_insert.append({
                "business_id": business_id,
                "name": name,
                "email": email,
                "phone": phone if phone else None,
                "created_at": datetime.now(timezone.utc).isoformat()
            })

        # Bulk insert valid customers
        if customers_to_insert:
            result = supabase.table("customers").insert(customers_to_insert).execute()
            if result.data:
                success_count = len(result.data)
            else:
                # If bulk insert fails, return error
                return jsonify({
                    "success": False,
                    "message": "Failed to insert customers into database"
                }), 500

        return jsonify({
            "success": True,
            "success_count": success_count,
            "skipped_count": skipped_count,
            "error_count": error_count,
            "errors": errors
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"An error occurred: {str(e)}"
        }), 500
