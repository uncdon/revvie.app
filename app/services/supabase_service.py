"""
Supabase service - handles connection to Supabase database.

Supabase is a backend-as-a-service that provides:
- PostgreSQL database
- Authentication
- Real-time subscriptions
- File storage
- Edge functions

This service creates a single connection that can be imported anywhere in your app.
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

# Validate that credentials exist
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError(
        "Missing Supabase credentials! "
        "Make sure SUPABASE_URL and SUPABASE_KEY are set in your .env file"
    )

# Create the Supabase client
# This is the main object you'll use to interact with Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


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
