"""
Universal CSV Parser Service

This service provides robust CSV parsing that works with exports from any booking system
(Fresha, Square, Vagaro, Mindbody, custom Excel exports, etc.)

Features:
- Auto-detect file encoding (UTF-8, Latin1, etc.)
- Auto-detect delimiter (comma, semicolon, tab, pipe)
- Smart column mapping using fuzzy matching
- Phone number validation and E.164 formatting
- Email validation and normalization
- Comprehensive error reporting
"""

import re
import csv
from io import StringIO
from typing import Optional
from datetime import datetime

import chardet
import pandas as pd
import phonenumbers
from phonenumbers import NumberParseException
from dateutil import parser as date_parser
from dateutil.parser import ParserError


# =============================================================================
# ENCODING DETECTION
# =============================================================================

def detect_encoding(file_path: str) -> str:
    """
    Detect the character encoding of a CSV file.

    Different booking systems export CSVs in different encodings:
    - UTF-8: Most modern systems (Fresha, Square)
    - Latin-1/ISO-8859-1: Older systems, European exports
    - Windows-1252: Excel exports on Windows
    - UTF-16: Some Excel "Unicode" exports

    Args:
        file_path: Path to the CSV file

    Returns:
        Encoding string (e.g., 'utf-8', 'latin-1', 'windows-1252')
        Defaults to 'utf-8' if detection fails or confidence is low
    """
    # Read raw bytes from file for encoding detection
    # We read up to 100KB which is usually enough to detect encoding accurately
    with open(file_path, 'rb') as f:
        raw_data = f.read(100000)

    # Use chardet to analyze the byte patterns
    result = chardet.detect(raw_data)

    # chardet returns: {'encoding': 'utf-8', 'confidence': 0.99, 'language': ''}
    encoding = result.get('encoding', 'utf-8')
    confidence = result.get('confidence', 0)

    # If confidence is low, default to UTF-8 as it's most common
    # and can handle ASCII files correctly
    if confidence < 0.5 or encoding is None:
        return 'utf-8'

    # Normalize encoding names for consistency
    encoding = encoding.lower()

    # Map common encoding aliases
    encoding_map = {
        'ascii': 'utf-8',  # ASCII is a subset of UTF-8
        'iso-8859-1': 'latin-1',
        'iso-8859-2': 'latin-2',
        'cp1252': 'windows-1252',
    }

    return encoding_map.get(encoding, encoding)


# =============================================================================
# DELIMITER DETECTION
# =============================================================================

def detect_delimiter(file_path: str, encoding: str) -> str:
    """
    Detect the column delimiter used in a CSV file.

    Different systems use different delimiters:
    - Comma (,): Standard CSV, most US systems
    - Semicolon (;): European systems (where comma is decimal separator)
    - Tab (\t): TSV exports, some database exports
    - Pipe (|): Some legacy systems

    Args:
        file_path: Path to the CSV file
        encoding: The file's character encoding

    Returns:
        Single character delimiter (defaults to ',' if detection fails)
    """
    # Common delimiters to test, in order of likelihood
    delimiters = [',', ';', '\t', '|']

    # Read the first few lines to analyze
    with open(file_path, 'r', encoding=encoding, errors='replace') as f:
        # Read first 10 lines or until EOF
        sample_lines = []
        for i, line in enumerate(f):
            if i >= 10:
                break
            sample_lines.append(line)

    if not sample_lines:
        return ','  # Default to comma for empty files

    # Join sample lines for analysis
    sample_text = ''.join(sample_lines)

    # Use Python's CSV Sniffer for intelligent detection
    try:
        # Sniffer analyzes patterns to guess the dialect
        dialect = csv.Sniffer().sniff(sample_text, delimiters=''.join(delimiters))
        return dialect.delimiter
    except csv.Error:
        # Sniffer failed, fall back to counting delimiters
        pass

    # Fallback: Count occurrences and check for consistency
    # A good delimiter should appear the same number of times on each line
    best_delimiter = ','
    best_score = 0

    for delimiter in delimiters:
        counts = [line.count(delimiter) for line in sample_lines if line.strip()]

        if not counts or max(counts) == 0:
            continue

        # Check if delimiter count is consistent across lines
        # (allowing for some variation in data)
        avg_count = sum(counts) / len(counts)
        consistency = sum(1 for c in counts if abs(c - avg_count) <= 1) / len(counts)

        # Score based on count and consistency
        score = avg_count * consistency

        if score > best_score:
            best_score = score
            best_delimiter = delimiter

    return best_delimiter


