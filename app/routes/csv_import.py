"""
CSV Import API endpoints.

Endpoints:
- POST /api/customers/import-csv - Upload and import customers from CSV file
- GET /api/customers/import-csv/example - Download example CSV template
"""

import os
import uuid
import logging
from datetime import datetime, timezone
from io import StringIO

from flask import Blueprint, jsonify, request, Response
from werkzeug.utils import secure_filename

from app.services.auth_service import require_auth
from app.middleware.require_verified_email import require_verified_email
from app.services.supabase_service import supabase
from app.services.csv_parser import parse_and_validate, preview_csv
from app.services import duplicate_checker

# Configure logging
logger = logging.getLogger(__name__)

csv_import_bp = Blueprint('csv_import', __name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

# Allowed file extensions
ALLOWED_EXTENSIONS = {'csv', 'txt'}

# Maximum file size: 5MB
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB in bytes

# Temporary upload directory
UPLOAD_FOLDER = '/tmp'


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def allowed_file(filename: str) -> bool:
    """
    Check if the file has an allowed extension.

    Args:
        filename: Original filename from upload

    Returns:
        True if extension is allowed, False otherwise
    """
    if '.' not in filename:
        return False
    extension = filename.rsplit('.', 1)[1].lower()
    return extension in ALLOWED_EXTENSIONS


def get_temp_filepath() -> str:
    """
    Generate a unique temporary file path.

    Returns:
        Path like /tmp/upload_abc123.csv
    """
    unique_id = uuid.uuid4().hex[:8]
    return os.path.join(UPLOAD_FOLDER, f'upload_{unique_id}.csv')


def cleanup_temp_file(filepath: str) -> None:
    """
    Safely delete a temporary file.

    Args:
        filepath: Path to the temporary file
    """
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
            logger.debug(f"Cleaned up temporary file: {filepath}")
    except Exception as e:
        logger.warning(f"Failed to cleanup temp file {filepath}: {e}")


# =============================================================================
# ROUTES
# =============================================================================

@csv_import_bp.route('/import-csv', methods=['POST'])
@require_auth
@require_verified_email
def import_csv():
    """
    Import customers from an uploaded CSV file.

    Request:
        Headers:
            Authorization: Bearer <access_token>
            Content-Type: multipart/form-data

        Form Data:
            file: CSV file (required)
            mode: 'skip' or 'update' for duplicates (optional, default: 'skip')

    Response (success):
        {
            "success": true,
            "imported": 10,
            "updated": 2,
            "skipped": 3,
            "errors": [
                {"row": 5, "reason": "No valid email or phone"}
            ],
            "customers": [
                {"id": "uuid", "name": "John Doe", "email": "john@example.com", ...}
            ]
        }

    Error Responses:
        400: No file provided, wrong file type, or CSV parsing error
        413: File too large
        500: Database error
    """
    temp_filepath = None

    try:
        # =================================================================
        # STEP 1: Validate file upload
        # =================================================================

        # Check if file was provided
        if 'file' not in request.files:
            logger.warning("CSV import attempted without file")
            return jsonify({
                "success": False,
                "message": "No file provided. Please upload a CSV file."
            }), 400

        file = request.files['file']

        # Check if file was actually selected
        if file.filename == '':
            return jsonify({
                "success": False,
                "message": "No file selected. Please choose a CSV file to upload."
            }), 400

        # Validate file extension
        if not allowed_file(file.filename):
            return jsonify({
                "success": False,
                "message": "Invalid file type. Please upload a .csv or .txt file."
            }), 400

        # Check file size (read content length from header or check actual size)
        # Note: request.content_length may not be accurate for multipart
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)  # Reset to beginning

        if file_size > MAX_FILE_SIZE:
            return jsonify({
                "success": False,
                "message": f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB."
            }), 413

        if file_size == 0:
            return jsonify({
                "success": False,
                "message": "File is empty. Please upload a CSV with customer data."
            }), 400

        # =================================================================
        # STEP 2: Save file temporarily
        # =================================================================

        # Secure the filename (prevent directory traversal attacks)
        original_filename = secure_filename(file.filename)
        temp_filepath = get_temp_filepath()

        logger.info(f"Saving uploaded file '{original_filename}' to {temp_filepath}")
        file.save(temp_filepath)

        # =================================================================
        # STEP 3: Parse and validate CSV
        # =================================================================

        logger.info(f"Parsing CSV file: {temp_filepath}")
        parse_result = parse_and_validate(temp_filepath)

        if not parse_result['success']:
            # Get the first error message for user-friendly response
            error_msg = "Failed to parse CSV file"
            if parse_result['errors']:
                error_msg = parse_result['errors'][0].get('reason', error_msg)

            return jsonify({
                "success": False,
                "message": error_msg,
                "errors": parse_result['errors'],
                "column_mapping": parse_result.get('column_mapping', {})
            }), 400

        customers_to_process = parse_result['customers']

        if not customers_to_process:
            return jsonify({
                "success": False,
                "message": "No valid customers found in CSV. Ensure your file has name, email, or phone columns.",
                "errors": parse_result['errors'],
                "summary": parse_result['summary'],
                "column_mapping": parse_result.get('column_mapping', {})
            }), 400

        logger.info(f"Parsed {len(customers_to_process)} valid customers from CSV")

        # =================================================================
        # STEP 3.5: Filter out recently contacted customers
        # =================================================================
        business_id = request.business.get('id')
        total_in_csv = len(customers_to_process)

        check_result = duplicate_checker.check_bulk_duplicates(
            business_id=business_id,
            customers_list=customers_to_process
        )

        recently_contacted_count = check_result['duplicate_count']

        if recently_contacted_count > 0:
            # Build set of safe emails/phones to filter
            safe_identifiers = set()
            for safe in check_result['safe_to_send']:
                if safe.get('email'):
                    safe_identifiers.add(safe['email'].lower())
                if safe.get('phone'):
                    safe_identifiers.add(safe['phone'])

            filtered = []
            for c in customers_to_process:
                email = c.get('email', '') or ''
                phone = c.get('phone', '') or ''
                if not email and not phone:
                    filtered.append(c)  # No contact info = can't be a duplicate
                elif email.lower() in safe_identifiers or phone in safe_identifiers:
                    filtered.append(c)

            logger.info(
                f"CSV duplicate filter: {len(filtered)} safe to send, "
                f"{recently_contacted_count} recently contacted (skipped)"
            )
            customers_to_process = filtered

        # =================================================================
        # STEP 4: Get duplicate handling mode
        # =================================================================

        # 'skip' = skip duplicates (default)
        # 'update' = update existing customer with new data
        duplicate_mode = request.form.get('mode', 'skip').lower()
        if duplicate_mode not in ('skip', 'update'):
            duplicate_mode = 'skip'

        # =================================================================
        # STEP 5: Check for existing customers
        # =================================================================

        # Fetch existing customers for this business
        existing_result = supabase.table("customers") \
            .select("id, email, phone") \
            .eq("business_id", business_id) \
            .execute()

        # Build lookup dictionaries for fast duplicate detection
        # Key: normalized email/phone, Value: customer ID
        existing_by_email = {}
        existing_by_phone = {}

        for customer in existing_result.data:
            if customer.get('email'):
                existing_by_email[customer['email'].lower()] = customer['id']
            if customer.get('phone'):
                existing_by_phone[customer['phone']] = customer['id']

        logger.debug(f"Found {len(existing_by_email)} existing emails, {len(existing_by_phone)} existing phones")

        # =================================================================
        # STEP 6: Process customers - insert new, handle duplicates
        # =================================================================

        imported_count = 0
        updated_count = 0
        skipped_count = 0
        import_errors = parse_result['errors'].copy()  # Start with parsing errors
        imported_customers = []

        # Track what we've seen in this batch to avoid duplicate inserts
        batch_emails = set()
        batch_phones = set()

        for customer in customers_to_process:
            email = customer.get('email')
            phone = customer.get('phone')
            name = customer.get('name')
            last_visit = customer.get('last_visit')
            source_row = customer.get('source_row', 0)

            # Check for duplicate within this batch
            is_batch_duplicate = False
            if email and email.lower() in batch_emails:
                is_batch_duplicate = True
            if phone and phone in batch_phones:
                is_batch_duplicate = True

            if is_batch_duplicate:
                skipped_count += 1
                import_errors.append({
                    'row': source_row,
                    'reason': 'Duplicate within import file'
                })
                continue

            # Track in batch
            if email:
                batch_emails.add(email.lower())
            if phone:
                batch_phones.add(phone)

            # Check for existing customer
            existing_customer_id = None
            if email and email.lower() in existing_by_email:
                existing_customer_id = existing_by_email[email.lower()]
            elif phone and phone in existing_by_phone:
                existing_customer_id = existing_by_phone[phone]

            if existing_customer_id:
                if duplicate_mode == 'update':
                    # Update existing customer
                    try:
                        update_data = {}
                        if name:
                            update_data['name'] = name
                        if email:
                            update_data['email'] = email
                        if phone:
                            update_data['phone'] = phone
                        if last_visit:
                            update_data['last_visit'] = last_visit

                        if update_data:
                            result = supabase.table("customers") \
                                .update(update_data) \
                                .eq("id", existing_customer_id) \
                                .eq("business_id", business_id) \
                                .execute()

                            if result.data:
                                updated_count += 1
                                imported_customers.append(result.data[0])
                            else:
                                import_errors.append({
                                    'row': source_row,
                                    'reason': 'Failed to update existing customer'
                                })
                    except Exception as e:
                        logger.error(f"Failed to update customer: {e}")
                        import_errors.append({
                            'row': source_row,
                            'reason': f'Database error: {str(e)}'
                        })
                else:
                    # Skip duplicate
                    skipped_count += 1
            else:
                # Insert new customer
                try:
                    customer_data = {
                        "business_id": business_id,
                        "name": name,
                        "email": email,
                        "phone": phone,
                        "created_at": datetime.now(timezone.utc).isoformat()
                    }

                    # Add last_visit if present
                    if last_visit:
                        customer_data['last_visit'] = last_visit

                    result = supabase.table("customers") \
                        .insert(customer_data) \
                        .execute()

                    if result.data:
                        imported_count += 1
                        imported_customers.append(result.data[0])

                        # Update lookup for subsequent duplicate checking
                        new_customer = result.data[0]
                        if new_customer.get('email'):
                            existing_by_email[new_customer['email'].lower()] = new_customer['id']
                        if new_customer.get('phone'):
                            existing_by_phone[new_customer['phone']] = new_customer['id']
                    else:
                        import_errors.append({
                            'row': source_row,
                            'reason': 'Failed to insert customer'
                        })

                except Exception as e:
                    logger.error(f"Failed to insert customer: {e}")
                    import_errors.append({
                        'row': source_row,
                        'reason': f'Database error: {str(e)}'
                    })

        # =================================================================
        # STEP 7: Return results
        # =================================================================

        logger.info(
            f"CSV import complete: {imported_count} imported, {updated_count} updated, "
            f"{skipped_count} skipped, {recently_contacted_count} recently contacted"
        )

        cooldown_days = duplicate_checker.get_cooldown_setting(business_id)

        return jsonify({
            "success": True,
            "message": (
                f"Imported {imported_count} new customers"
                + (f", skipped {recently_contacted_count} recently contacted"
                   f" (within {cooldown_days} days)" if recently_contacted_count > 0 else "")
            ),
            "imported": imported_count,
            "updated": updated_count,
            "skipped": skipped_count,
            "recently_contacted": recently_contacted_count,
            "total_in_csv": total_in_csv,
            "errors": import_errors,
            "customers": imported_customers,
            "summary": {
                "total_rows": parse_result['summary']['total_rows'],
                "valid_rows": parse_result['summary']['valid_rows'],
                "invalid_rows": parse_result['summary']['invalid_rows'],
                "skipped_rows": parse_result['summary']['skipped_rows'] + skipped_count,
                "recently_contacted": recently_contacted_count,
            },
            "column_mapping": parse_result.get('column_mapping', {})
        }), 200

    except Exception as e:
        logger.exception(f"Unexpected error during CSV import: {e}")
        return jsonify({
            "success": False,
            "message": f"An unexpected error occurred: {str(e)}"
        }), 500

    finally:
        # Always clean up the temporary file
        cleanup_temp_file(temp_filepath)


