"""
Quick test script for authentication endpoints.
Run this while your Flask server is running in another terminal.

Usage:
    python test_auth.py signup
    python test_auth.py login
    python test_auth.py me <token>
"""

import requests
import sys

BASE_URL = "http://localhost:5001/api"

def signup():
    """Test the signup endpoint."""
    email = input("Enter email: ")
    password = input("Enter password: ")
    business_name = input("Enter business name: ")

    response = requests.post(f"{BASE_URL}/auth/signup", json={
        "email": email,
        "password": password,
        "business_name": business_name
    })

    print(f"\nStatus: {response.status_code}")
    print(f"Response: {response.json()}")

    # If successful, show the token
    data = response.json()
    if "session" in data and data["session"].get("access_token"):
        print(f"\n✓ Save this token for testing protected routes:")
        print(f"  {data['session']['access_token'][:50]}...")

def login():
    """Test the login endpoint."""
    email = input("Enter email: ")
    password = input("Enter password: ")

    response = requests.post(f"{BASE_URL}/auth/login", json={
        "email": email,
        "password": password
    })

    print(f"\nStatus: {response.status_code}")
    print(f"Response: {response.json()}")

def me(token):
    """Test the /me endpoint with a token."""
    response = requests.get(f"{BASE_URL}/auth/me", headers={
        "Authorization": f"Bearer {token}"
    })

    print(f"\nStatus: {response.status_code}")
    print(f"Response: {response.json()}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python test_auth.py signup")
        print("  python test_auth.py login")
        print("  python test_auth.py me <token>")
        sys.exit(1)

    command = sys.argv[1]

    if command == "signup":
        signup()
    elif command == "login":
        login()
    elif command == "me":
        if len(sys.argv) < 3:
            print("Error: Token required. Usage: python test_auth.py me <token>")
            sys.exit(1)
        me(sys.argv[2])
    else:
        print(f"Unknown command: {command}")
