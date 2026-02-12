"""
Google Places API helper functions.

Provides utilities for searching businesses, getting place details,
and building Google review/maps URLs.
"""

import os
import logging
import requests as http_requests

logger = logging.getLogger(__name__)

GOOGLE_PLACES_API_KEY = os.getenv('GOOGLE_PLACES_API_KEY')


def get_review_url(place_id):
    """Build a Google review URL for a given Place ID."""
    if not place_id:
        return None
    return f"https://search.google.com/local/writereview?placeid={place_id}"


def get_maps_url(place_id):
    """Build a Google Maps URL for a given Place ID."""
    if not place_id:
        return None
    return f"https://www.google.com/maps/place/?q=place_id:{place_id}"


def search_places(query):
    """
    Search Google Places API for businesses matching a query.

    Args:
        query: Search string (e.g. "Bella Hair Salon Las Vegas")

    Returns:
        list of dicts with place_id, name, address, rating, total_ratings.
        Empty list on error or no results.
    """
    if not query or not query.strip():
        return []

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
            logger.error(f"Places search failed: status={data.get('status')}, error={data.get('error_message')}")
            return []

        return [
            {
                "place_id": r['place_id'],
                "name": r.get('name', ''),
                "address": r.get('formatted_address', ''),
                "rating": r.get('rating'),
                "total_ratings": r.get('user_ratings_total'),
            }
            for r in data.get('results', [])[:5]
        ]

    except Exception as e:
        logger.exception(f"Places search error for query '{query}': {e}")
        return []


def get_place_details(place_id):
    """
    Get detailed info for a specific Place ID from Google Places API.

    Args:
        place_id: A Google Place ID string

    Returns:
        dict with name, address, rating, phone, website, total_ratings.
        None on error or invalid place_id.
    """
    if not place_id:
        return None

    try:
        resp = http_requests.get(
            'https://maps.googleapis.com/maps/api/place/details/json',
            params={
                'place_id': place_id,
                'fields': 'name,formatted_address,rating,user_ratings_total,formatted_phone_number,website',
                'key': GOOGLE_PLACES_API_KEY,
            },
            timeout=10,
        )
        data = resp.json()

        if data.get('status') != 'OK':
            logger.error(f"Place details failed for {place_id}: status={data.get('status')}, error={data.get('error_message')}")
            return None

        result = data.get('result', {})
        return {
            "name": result.get('name', ''),
            "address": result.get('formatted_address', ''),
            "rating": result.get('rating'),
            "total_ratings": result.get('user_ratings_total'),
            "phone": result.get('formatted_phone_number'),
            "website": result.get('website'),
        }

    except Exception as e:
        logger.exception(f"Place details error for {place_id}: {e}")
        return None
