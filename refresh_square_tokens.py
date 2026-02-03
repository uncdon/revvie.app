#!/usr/bin/env python3
"""
Square Token Refresh Cron Job

This script refreshes all Square integration tokens that are expiring soon.
Run this weekly to ensure tokens never expire unexpectedly.

USAGE:
======
    python refresh_square_tokens.py

CRON SETUP:
===========
Run weekly on Sunday at midnight:

    # Edit crontab
    crontab -e

    # Add this line (adjust path as needed):
    0 0 * * 0 cd /path/to/revvie && /path/to/venv/bin/python refresh_square_tokens.py >> /path/to/logs/token_refresh.log 2>&1

RAILWAY/HEROKU:
===============
Use a scheduler addon to run this script weekly.

For Railway, you can use the Railway Cron feature or a separate worker service.
"""

import os
import sys
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    """Run the token refresh job."""
    print("=" * 60)
    print(f"SQUARE TOKEN REFRESH JOB")
    print(f"Started at: {datetime.now().isoformat()}")
    print("=" * 60)

    try:
        from app.services.square_service import refresh_all_tokens

        results = refresh_all_tokens()

        print(f"\nResults:")
        print(f"  Total integrations: {results['total']}")
        print(f"  Refreshed:          {results['refreshed']}")
        print(f"  Skipped (valid):    {results['skipped']}")
        print(f"  Failed:             {results['failed']}")

        if results['errors']:
            print(f"\nErrors:")
            for error in results['errors']:
                print(f"  - {error}")

        print(f"\nCompleted at: {datetime.now().isoformat()}")
        print("=" * 60)

        # Exit with error code if any failures
        if results['failed'] > 0:
            sys.exit(1)

    except Exception as e:
        print(f"\nFATAL ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