# =============================================================================
# CSV PARSING
# =============================================================================

def parse_csv(file_path: str) -> pd.DataFrame:
    """
    Parse a CSV file into a pandas DataFrame with automatic encoding and delimiter detection.

    This function handles various edge cases:
    - BOM (Byte Order Mark) at file start
    - Mixed line endings (Windows/Unix)
    - Quoted fields containing delimiters
    - Extra whitespace in headers and values

    Args:
        file_path: Path to the CSV file

    Returns:
        pandas DataFrame with the CSV data

    Raises:
        ValueError: If the file cannot be parsed
        FileNotFoundError: If the file doesn't exist
    """
    # Step 1: Detect encoding
    encoding = detect_encoding(file_path)

    # Step 2: Detect delimiter
    delimiter = detect_delimiter(file_path, encoding)

    # Step 3: Read CSV with pandas
    try:
        df = pd.read_csv(
            file_path,
            encoding=encoding,
            delimiter=delimiter,
            # Handle various edge cases:
            skipinitialspace=True,     # Strip leading whitespace after delimiter
            skip_blank_lines=True,      # Ignore empty rows
            on_bad_lines='warn',        # Don't fail on malformed rows
            dtype=str,                  # Read everything as strings initially
            keep_default_na=False,      # Don't convert "NA" to NaN
            encoding_errors='replace',  # Replace undecodable chars with ?
        )

        # Clean up column names
        # - Strip whitespace
        # - Remove BOM characters that might be in first column
        df.columns = [
            col.strip().replace('\ufeff', '').replace('\ufffe', '')
            for col in df.columns
        ]

        # Strip whitespace from all string values
        for col in df.columns:
            df[col] = df[col].apply(lambda x: x.strip() if isinstance(x, str) else x)

        return df

    except Exception as e:
        raise ValueError(f"Failed to parse CSV file: {str(e)}")


# =============================================================================
# COLUMN MAPPING
# =============================================================================

def map_columns(df: pd.DataFrame) -> dict:
    """
    Intelligently map CSV columns to our standard field names.

    Different booking systems use different column names:
    - Fresha: "Client Name", "Email", "Mobile"
    - Square: "Customer Name", "Email Address", "Phone Number"
    - Vagaro: "First Name" + "Last Name", "Email", "Cell Phone"
    - Mindbody: "Client.FirstName", "Client.Email", "Client.MobilePhone"

    This function uses fuzzy matching to identify columns regardless of naming.

    Args:
        df: pandas DataFrame with CSV data

    Returns:
        Dictionary mapping our field names to actual CSV column names
        Example: {'name': 'Customer Name', 'email': 'Email Address', 'phone': 'Mobile'}
        Missing fields will have None as the value
    """
    # Define possible variations for each field (lowercase for matching)
    field_variations = {
        'name': [
            'name', 'customer name', 'client name', 'full name',
            'customer', 'client', 'contact name', 'contact',
            'guest name', 'guest', 'member name', 'member',
            'patient name', 'patient',  # For medical spas
            'fullname', 'customername', 'clientname',
        ],
        'first_name': [
            'first name', 'firstname', 'first', 'fname',
            'client.firstname', 'customer.firstname',
            'given name', 'givenname',
        ],
        'last_name': [
            'last name', 'lastname', 'last', 'lname', 'surname',
            'client.lastname', 'customer.lastname',
            'family name', 'familyname',
        ],
        'email': [
            'email', 'email address', 'e-mail', 'customer email',
            'client email', 'emailaddress', 'e-mail address',
            'contact email', 'client.email', 'customer.email',
            'mail', 'email_address',
        ],
        'phone': [
            'phone', 'phone number', 'mobile', 'cell', 'telephone',
            'customer phone', 'client phone', 'cell phone', 'mobile phone',
            'phonenumber', 'cellphone', 'mobilephone', 'tel',
            'contact phone', 'client.phone', 'client.mobilephone',
            'customer.phone', 'primary phone', 'phone_number',
            'mobile_phone', 'cell_phone',
        ],
        'date': [
            'date', 'appointment date', 'visit date', 'service date',
            'last visit', 'last appointment', 'booking date',
            'appointment', 'visit', 'last_visit', 'lastvisit',
            'date of visit', 'transaction date', 'created',
            'created date', 'created_at', 'createdat',
        ],
    }

    # Get lowercase versions of actual column names for matching
    actual_columns = {col.lower().strip(): col for col in df.columns}

    # Result mapping
    mapping = {
        'name': None,
        'first_name': None,
        'last_name': None,
        'email': None,
        'phone': None,
        'date': None,
    }

    # Match each field to actual columns
    for field, variations in field_variations.items():
        for variation in variations:
            # Try exact match first
            if variation in actual_columns:
                mapping[field] = actual_columns[variation]
                break

            # Try partial match (column contains the variation)
            for col_lower, col_original in actual_columns.items():
                # Don't let the 'name' field claim first/last name columns —
                # those should be handled by the first_name/last_name fields
                # so that we can combine them into a full name later.
                if field == 'name' and ('first' in col_lower or 'last' in col_lower):
                    continue
                if variation in col_lower or col_lower in variation:
                    # Avoid false positives (e.g., "phone" matching "cellphone2")
                    # by checking word boundaries
                    if (col_lower == variation or
                        col_lower.startswith(variation + ' ') or
                        col_lower.endswith(' ' + variation) or
                        ' ' + variation + ' ' in col_lower or
                        col_lower.startswith(variation + '_') or
                        col_lower.endswith('_' + variation)):
                        mapping[field] = col_original
                        break

            if mapping[field]:
                break

    return mapping


