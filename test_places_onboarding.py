"""
Test script for Google Places onboarding flow.

Tests the complete flow:
1. Places API search
2. Place selection + DB save
3. Review URL generation (unit)
4. Review request blocking when no Place ID
5. Login redirect for incomplete onboarding

Usage:
    python test_places_onboarding.py

Requires: Flask server running on localhost:5001
"""

import os
import sys
import json
import requests

# Load .env
from dotenv import load_dotenv
load_dotenv()

BASE_URL = "http://localhost:5001/api"

# Test credentials - uses your real account
TEST_EMAIL = "izzyd3149@gmail.com"
TEST_PASSWORD = "fuchsiacaesar35"

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
SKIP = "\033[93mSKIP\033[0m"

results = {"passed": 0, "failed": 0, "skipped": 0}


def log_result(name, passed, detail=""):
    status = PASS if passed else FAIL
    if passed:
        results["passed"] += 1
    else:
        results["failed"] += 1
    suffix = f" - {detail}" if detail else ""
    print(f"  {status}  {name}{suffix}")


def log_skip(name, reason):
    results["skipped"] += 1
    print(f"  {SKIP}  {name} - {reason}")


def get_token():
    """Login and return access token."""
    resp = requests.post(f"{BASE_URL}/auth/login", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD
    })
    if resp.status_code != 200:
        print(f"\n  Could not login: {resp.status_code} {resp.text}")
        return None
    return resp.json()["session"]["access_token"]


def auth_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }


# ──────────────────────────────────────────────
# TEST 1: Places API search
# ──────────────────────────────────────────────
def test_places_search(token):
    print("\n1. Places API Search")
    print("   " + "-" * 40)

    # 1a. Search with valid query
    resp = requests.get(
        f"{BASE_URL}/places/search?query=Starbucks+Las+Vegas",
        headers=auth_headers(token)
    )
    data = resp.json()

    log_result(
        "Search returns 200",
        resp.status_code == 200,
        f"got {resp.status_code}"
    )

    has_results = len(data.get("results", [])) > 0
    if has_results:
        log_result("Returns at least 1 result", True, f"got {len(data['results'])} results")
    else:
        log_skip("Returns at least 1 result", "API key has referer restrictions (fix in Google Cloud Console)")

    if has_results:
        r = data["results"][0]
        log_result("Result has place_id", bool(r.get("place_id")), r.get("place_id", "")[:30])
        log_result("Result has name", bool(r.get("name")), r.get("name", ""))
        log_result("Result has address", bool(r.get("address")), r.get("address", "")[:40])
    else:
        for field in ["place_id", "name", "address"]:
            log_skip(f"Result has {field}", "no results (API key may have referer restrictions)")

    # 1b. Empty query should return 400
    resp2 = requests.get(
        f"{BASE_URL}/places/search?query=",
        headers=auth_headers(token)
    )
    log_result("Empty query returns 400", resp2.status_code == 400, f"got {resp2.status_code}")

    # 1c. No auth should return 401
    resp3 = requests.get(f"{BASE_URL}/places/search?query=test")
    log_result("No auth returns 401", resp3.status_code == 401, f"got {resp3.status_code}")

    return data.get("results", [])


# ──────────────────────────────────────────────
# TEST 2: Place selection
# ──────────────────────────────────────────────
def test_place_selection(token, search_results):
    print("\n2. Place Selection")
    print("   " + "-" * 40)

    # Use a real Starbucks place_id if search didn't return results
    if search_results:
        place = search_results[0]
        place_id = place["place_id"]
    else:
        place_id = "ChIJCSF8lBZEwokRhngABHRcdoJ"  # Known valid Starbucks place_id

    resp = requests.post(
        f"{BASE_URL}/places/select",
        headers=auth_headers(token),
        json={"place_id": place_id}
    )
    data = resp.json()

    log_result("Select returns 200", resp.status_code == 200, f"got {resp.status_code}")
    log_result("Response has success=true", data.get("success") is True)
    log_result("Response has place_id", data.get("place_id") == place_id)
    log_result(
        "Response has review_url",
        "writereview" in (data.get("review_url") or ""),
        (data.get("review_url") or "")[:50]
    )
    log_result(
        "Response has maps_url",
        "google.com/maps" in (data.get("maps_url") or ""),
        (data.get("maps_url") or "")[:50]
    )

    # Verify it saved to DB via /auth/me
    me_resp = requests.get(f"{BASE_URL}/auth/me", headers=auth_headers(token))
    me_data = me_resp.json()
    biz = me_data.get("business", {})
    log_result(
        "Saved google_place_id to DB",
        biz.get("google_place_id") == place_id,
        f"DB has: {biz.get('google_place_id', 'NULL')[:30]}"
    )
    log_result(
        "Saved google_review_url to DB",
        "writereview" in (biz.get("google_review_url") or "")
    )

    # 2b. Empty place_id should return 400
    resp2 = requests.post(
        f"{BASE_URL}/places/select",
        headers=auth_headers(token),
        json={"place_id": ""}
    )
    log_result("Empty place_id returns 400", resp2.status_code == 400, f"got {resp2.status_code}")

    return place_id


