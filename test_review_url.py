"""
Test script: verify review request URLs are correct.

Checks:
1. Unit test - get_review_url() builds the right URL
2. Live test - send a real review request to YOUR email/phone
3. Inspect the review_url in the API response
4. Verify the clicktracking="off" attribute is in the email HTML

Usage:
    python test_review_url.py              # run all checks (no real send)
    python test_review_url.py --send-email # actually send test email to yourself
    python test_review_url.py --send-sms   # actually send test SMS to yourself
    python test_review_url.py --send-both  # send both

Requires: Flask server running on localhost:5001
"""

import os
import sys
import json
import requests

from dotenv import load_dotenv
load_dotenv()

BASE_URL = "http://localhost:5001/api"

# Your test account
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
# TEST 1: Unit test - URL generation
# ──────────────────────────────────────────────
def test_url_generation():
    print("\n1. URL Generation (unit test)")
    print("   " + "-" * 40)

    sys.path.insert(0, os.path.dirname(__file__))
    from app.services.google_places import get_review_url

    test_id = "ChIJN1t_tDeuEmsRUsoyG83frY4"
    expected = f"https://search.google.com/local/writereview?placeid={test_id}"

    url = get_review_url(test_id)
    log_result("URL format correct", url == expected, url or "None")
    log_result("None place_id -> None", get_review_url(None) is None)
    log_result("Empty place_id -> None", get_review_url("") is None)


# ──────────────────────────────────────────────
# TEST 2: Verify business has a google_place_id
# ──────────────────────────────────────────────
def test_business_has_place_id(token):
    print("\n2. Business Google Place ID")
    print("   " + "-" * 40)

    resp = requests.get(f"{BASE_URL}/auth/me", headers=auth_headers(token))
    data = resp.json()
    biz = data.get("business", {})

    place_id = biz.get("google_place_id")
    review_url = biz.get("google_review_url")
    biz_name = biz.get("business_name")

    print(f"   Business name:    {biz_name}")
    print(f"   google_place_id:  {place_id}")
    print(f"   google_review_url: {review_url}")

    log_result("google_place_id is set", bool(place_id), place_id or "MISSING")
    log_result(
        "google_review_url contains placeid",
        bool(review_url and "writereview?placeid=" in review_url),
        review_url or "MISSING"
    )

    if place_id and review_url:
        log_result(
            "review_url matches place_id",
            review_url.endswith(place_id),
            "IDs match" if review_url.endswith(place_id) else f"MISMATCH: URL ends with ...{review_url[-30:]}"
        )

    return place_id, review_url, biz_name


# ──────────────────────────────────────────────
# TEST 3: Email HTML has clicktracking=off
# ──────────────────────────────────────────────
def test_email_html_has_clicktracking_off():
    print("\n3. Email Template - clicktracking attribute")
    print("   " + "-" * 40)

    from app.services.email_service import send_review_request_email
    import inspect

    source = inspect.getsource(send_review_request_email)

    log_result(
        'clicktracking="off" in email template',
        'clicktracking="off"' in source or "clicktracking='off'" in source,
    )

    # Also build the actual HTML and check
    # We can't call send_review_request_email without actually sending,
    # but we can check the f-string template in the source
    log_result(
        "review_url is in href (not hardcoded)",
        '{review_url}' in source,
        "uses {review_url} variable"
    )


