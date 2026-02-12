"""
Google Places API endpoints for business lookup.

- GET  /api/places/search  - Search for businesses by name
- POST /api/places/select  - Save a selected Place ID to the business
"""

from flask import Blueprint, jsonify, request
from app.services.auth_service import require_auth
from app.services.supabase_service import supabase
from app.services.google_places import search_places as places_search, get_review_url, get_maps_url

places_bp = Blueprint('places', __name__)


@places_bp.route('/places/search', methods=['GET'])
@require_auth
def search_places():
    query = request.args.get('query', '').strip()
    if not query:
        return jsonify({"error": "Query parameter is required"}), 400

    results = places_search(query)
    return jsonify({"results": results}), 200


@places_bp.route('/places/select', methods=['POST'])
@require_auth
def select_place():
    body = request.get_json()
    if not body or not body.get('place_id', '').strip():
        return jsonify({"error": "place_id is required"}), 400

    place_id = body['place_id'].strip()
    business_id = request.business.get('id')

    review_url = get_review_url(place_id)
    maps_url = get_maps_url(place_id)

    try:
        result = supabase.table('businesses').update({
            'google_place_id': place_id,
            'google_review_url': review_url,
            'onboarding_step': 2,
        }).eq('id', business_id).execute()

        if not result.data:
            return jsonify({"error": "Business not found"}), 404

        return jsonify({
            "success": True,
            "place_id": place_id,
            "review_url": review_url,
            "maps_url": maps_url,
        }), 200

    except Exception:
        return jsonify({"error": "Failed to save selection, please try again"}), 500