# =============================================================================
# PHONE VALIDATION
# =============================================================================

def validate_phone(phone_string: Optional[str], default_country: str = 'US') -> Optional[str]:
    """
    Validate and format a phone number to E.164 format.

    E.164 is the international standard format: +[country code][number]
    Examples: +12025551234, +447911123456

    This function handles various input formats:
    - (202) 555-1234
    - 202-555-1234
    - 202.555.1234
    - 2025551234
    - +1 202 555 1234
    - 1-202-555-1234

    Args:
        phone_string: Raw phone number string (can be messy)
        default_country: ISO country code for numbers without country code

    Returns:
        E.164 formatted phone number (e.g., '+12025551234')
        None if the phone number is invalid or missing
    """
    # Handle empty/None values
    if not phone_string or not isinstance(phone_string, str):
        return None

    # Clean up the input
    phone_string = phone_string.strip()

    # Remove common non-phone characters and annotations
    # Some systems add notes like "mobile:" or "cell:"
    phone_string = re.sub(r'^(phone|mobile|cell|tel|telephone|home|work|fax)[:\s]*', '',
                          phone_string, flags=re.IGNORECASE)

    # If empty after cleaning, return None
    if not phone_string:
        return None

    # Check if there are any digits at all
    if not re.search(r'\d', phone_string):
        return None

    try:
        # Parse the phone number
        # phonenumbers library is very smart about handling various formats
        parsed = phonenumbers.parse(phone_string, default_country)

        # Validate that it's a plausible phone number
        if not phonenumbers.is_valid_number(parsed):
            # Try to be lenient - some valid numbers fail strict validation
            # Check if it at least has the right length
            if not phonenumbers.is_possible_number(parsed):
                return None

        # Format to E.164
        formatted = phonenumbers.format_number(
            parsed,
            phonenumbers.PhoneNumberFormat.E164
        )

        return formatted

    except NumberParseException:
        # phonenumbers couldn't parse it
        # Last resort: try to extract just digits and validate length
        digits_only = re.sub(r'\D', '', phone_string)

        # US numbers: 10 digits, or 11 if starting with 1
        if len(digits_only) == 10:
            try:
                parsed = phonenumbers.parse('+1' + digits_only, None)
                if phonenumbers.is_valid_number(parsed):
                    return '+1' + digits_only
            except NumberParseException:
                pass
        elif len(digits_only) == 11 and digits_only.startswith('1'):
            try:
                parsed = phonenumbers.parse('+' + digits_only, None)
                if phonenumbers.is_valid_number(parsed):
                    return '+' + digits_only
            except NumberParseException:
                pass

        return None


# =============================================================================
# EMAIL VALIDATION
# =============================================================================