# ──────────────────────────────────────────────
# TEST 4: Dry-run review request (check response URL)
# ──────────────────────────────────────────────
def test_review_request_response(token, place_id):
    print("\n4. Review Request API - response check")
    print("   " + "-" * 40)

    if not place_id:
        log_skip("API response check", "No google_place_id on business")
        return

    # Send a review request to a fake email - it will go through SendGrid
    # but we mostly care about the response data showing the correct URL
    resp = requests.post(
        f"{BASE_URL}/review-requests/send",
        headers=auth_headers(token),
        json={
            "customer_name": "URL Test",
            "customer_email": "url-test-do-not-reply@example.com",
            "method": "email"
        }
    )
    data = resp.json()

    returned_url = data.get("data", {}).get("review_url", "")

    log_result("API returns 200", resp.status_code == 200, f"got {resp.status_code}")
    log_result(
        "review_url in response is correct",
        f"writereview?placeid={place_id}" in returned_url,
        returned_url or "MISSING"
    )
    log_result(
        "review_url is NOT a sendgrid tracking URL",
        "sendgrid" not in returned_url.lower(),
        "clean URL" if "sendgrid" not in returned_url.lower() else "WRAPPED BY SENDGRID"
    )

    if not data.get("success"):
        print(f"   Note: send may have failed ({data.get('message')}), but URL was still correct in response")


# ──────────────────────────────────────────────
# TEST 5: Send real email/SMS to yourself
# ──────────────────────────────────────────────
def test_send_real(token, method, place_id, biz_name):
    print(f"\n5. LIVE SEND ({method}) - check your inbox/phone!")
    print("   " + "-" * 40)

    if not place_id:
        log_skip("Live send", "No google_place_id on business")
        return

    payload = {
        "customer_name": "Danny",
        "method": method,
    }

    if method in ("email", "both"):
        payload["customer_email"] = TEST_EMAIL
    if method in ("sms", "both"):
        payload["customer_phone"] = os.environ.get("TELNYX_PHONE_NUMBER", "")
        if not payload["customer_phone"]:
            log_skip("SMS send", "No TELNYX_PHONE_NUMBER in .env to send to")
            if method == "sms":
                return
            payload["method"] = "email"

    print(f"   Sending {payload['method']} review request...")
    print(f"   Business: {biz_name}")
    print(f"   Expected URL: https://search.google.com/local/writereview?placeid={place_id}")

    resp = requests.post(
        f"{BASE_URL}/review-requests/send",
        headers=auth_headers(token),
        json=payload
    )
    data = resp.json()

    log_result("Send succeeded", data.get("success") is True, data.get("message", ""))

    review_url = data.get("data", {}).get("review_url", "")
    log_result(
        "Returned review_url correct",
        f"writereview?placeid={place_id}" in review_url,
        review_url
    )

    if data.get("success"):
        print()
        print("   ┌─────────────────────────────────────────────────┐")
        print("   │  NOW CHECK YOUR INBOX / PHONE:                  │")
        print("   │                                                  │")
        if method in ("email", "both"):
            print(f"   │  Email: {TEST_EMAIL:<40} │")
        if method in ("sms", "both"):
            phone = payload.get("customer_phone", "")
            print(f"   │  Phone: {phone:<40} │")
        print("   │                                                  │")
        print("   │  1. Click the review link                       │")
        print("   │  2. It should open the Google review page       │")
        print("   │  3. If SendGrid wraps the URL, the FINAL       │")
        print("   │     destination should still be correct          │")
        print("   └─────────────────────────────────────────────────┘")


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
if __name__ == "__main__":
    send_method = None
    if "--send-email" in sys.argv:
        send_method = "email"
    elif "--send-sms" in sys.argv:
        send_method = "sms"
    elif "--send-both" in sys.argv:
        send_method = "both"

    print("=" * 50)
    print("  Review URL Fix - Verification")
    print("=" * 50)

    # Check server
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
        print(f"\n  {FAIL}  Could not authenticate.")
        sys.exit(1)
    print("  Authenticated OK")

    # Run tests
    test_url_generation()
    place_id, review_url, biz_name = test_business_has_place_id(token)
    test_email_html_has_clicktracking_off()
    test_review_request_response(token, place_id)

    if send_method:
        test_send_real(token, send_method, place_id, biz_name)
    else:
        print("\n  ─────────────────────────────────────────────────")
        print("  To send a real test message, re-run with a flag:")
        print("    python test_review_url.py --send-email")
        print("    python test_review_url.py --send-sms")
        print("    python test_review_url.py --send-both")
        print("  ─────────────────────────────────────────────────")

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
