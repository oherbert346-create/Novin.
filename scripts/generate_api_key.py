#!/usr/bin/env python3

from __future__ import annotations

import argparse
import secrets

def generate_api_key() -> str:
    """Generate a secure random API key."""
    return f"novin_{secrets.token_urlsafe(32)}"

def generate_basic_auth() -> tuple[str, str]:
    """Generate Basic Auth username and password."""
    username = f"pilot_user_{secrets.token_urlsafe(8)}"
    password = secrets.token_urlsafe(32)
    return username, password

def main():
    parser = argparse.ArgumentParser(description="Generate secure API key or Basic Auth credentials")
    parser.add_argument("--env-name", default="INGEST_API_KEY", help="Environment variable name for API key")
    parser.add_argument("--basic-auth", action="store_true", help="Generate Basic Auth credentials instead of API key")
    args = parser.parse_args()
    
    if args.basic_auth:
        username, password = generate_basic_auth()
        print(f"BASIC_AUTH_USER={username}")
        print(f"BASIC_AUTH_PASS={password}")
        print()
        print(f"Add these to your .env file:")
        print(f"BASIC_AUTH_USER={username}")
        print(f"BASIC_AUTH_PASS={password}")
    else:
        key = generate_api_key()
        print(f"{args.env_name}={key}")
        print()
        print(f"Add this to your .env file:")
        print(f"{args.env_name}={key}")

if __name__ == "__main__":
    main()
