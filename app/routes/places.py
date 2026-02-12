"""
Google Places API endpoints for business lookup.

- GET  /api/places/search  - Search for businesses by name
- POST /api/places/select  - Save a selected Place ID to the business
"""

import os
import requests as http_requests

from flask import Blueprint, jsonify, request
from app.services.auth_service import require_auth
from app.services.supabase_service import supabase

places_bp = Blueprint('places', __name__)

GOOGLE_PLACES_API_KEY = os.getenv('GOOGLE_PLACES_API_KEY')


@places_bp.route('/places/search', methods=['GET'])
@require_auth
def search_places():
    query = request.args.get('query', '').strip()
    if not query:
        return jsonify({"error": "Query parameter is required"}), 400

    try:
        resp = http_requests.get(
            'https://maps.googleapis.com/maps/api/place/textsearch/json',
            params={
                'query': query,
                'type': 'establishment',
                'key': GOOGLE_PLACES_API_KEY,
            },
            timeout=10,
        )
        data = resp.json()

        if data.get('status') not in ('OK', 'ZERO_RESULTS'):
            return jsonify({"error": "Search unavailable, please try again"}), 500

        results = [
            {
                "place_id": r['place_id'],
                "name": r.get('name', ''),
                "address": r.get('formatted_address', ''),
                "rating": r.get('rating'),
                "total_ratings": r.get('user_ratings_total'),
            }
            for r in data.get('results', [])[:5]
        ]

        return jsonify({"results": results}), 200

    except Exception:
        return jsonify({"error": "Search unavailable, please try again"}), 500


@places_bp.route('/places/select', methods=['POST'])
@require_auth
def select_place():
    body = request.get_json()
    if not body or not body.get('place_id', '').strip():
        return jsonify({"error": "place_id is required"}), 400

    place_id = body['place_id'].strip()
    business_id = request.business.get('id')

    review_url = f"https://search.google.com/local/writereview?placeid={place_id}"
    maps_url = f"https://www.google.com/maps/place/?q=place_id:{place_id}"

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