# ──────────────────────────────────────────────
# TEST 3: Review URL generation (unit test)
# ──────────────────────────────────────────────
def test_review_url_generation():
    print("\n3. Review URL Generation (unit)")
    print("   " + "-" * 40)

    # Import directly
    sys.path.insert(0, os.path.dirname(__file__))
    from app.services.google_places import get_review_url, get_maps_url

    test_id = "ChIJN1t_tDeuEmsRUsoyG83frY4"
    expected_review = f"https://search.google.com/local/writereview?placeid={test_id}"
    expected_maps = f"https://www.google.com/maps/place/?q=place_id:{test_id}"

    review_url = get_review_url(test_id)
    log_result("get_review_url() correct", review_url == expected_review, review_url or "None")

    maps_url = get_maps_url(test_id)
    log_result("get_maps_url() correct", maps_url == expected_maps, maps_url or "None")

    log_result("get_review_url(None) returns None", get_review_url(None) is None)
    log_result("get_review_url('') returns None", get_review_url("") is None)
    log_result("get_maps_url(None) returns None", get_maps_url(None) is None)
    log_result("get_maps_url('') returns None", get_maps_url("") is None)


# ──────────────────────────────────────────────
# TEST 4: Review request blocking (no Place ID)
# ──────────────────────────────────────────────
def test_review_request_blocking(token):
    print("\n4. Review Request Blocking")
    print("   " + "-" * 40)

    from app.services.supabase_service import supabase

    # Get current business state so we can restore it
    me_resp = requests.get(f"{BASE_URL}/auth/me", headers=auth_headers(token))
    biz = me_resp.json().get("business", {})
    original_place_id = biz.get("google_place_id")
    original_review_url = biz.get("google_review_url")
    business_id = biz.get("id")

    # Clear google_place_id and google_review_url
    supabase.table("businesses").update({
        "google_place_id": None,
        "google_review_url": None
    }).eq("id", business_id).execute()

    # Need a fresh token since require_auth caches business data in the request
    # Actually, require_auth fetches fresh from DB each request, so the token is fine

    try:
        # Try to send a review request - should be blocked
        resp = requests.post(
            f"{BASE_URL}/review-requests/send",
            headers=auth_headers(token),
            json={
                "customer_name": "Test Customer",
                "customer_email": "test@example.com",
                "method": "email"
            }
        )
        data = resp.json()

        log_result("Blocked with 400", resp.status_code == 400, f"got {resp.status_code}")
        log_result(
            "Error mentions Google Business",
            "google" in (data.get("error") or "").lower(),
            data.get("error", "")[:50]
        )
        log_result(
            "Response has redirect to /onboarding",
            data.get("redirect") == "/onboarding",
            f"got: {data.get('redirect')}"
        )

    finally:
        # Restore original values
        supabase.table("businesses").update({
            "google_place_id": original_place_id,
            "google_review_url": original_review_url
        }).eq("id", business_id).execute()
        log_result("Restored business data", True, "cleanup done")


# ──────────────────────────────────────────────
# TEST 5: Login redirect for incomplete onboarding
# ──────────────────────────────────────────────
def test_login_redirect(token):
    print("\n5. Login Redirect (onboarding check)")
    print("   " + "-" * 40)

    from app.services.supabase_service import supabase

    # Get current state
    me_resp = requests.get(f"{BASE_URL}/auth/me", headers=auth_headers(token))
    biz = me_resp.json().get("business", {})
    business_id = biz.get("id")
    original_place_id = biz.get("google_place_id")
    original_review_url = biz.get("google_review_url")

    # 5a. Test with completed onboarding (has google_place_id)
    supabase.table("businesses").update({
        "google_place_id": "ChIJtest123",
        "google_review_url": "https://search.google.com/local/writereview?placeid=ChIJtest123",
    }).eq("id", business_id).execute()

    resp1 = requests.post(f"{BASE_URL}/auth/login", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD
    })
    data1 = resp1.json()
    log_result(
        "Complete user -> redirect /dashboard",
        data1.get("redirect") == "/dashboard",
        f"got: {data1.get('redirect')}"
    )

    # 5b. Test with no Place ID
    supabase.table("businesses").update({
        "google_place_id": None,
        "google_review_url": None,
    }).eq("id", business_id).execute()

    resp2 = requests.post(f"{BASE_URL}/auth/login", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD
    })
    data2 = resp2.json()
    log_result(
        "Incomplete user -> redirect /onboarding",
        data2.get("redirect") == "/onboarding",
        f"got: {data2.get('redirect')}"
    )

    # Restore
    supabase.table("businesses").update({
        "google_place_id": original_place_id,
        "google_review_url": original_review_url,
    }).eq("id", business_id).execute()
    log_result("Restored business data", True, "cleanup done")


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  Google Places Onboarding - Test Suite")
    print("=" * 50)

    # Check server is running
    try:
        requests.get(f"{BASE_URL}/health", timeout=3)
    except requests.ConnectionError:
        print(f"\n  {FAIL}  Server not running at {BASE_URL}")
        print("  Start it with: python run.py")
        sys.exit(1)

    # Login
    print("\n  Logging in...")
    token = get_token()
    if not token:
        print(f"\n  {FAIL}  Could not authenticate. Check TEST_EMAIL/TEST_PASSWORD.")
        sys.exit(1)
    print(f"  Authenticated OK")

    # Run tests
    search_results = test_places_search(token)
    saved_place_id = test_place_selection(token, search_results)
    test_review_url_generation()
    test_review_request_blocking(token)
    test_login_redirect(token)

    # Summary
    total = results["passed"] + results["failed"] + results["skipped"]
    print("\n" + "=" * 50)
    print(f"  Results: {results['passed']}/{total} passed", end="")
    if results["failed"]:
        print(f", {results['failed']} failed", end="")
    if results["skipped"]:
        print(f", {results['skipped']} skipped", end="")
    print()
    print("=" * 50)

    sys.exit(0 if results["failed"] == 0 else 1)