def validate_email(email_string: Optional[str]) -> Optional[str]:
    """
    Validate and normalize an email address.

    Performs basic validation and cleaning:
    - Must contain @ and at least one dot after @
    - Strips whitespace
    - Converts to lowercase
    - Removes common invalid patterns

    Note: This is intentionally permissive. We do basic format checking
    but don't reject valid-looking emails. Real validation happens when
    we actually send emails.

    Args:
        email_string: Raw email string (can be messy)

    Returns:
        Cleaned, lowercase email address
        None if the email is invalid or missing
    """
    # Handle empty/None values
    if not email_string or not isinstance(email_string, str):
        return None

    # Clean up whitespace
    email = email_string.strip()

    # Convert to lowercase (emails are case-insensitive)
    email = email.lower()

    # Remove any surrounding quotes or brackets
    email = email.strip('"\'<>[](){}')

    # Check for empty after cleaning
    if not email:
        return None

    # Check for placeholder values that aren't real emails
    invalid_patterns = [
        'n/a', 'na', 'none', 'null', 'undefined', 'no email',
        'noemail', 'no-email', 'test', 'example', 'sample',
        'xxx', '---', '...', 'unknown', 'no@email', 'no@no',
    ]
    if email in invalid_patterns:
        return None

    # Basic format validation
    # Must have exactly one @
    if email.count('@') != 1:
        return None

    # Split into local and domain parts
    local_part, domain_part = email.split('@')

    # Both parts must be non-empty
    if not local_part or not domain_part:
        return None

    # Domain must have at least one dot (e.g., example.com)
    if '.' not in domain_part:
        return None

    # Domain can't start or end with a dot
    if domain_part.startswith('.') or domain_part.endswith('.'):
        return None

    # Check for obviously invalid TLDs
    tld = domain_part.split('.')[-1]
    if len(tld) < 2:  # TLDs are at least 2 characters
        return None

    # Basic character validation using regex
    # Allow most characters but catch obvious issues
    email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_regex, email):
        # Be permissive - some valid emails have unusual characters
        # At minimum, check no spaces
        if ' ' in email:
            return None

    return email


# =============================================================================
# DATE PARSING
# =============================================================================

def parse_date(date_string: Optional[str]) -> Optional[datetime]:
    """
    Parse a date string in any format to a datetime object.

    Handles various formats from different booking systems:
    - 2024-01-15 (ISO format)
    - 01/15/2024 (US format)
    - 15/01/2024 (European format)
    - Jan 15, 2024
    - 15-Jan-2024
    - January 15, 2024
    - 2024-01-15T10:30:00 (ISO with time)

    Args:
        date_string: Raw date string

    Returns:
        datetime object or None if parsing fails
    """
    if not date_string or not isinstance(date_string, str):
        return None

    date_string = date_string.strip()

    if not date_string:
        return None

    # Check for placeholder values
    if date_string.lower() in ['n/a', 'na', 'none', 'null', '-', '--', '']:
        return None

    try:
        # dateutil.parser is very flexible and handles most formats
        # dayfirst=False assumes US format (month first) for ambiguous dates
        parsed = date_parser.parse(date_string, dayfirst=False, fuzzy=True)
        return parsed
    except (ParserError, ValueError, OverflowError):
        return None


# =============================================================================
# MAIN PARSING FUNCTION
# =============================================================================

