"""
Reviews API endpoints - example of how to use Supabase in routes.

This file shows the pattern for creating CRUD (Create, Read, Update, Delete) endpoints.
You'll need to create a 'reviews' table in your Supabase dashboard first!
"""

from flask import Blueprint, jsonify, request
from app.services.supabase_service import supabase

# Create a Blueprint for review-related routes
reviews_bp = Blueprint('reviews', __name__)


@reviews_bp.route('/reviews', methods=['GET'])
def get_reviews():
    """
    Get all reviews from the database.

    Example: GET /api/reviews
    """
    try:
        # Query all rows from the 'reviews' table
        response = supabase.table("reviews").select("*").execute()
        return jsonify(response.data), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@reviews_bp.route('/reviews/<int:review_id>', methods=['GET'])
def get_review(review_id):
    """
    Get a single review by ID.

    Example: GET /api/reviews/123
    """
    try:
        response = supabase.table("reviews").select("*").eq("id", review_id).execute()

        if not response.data:
            return jsonify({"error": "Review not found"}), 404

        return jsonify(response.data[0]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@reviews_bp.route('/reviews', methods=['POST'])
def create_review():
    """
    Create a new review.

    Example: POST /api/reviews
    Body: {"title": "Great product!", "content": "I love it", "rating": 5}
    """
    try:
        # Get JSON data from request body
        data = request.get_json()

        # Validate required fields
        if not data:
            return jsonify({"error": "No data provided"}), 400

        # Insert into Supabase
        response = supabase.table("reviews").insert(data).execute()

        return jsonify(response.data[0]), 201  # 201 = Created
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@reviews_bp.route('/reviews/<int:review_id>', methods=['PUT'])
def update_review(review_id):
    """
    Update an existing review.

    Example: PUT /api/reviews/123
    Body: {"rating": 4}
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "No data provided"}), 400

        # Update the row where id matches
        response = supabase.table("reviews").update(data).eq("id", review_id).execute()

        if not response.data:
            return jsonify({"error": "Review not found"}), 404

        return jsonify(response.data[0]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@reviews_bp.route('/reviews/<int:review_id>', methods=['DELETE'])
def delete_review(review_id):
    """
    Delete a review.

    Example: DELETE /api/reviews/123
    """
    try:
        response = supabase.table("reviews").delete().eq("id", review_id).execute()

        if not response.data:
            return jsonify({"error": "Review not found"}), 404

        return jsonify({"message": "Review deleted"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
