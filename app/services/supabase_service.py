"""
Supabase service - handles connection to Supabase database.

Supabase is a backend-as-a-service that provides:
- PostgreSQL database
- Authentication
- Real-time subscriptions
- File storage
- Edge functions

This service creates two connections:
1. `supabase` - Uses anon key, respects Row Level Security (RLS)
   Use for operations where the user context matters

2. `supabase_admin` - Uses service_role key, bypasses RLS
   Use for backend operations like queue processing, webhooks, etc.
   BE CAREFUL: This can access ALL data regardless of RLS policies!
"""

import os
from supabase import create_client, Client
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get credentials from environment variables
# NEVER hardcode credentials in your code!
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

# Validate that credentials exist
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError(
        "Missing Supabase credentials! "
        "Make sure SUPABASE_URL and SUPABASE_KEY are set in your .env file"
    )

# Create the standard Supabase client (respects RLS)
# This is the main object you'll use to interact with Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Create the admin client (bypasses RLS) - for backend/server operations
# This is used for queue processing, webhooks, and other server-side tasks
if SUPABASE_SERVICE_ROLE_KEY:
    supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
else:
    # Fall back to regular client if service role key not configured
    # This means RLS will still apply, which may cause issues with queue processing
    import logging
    logging.warning(
        "SUPABASE_SERVICE_ROLE_KEY not set! "
        "Queue processor and webhooks may not work correctly due to RLS policies. "
        "Get the service_role key from your Supabase dashboard > Settings > API"
    )
    supabase_admin: Client = supabase


# ============================================================================
# USAGE EXAMPLES (for your reference)
# ============================================================================

# --- READING DATA (SELECT) ---
# Get all rows from a table:
#   response = supabase.table("reviews").select("*").execute()
#   data = response.data  # List of dictionaries
#
# Get specific columns:
#   response = supabase.table("reviews").select("id, title, rating").execute()
#
# Filter with WHERE clause:
#   response = supabase.table("reviews").select("*").eq("status", "published").execute()
#
# Multiple filters:
#   response = supabase.table("reviews").select("*").eq("status", "published").gte("rating", 4).execute()

# --- INSERTING DATA (INSERT) ---
# Insert a single row:
#   response = supabase.table("reviews").insert({"title": "Great!", "rating": 5}).execute()
#
# Insert multiple rows:
#   response = supabase.table("reviews").insert([
#       {"title": "Great!", "rating": 5},
#       {"title": "Good", "rating": 4}
#   ]).execute()

# --- UPDATING DATA (UPDATE) ---
# Update rows matching a condition:
#   response = supabase.table("reviews").update({"status": "published"}).eq("id", 123).execute()

# --- DELETING DATA (DELETE) ---
# Delete rows matching a condition:
#   response = supabase.table("reviews").delete().eq("id", 123).execute()

# --- COMMON FILTER METHODS ---
# .eq("column", value)      - equals
# .neq("column", value)     - not equals
# .gt("column", value)      - greater than
# .gte("column", value)     - greater than or equal
# .lt("column", value)      - less than
# .lte("column", value)     - less than or equal
# .like("column", "%text%") - pattern matching
# .ilike("column", "%text%")- case-insensitive pattern matching
# .is_("column", None)      - is null
# .in_("column", [1, 2, 3]) - in list
# .order("column")          - order by (ascending)
# .order("column", desc=True) - order by (descending)
# .limit(10)                - limit results
# .range(0, 9)              - pagination (get rows 0-9)
