"""
Test endpoint to verify Supabase database connection.
"""

from flask import Blueprint, jsonify
from app.services.supabase_service import supabase

test_bp = Blueprint('test', __name__)


@test_bp.route('/test-db', methods=['GET'])
def test_database():
    """
    Test endpoint that fetches all businesses from Supabase.

    Returns:
        JSON array of businesses (empty array if none exist)
    """
    try:
        response = supabase.table("businesses").select("*").execute()
        return jsonify(response.data), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