@csv_import_bp.route('/import-csv/preview', methods=['POST'])
@require_auth
def preview_csv_upload():
    """
    Preview a CSV file before importing.

    Shows detected encoding, delimiter, columns, and sample rows.
    Useful for users to verify their CSV will be parsed correctly.

    Request:
        Headers:
            Authorization: Bearer <access_token>
            Content-Type: multipart/form-data

        Form Data:
            file: CSV file (required)

    Response:
        {
            "success": true,
            "encoding": "utf-8",
            "delimiter": ",",
            "columns": ["Name", "Email", "Phone"],
            "total_rows": 100,
            "sample_rows": [...first 5 rows...],
            "suggested_mapping": {
                "name": "Name",
                "email": "Email",
                "phone": "Phone"
            }
        }
    """
    temp_filepath = None

    try:
        # Validate file upload (same as import)
        if 'file' not in request.files:
            return jsonify({
                "success": False,
                "message": "No file provided"
            }), 400

        file = request.files['file']

        if file.filename == '':
            return jsonify({
                "success": False,
                "message": "No file selected"
            }), 400

        if not allowed_file(file.filename):
            return jsonify({
                "success": False,
                "message": "Invalid file type. Please upload a .csv or .txt file."
            }), 400

        # Save temporarily
        temp_filepath = get_temp_filepath()
        file.save(temp_filepath)

        # Get preview
        result = preview_csv(temp_filepath, num_rows=5)

        return jsonify(result), 200 if result.get('success') else 400

    except Exception as e:
        logger.exception(f"Error previewing CSV: {e}")
        return jsonify({
            "success": False,
            "message": f"Error previewing file: {str(e)}"
        }), 500

    finally:
        cleanup_temp_file(temp_filepath)


