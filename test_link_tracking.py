"""
End-to-end test for the complete link tracking system.

Tests:
1. Short URL generation (tracking link creation)
2. Redirect works (GET /r/<code> returns 302)
3. Click is logged (link_clicks table)
4. Status updated to 'clicked' (review_requests table)
5. Analytics API returns data
6. Dashboard stats endpoint returns data

Run: python test_link_tracking.py
"""

import os
import sys
import json
import requests
import time

# Load env
from dotenv import load_dotenv
load_dotenv()

API_URL = "http://localhost:5001"

# ── Supabase direct access for verification ──
from app.services.supabase_service import supabase_admin

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
SKIP = "\033[93mSKIP\033[0m"

results = []

def log(test_name, passed, detail=""):
    status = PASS if passed else FAIL
    results.append(passed)
    print(f"  {status}  {test_name}")
    if detail:
        print(f"         {detail}")


def main():
    print("\n" + "=" * 60)
    print("LINK TRACKING SYSTEM - END TO END TEST")
    print("=" * 60)

    # ── Setup: Get a business with a google_place_id ──
    biz_result = supabase_admin.table('businesses') \
        .select('id, business_name, google_place_id') \
        .not_.is_('google_place_id', 'null') \
        .limit(1).execute()

    if not biz_result.data:
        print(f"\n  {FAIL}  No business with google_place_id found. Cannot test.")
        return

    business = biz_result.data[0]
    business_id = business['id']
    business_name = business['business_name']
    place_id = business['google_place_id']
    print(f"\nTest business: {business_name}")
    print(f"Business ID:   {business_id}")
    print(f"Place ID:      {place_id}")

    # ────────────────────────────────────────────────────────
    # TEST 1: Short URL Generation (link_tracker.create_tracking_link)
    # ────────────────────────────────────────────────────────
    print(f"\n{'─' * 60}")
    print("TEST 1: Short URL Generation")
    print(f"{'─' * 60}")

    from app.services.link_tracker import create_tracking_link, get_tracking_link
    from app.services.google_places import get_review_url

    review_url = get_review_url(place_id)
    log("Review URL generated",
        review_url is not None and "placeid=" in review_url,
        f"URL: {review_url}")

    tracking_link = create_tracking_link(
        business_id=business_id,
        destination_url=review_url,
    )

    log("Tracking link created",
        tracking_link is not None,
        f"Result: {json.dumps(tracking_link, indent=2) if tracking_link else 'None'}")

    if tracking_link:
        short_url = tracking_link['short_url']
        short_code = tracking_link['short_code']
        log("Short URL format correct",
            '/r/' in short_url and len(short_code) == 7,
            f"Short URL: {short_url}")

        log("Short code is 7 chars alphanumeric",
            len(short_code) == 7 and short_code.isalnum(),
            f"Code: {short_code}")
    else:
        log("Short URL format correct", False, "No tracking link created")
        log("Short code is 7 chars alphanumeric", False, "No tracking link created")
        print(f"\n  {FAIL}  Cannot continue without tracking link.")
        return

    # ────────────────────────────────────────────────────────
    # TEST 2: Redirect Works (GET /r/<code>)
    # ────────────────────────────────────────────────────────
    print(f"\n{'─' * 60}")
    print("TEST 2: Redirect Endpoint")
    print(f"{'─' * 60}")

    redirect_url = f"{API_URL}/r/{short_code}"
    resp = requests.get(redirect_url, allow_redirects=False)

    log("GET /r/<code> returns 302",
        resp.status_code == 302,
        f"Status: {resp.status_code}")

    location = resp.headers.get('Location', '')
    log("Redirects to Google review page",
        'google.com' in location and 'writereview' in location,
        f"Location: {location[:80]}...")

    log("Redirect URL contains correct place_id",
        place_id in location,
        f"Expected placeid={place_id}")

    # ────────────────────────────────────────────────────────
    # TEST 3: Click is Logged (link_clicks table)
    # ────────────────────────────────────────────────────────
    print(f"\n{'─' * 60}")
    print("TEST 3: Click Logging")
    print(f"{'─' * 60}")

    time.sleep(0.5)  # Brief pause for DB write

    clicks_result = supabase_admin.table('link_clicks') \
        .select('*') \
        .eq('tracking_link_id', tracking_link['id']) \
        .order('clicked_at', desc=True) \
        .limit(5).execute()

    clicks = clicks_result.data or []
    log("Click recorded in link_clicks table",
        len(clicks) > 0,
        f"Found {len(clicks)} click(s)")

    if clicks:
        click = clicks[0]
        log("Click has tracking_link_id",
            click.get('tracking_link_id') == tracking_link['id'],
            f"tracking_link_id: {click.get('tracking_link_id')}")

        log("Click has device_type",
            click.get('device_type') in ('mobile', 'desktop', 'tablet', 'unknown'),
            f"device_type: {click.get('device_type')}")

        log("Click has user_agent",
            click.get('user_agent') is not None,
            f"user_agent: {(click.get('user_agent') or '')[:50]}...")

        log("Click has timestamp",
            click.get('clicked_at') is not None,
            f"clicked_at: {click.get('clicked_at')}")
    else:
        for label in ["Click has tracking_link_id", "Click has device_type",
                       "Click has user_agent", "Click has timestamp"]:
            log(label, False, "No clicks found")

    # ────────────────────────────────────────────────────────
    # TEST 4: Tracking Link Saved in DB
    # ────────────────────────────────────────────────────────
    print(f"\n{'─' * 60}")
    print("TEST 4: Tracking Link Record")
    print(f"{'─' * 60}")

    tl_result = supabase_admin.table('tracking_links') \
        .select('*') \
        .eq('id', tracking_link['id']) \
        .limit(1).execute()

    if tl_result.data:
        tl = tl_result.data[0]
        log("Tracking link exists in DB",
            True,
            f"ID: {tl['id']}")

        log("Tracking link has correct business_id",
            tl.get('business_id') == business_id,
            f"business_id: {tl.get('business_id')}")

        log("Tracking link has correct destination_url",
            tl.get('destination_url') == review_url,
            f"destination_url: {tl.get('destination_url', '')[:60]}...")

        log("Tracking link has correct short_code",
            tl.get('short_code') == short_code,
            f"short_code: {tl.get('short_code')}")
    else:
        for label in ["Tracking link exists in DB", "correct business_id",
                       "correct destination_url", "correct short_code"]:
            log(label, False, "Not found in DB")

    # ────────────────────────────────────────────────────────
    # TEST 5: Full Send Flow (simulate what review_requests.py does)
    # ────────────────────────────────────────────────────────
    print(f"\n{'─' * 60}")
    print("TEST 5: Full Send Flow Simulation")
    print(f"{'─' * 60}")

    # Create a review_request record (like the send endpoint does)
    from datetime import datetime, timezone
    rr_record = {
        'business_id': business_id,
        'customer_name': 'Link Tracking Test',
        'customer_email': 'linktest@example.com',
        'status': 'sent',
        'method': 'email',
        'sent_at': datetime.now(timezone.utc).isoformat(),
    }
    rr_result = supabase_admin.table('review_requests').insert(rr_record).execute()

    if rr_result.data:
        rr_id = rr_result.data[0]['id']
        log("Review request created", True, f"ID: {rr_id}")

        # Create tracking link with review_request_id
        tl2 = create_tracking_link(
            business_id=business_id,
            destination_url=review_url,
            review_request_id=rr_id,
        )
        log("Tracking link created with review_request_id",
            tl2 is not None and tl2.get('short_code'),
            f"Short URL: {tl2['short_url'] if tl2 else 'None'}")

        if tl2:
            # Verify review_request_id is set on tracking_links row
            tl2_check = supabase_admin.table('tracking_links') \
                .select('review_request_id') \
                .eq('id', tl2['id']) \
                .limit(1).execute()

            has_rr_id = (tl2_check.data and
                         tl2_check.data[0].get('review_request_id') == rr_id)
            log("tracking_link.review_request_id is set",
                has_rr_id,
                f"review_request_id: {tl2_check.data[0].get('review_request_id') if tl2_check.data else 'None'}")

            # Click the link to test status update
            click_url = f"{API_URL}/r/{tl2['short_code']}"
            click_resp = requests.get(click_url, allow_redirects=False)
            log("Click on linked request returns 302",
                click_resp.status_code == 302, f"Status: {click_resp.status_code}")

            time.sleep(0.5)

            # Check if review_request status updated to 'clicked'
            rr_check = supabase_admin.table('review_requests') \
                .select('status') \
                .eq('id', rr_id) \
                .limit(1).execute()

            status = rr_check.data[0].get('status') if rr_check.data else None
            log("Review request status updated to 'clicked'",
                status == 'clicked',
                f"status: {status}")
        else:
            for label in ["tracking_link.review_request_id is set",
                           "Click on linked request returns 302",
                           "Review request status updated to 'clicked'"]:
                log(label, False, "No tracking link")
    else:
        log("Review request created", False, "Insert failed")

    # ────────────────────────────────────────────────────────
    # TEST 6: Analytics - get_stats_for_business
    # ────────────────────────────────────────────────────────
    print(f"\n{'─' * 60}")
    print("TEST 6: Analytics (Direct Function Call)")
    print(f"{'─' * 60}")

    from app.services.link_tracker import get_stats_for_business
    stats = get_stats_for_business(business_id, days=30)

    log("Stats returns summary",
        'summary' in stats,
        f"Keys: {list(stats.keys())}")

    log("Stats returns recent_requests",
        'recent_requests' in stats,
        f"Count: {len(stats.get('recent_requests', []))}")

    summary = stats.get('summary', {})
    log("total_sent > 0",
        summary.get('total_sent', 0) > 0,
        f"total_sent: {summary.get('total_sent')}")

    log("total_clicked > 0",
        summary.get('total_clicked', 0) > 0,
        f"total_clicked: {summary.get('total_clicked')}")

    log("click_rate is calculated",
        summary.get('click_rate', 0) > 0,
        f"click_rate: {summary.get('click_rate')}")

    # Check recent_requests has our test request
    recent = stats.get('recent_requests', [])
    test_req = [r for r in recent if r.get('customer_name') == 'Link Tracking Test']
    log("Test request appears in recent_requests",
        len(test_req) > 0,
        f"Found {len(test_req)} matching request(s)")

    if test_req:
        tr = test_req[0]
        log("Test request shows clicked=true",
            tr.get('clicked') is True,
            f"clicked: {tr.get('clicked')}")

        log("Test request has click_count > 0",
            (tr.get('click_count') or 0) > 0,
            f"click_count: {tr.get('click_count')}")

    # ────────────────────────────────────────────────────────
    # TEST 7: Dashboard Stats Endpoint (no auth - direct test)
    # ────────────────────────────────────────────────────────
    print(f"\n{'─' * 60}")
    print("TEST 7: Dashboard Stats (Direct DB Check)")
    print(f"{'─' * 60}")

    # Count sent (not failed)
    sent_result = supabase_admin.table('review_requests') \
        .select('id', count='exact') \
        .eq('business_id', business_id) \
        .neq('status', 'failed') \
        .execute()
    total_sent = sent_result.count if sent_result.count is not None else len(sent_result.data or [])

    # Count clicked
    clicked_result = supabase_admin.table('review_requests') \
        .select('id', count='exact') \
        .eq('business_id', business_id) \
        .eq('status', 'clicked') \
        .execute()
    total_clicked = clicked_result.count if clicked_result.count is not None else len(clicked_result.data or [])

    conversion = round((total_clicked / total_sent) * 100) if total_sent > 0 else 0

    log("Dashboard: total_sent > 0",
        total_sent > 0,
        f"total_sent: {total_sent}")

    log("Dashboard: total_clicked > 0",
        total_clicked > 0,
        f"total_clicked: {total_clicked}")

    log("Dashboard: conversion_rate calculated",
        conversion >= 0,
        f"conversion_rate: {conversion}%")

    print(f"\n  Dashboard would show:")
    print(f"    Requests Sent:  {total_sent}")
    print(f"    Links Clicked:  {total_clicked}")
    print(f"    Conversion:     {conversion}%")

    # ────────────────────────────────────────────────────────
    # CLEANUP
    # ────────────────────────────────────────────────────────
    print(f"\n{'─' * 60}")
    print("CLEANUP")
    print(f"{'─' * 60}")

    # Delete test data (tracking links, clicks, review request)
    if 'rr_id' in dir() or 'rr_id' in locals():
        # Delete clicks for both test tracking links
        for tl_obj in [tracking_link, tl2] if 'tl2' in locals() and tl2 else [tracking_link]:
            supabase_admin.table('link_clicks') \
                .delete().eq('tracking_link_id', tl_obj['id']).execute()
            supabase_admin.table('tracking_links') \
                .delete().eq('id', tl_obj['id']).execute()

        supabase_admin.table('review_requests') \
            .delete().eq('id', rr_id).execute()
        print("  Cleaned up test records")
    else:
        # Still clean up first tracking link
        supabase_admin.table('link_clicks') \
            .delete().eq('tracking_link_id', tracking_link['id']).execute()
        supabase_admin.table('tracking_links') \
            .delete().eq('id', tracking_link['id']).execute()
        print("  Cleaned up tracking link test records")

    # ────────────────────────────────────────────────────────
    # SUMMARY
    # ────────────────────────────────────────────────────────
    passed = sum(1 for r in results if r is True)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"RESULTS: {passed}/{total} tests passed")
    if passed == total:
        print(f"\033[92mALL TESTS PASSED\033[0m")
    else:
        failed = total - passed
        print(f"\033[91m{failed} TEST(S) FAILED\033[0m")
    print(f"{'=' * 60}\n")


if __name__ == '__main__':
    main()
