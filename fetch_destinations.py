"""
fetch_destinations.py
---------------------
One-time (or periodic) script to pull ALL destinations from the Travelopro API
and store them in a local JSON file: mcp-server/destinations.json

Run this script once before starting the agent:
    python fetch_destinations.py

The destinations file is then used by the MCP server's get_destinations tool
to do fast local filtering without hitting the API on every query.
"""

import json
import os
import sys
from pathlib import Path
import httpx
from dotenv import load_dotenv

# Load credentials from car-rental-agent/.env
_here = Path(__file__).parent
load_dotenv(_here / ".env")

BASE_URL = os.getenv("TRAVELOPRO_BASE_URL", "https://travelnext.works/api/carsv3-test").rstrip("/")
CREDS = {
    "user_id":       os.getenv("TRAVELOPRO_USER_ID", ""),
    "user_password": os.getenv("TRAVELOPRO_USER_PASSWORD", ""),
    "ip_address":    os.getenv("TRAVELOPRO_IP_ADDRESS", ""),
    "access":        os.getenv("TRAVELOPRO_ACCESS", "Test"),
}

OUTPUT_FILE = _here / "mcp-server" / "destinations.json"


def main():
    if not CREDS["user_id"] or CREDS["user_id"] == "your_user_id_here":
        print("ERROR: Travelopro credentials not set in .env")
        sys.exit(1)

    print(f"Fetching destinations from {BASE_URL}/destinations ...")
    try:
        response = httpx.post(
            f"{BASE_URL}/destinations",
            json=CREDS,
            timeout=60.0,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as e:
        print(f"HTTP Error {e.response.status_code}: {e.response.text}")
        sys.exit(1)
    except Exception as e:
        print(f"Request failed: {e}")
        sys.exit(1)

    if isinstance(data, dict) and "Errors" in data:
        err = data["Errors"]
        print(f"API Error [{err.get('ErrorCode')}]: {err.get('ErrorMessage')}")
        sys.exit(1)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(f"Saved {len(data):,} destinations -> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