@csv_import_bp.route('/import-csv/example', methods=['GET'])
def download_example_csv():
    """
    Download an example CSV template.

    Returns a CSV file with headers and sample data to help users
    understand the expected format.

    Response:
        Content-Type: text/csv
        Content-Disposition: attachment; filename="customer_import_template.csv"

        CSV content with headers and 3 sample rows
    """
    # Create example CSV content
    csv_content = """name,email,phone,date
John Smith,john.smith@example.com,(555) 123-4567,2024-01-15
Jane Doe,jane.doe@example.com,555-987-6543,2024-01-10
Bob Wilson,bob@example.com,+1 555 456 7890,2024-01-05
"""

    # Create response with CSV content
    response = Response(
        csv_content,
        mimetype='text/csv',
        headers={
            'Content-Disposition': 'attachment; filename=customer_import_template.csv',
            'Content-Type': 'text/csv; charset=utf-8'
        }
    )

    return response


@csv_import_bp.route('/import-csv/formats', methods=['GET'])
def get_supported_formats():
    """
    Get information about supported CSV formats.

    Returns documentation about what formats and columns are supported.

    Response:
        {
            "success": true,
            "supported_extensions": [".csv", ".txt"],
            "max_file_size": "5MB",
            "supported_encodings": ["UTF-8", "Latin-1", "Windows-1252"],
            "supported_delimiters": [",", ";", "\\t", "|"],
            "recognized_columns": {
                "name": ["name", "customer name", "client name", ...],
                "email": ["email", "email address", ...],
                "phone": ["phone", "mobile", "cell", ...],
                "date": ["date", "last visit", ...]
            }
        }
    """
    return jsonify({
        "success": True,
        "supported_extensions": [".csv", ".txt"],
        "max_file_size": "5MB",
        "max_file_size_bytes": MAX_FILE_SIZE,
        "supported_encodings": [
            "UTF-8 (recommended)",
            "Latin-1 / ISO-8859-1",
            "Windows-1252",
            "UTF-16"
        ],
        "supported_delimiters": {
            "comma": ",",
            "semicolon": ";",
            "tab": "\\t",
            "pipe": "|"
        },
        "recognized_columns": {
            "name": [
                "name", "customer name", "client name", "full name",
                "customer", "client", "guest name", "member name"
            ],
            "first_name": [
                "first name", "firstname", "first", "fname"
            ],
            "last_name": [
                "last name", "lastname", "last", "lname", "surname"
            ],
            "email": [
                "email", "email address", "e-mail", "customer email",
                "client email"
            ],
            "phone": [
                "phone", "phone number", "mobile", "cell", "telephone",
                "customer phone", "cell phone", "mobile phone"
            ],
            "date": [
                "date", "appointment date", "visit date", "last visit",
                "booking date", "service date"
            ]
        },
        "booking_systems_tested": [
            "Fresha",
            "Square",
            "Vagaro",
            "Mindbody",
            "Excel exports"
        ],
        "tips": [
            "Each row must have at least an email OR phone number",
            "Phone numbers are automatically formatted to E.164 (+12025551234)",
            "Various date formats are automatically detected",
            "Column names are matched flexibly - 'Customer Email' works same as 'email'",
            "Duplicate customers (by email or phone) can be skipped or updated"
        ]
    }), 200