def parse_and_validate(file_path: str) -> dict:
    """
    Main function to parse, validate, and extract customer data from a CSV file.

    This orchestrates the entire parsing process:
    1. Detect file encoding
    2. Detect delimiter
    3. Parse CSV into DataFrame
    4. Map columns to standard fields
    5. Validate and clean each row
    6. Return structured results with error reporting

    Args:
        file_path: Path to the CSV file

    Returns:
        Dictionary with:
        - success: bool - whether parsing succeeded
        - customers: list - valid customer records
        - errors: list - error details for invalid rows
        - summary: dict - statistics about the parsing
        - column_mapping: dict - how columns were mapped
    """
    result = {
        'success': False,
        'customers': [],
        'errors': [],
        'summary': {
            'total_rows': 0,
            'valid_rows': 0,
            'invalid_rows': 0,
            'skipped_rows': 0,  # Rows with neither email nor phone
        },
        'column_mapping': {},
    }

    try:
        # Step 1 & 2: Parse CSV (encoding and delimiter auto-detected)
        df = parse_csv(file_path)

        result['summary']['total_rows'] = len(df)

        # Handle empty file
        if len(df) == 0:
            result['success'] = True
            result['errors'].append({
                'row': 0,
                'reason': 'CSV file is empty or contains only headers'
            })
            return result

        # Step 3: Map columns to our standard fields
        mapping = map_columns(df)
        result['column_mapping'] = mapping

        # Check if we have at least some useful columns
        has_contact_info = mapping['email'] or mapping['phone']
        has_name = mapping['name'] or mapping['first_name']

        if not has_contact_info:
            result['errors'].append({
                'row': 0,
                'reason': 'Could not identify email or phone columns in CSV'
            })
            return result

        # Step 4: Process each row
        for idx, row in df.iterrows():
            row_number = idx + 2  # +2 because: 0-indexed + header row

            try:
                # Extract name
                name = None
                if mapping['name']:
                    name = row.get(mapping['name'], '').strip()
                elif mapping['first_name']:
                    # Combine first and last name
                    first = row.get(mapping['first_name'], '').strip()
                    last = row.get(mapping['last_name'], '').strip() if mapping['last_name'] else ''
                    name = f"{first} {last}".strip()

                # Clean up name
                if name:
                    # Remove extra whitespace
                    name = ' '.join(name.split())
                    # Check for placeholder names
                    if name.lower() in ['n/a', 'na', 'none', 'unknown', '-', 'guest', 'walk-in', 'walkin']:
                        name = None

                # Extract and validate email
                raw_email = None
                if mapping['email']:
                    raw_email = row.get(mapping['email'], '')
                validated_email = validate_email(raw_email)

                # Extract and validate phone
                raw_phone = None
                if mapping['phone']:
                    raw_phone = row.get(mapping['phone'], '')
                validated_phone = validate_phone(raw_phone)

                # Extract and parse date
                parsed_date = None
                if mapping['date']:
                    raw_date = row.get(mapping['date'], '')
                    parsed_date = parse_date(raw_date)

                # Skip row if no valid contact information
                if not validated_email and not validated_phone:
                    result['summary']['skipped_rows'] += 1
                    result['errors'].append({
                        'row': row_number,
                        'reason': 'No valid email or phone number',
                        'raw_email': raw_email,
                        'raw_phone': raw_phone,
                    })
                    continue

                # Build customer record
                customer = {
                    'name': name,
                    'email': validated_email,
                    'phone': validated_phone,
                    'last_visit': parsed_date.isoformat() if parsed_date else None,
                    'source_row': row_number,
                }

                result['customers'].append(customer)
                result['summary']['valid_rows'] += 1

            except Exception as e:
                # Catch any unexpected errors processing this row
                result['summary']['invalid_rows'] += 1
                result['errors'].append({
                    'row': row_number,
                    'reason': f'Error processing row: {str(e)}',
                })

        result['success'] = True

    except FileNotFoundError:
        result['errors'].append({
            'row': 0,
            'reason': f'File not found: {file_path}'
        })
    except ValueError as e:
        result['errors'].append({
            'row': 0,
            'reason': str(e)
        })
    except Exception as e:
        result['errors'].append({
            'row': 0,
            'reason': f'Unexpected error: {str(e)}'
        })

    return result


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def preview_csv(file_path: str, num_rows: int = 5) -> dict:
    """
    Preview a CSV file without full validation.

    Useful for showing users what was detected before processing.

    Args:
        file_path: Path to the CSV file
        num_rows: Number of rows to preview

    Returns:
        Dictionary with encoding, delimiter, columns, sample rows, and suggested mapping
    """
    try:
        encoding = detect_encoding(file_path)
        delimiter = detect_delimiter(file_path, encoding)
        df = parse_csv(file_path)
        mapping = map_columns(df)

        # Convert sample rows to list of dicts
        sample_rows = df.head(num_rows).to_dict('records')

        return {
            'success': True,
            'encoding': encoding,
            'delimiter': repr(delimiter),  # Show \t as readable
            'columns': list(df.columns),
            'total_rows': len(df),
            'sample_rows': sample_rows,
            'suggested_mapping': mapping,
        }

    except Exception as e:
        return {
            'success': False,
            'error': str(e),
        }
