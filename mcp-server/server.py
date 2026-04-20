"""
Travelopro Car Rental MCP Server

A FastMCP server that wraps the Travelopro Car Rental API, exposing all
API methods as MCP tools for AI agents.

API Endpoints:
  - /languages            → get_languages
  - /destinations         → get_destinations
  - /search               → search_cars
  - /rental_condition_details → get_rental_conditions
  - /car_insurance        → get_car_insurance
  - /car_book             → book_car
  - /cancel_booking       → cancel_car_booking
  - /booking_details      → get_booking_details
"""

import os
import json
import logging
from typing import Any, Optional
import httpx
from pathlib import Path
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Bootstrap — load .env from this file's directory OR its parent (car-rental-agent/)
# ---------------------------------------------------------------------------
_here = Path(__file__).parent
load_dotenv(_here / ".env")          # mcp-server/.env (if present)
load_dotenv(_here.parent / ".env")   # car-rental-agent/.env (primary)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("travelopro-mcp")

# ---------------------------------------------------------------------------
# Configuration — read from .env, fall back to defaults
# ---------------------------------------------------------------------------
BASE_URL: str = os.getenv(
    "TRAVELOPRO_BASE_URL", "https://travelnext.works/api/carsv3-test"
).rstrip("/")

CREDS: dict[str, str] = {
    "user_id":       os.getenv("TRAVELOPRO_USER_ID", ""),
    "user_password": os.getenv("TRAVELOPRO_USER_PASSWORD", ""),
    "ip_address":    os.getenv("TRAVELOPRO_IP_ADDRESS", ""),
    "access":        os.getenv("TRAVELOPRO_ACCESS", "Test"),
}

# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _auth_payload() -> dict[str, str]:
    """Return base authentication fields required by every Travelopro endpoint."""
    return dict(CREDS)


async def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """
    Issue a POST request to the Travelopro API and return the parsed JSON.
    Raises a RuntimeError with a human-readable message on any failure.
    """
    url = f"{BASE_URL}/{path.lstrip('/')}"
    logger.info(f"POST {url}")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(url, json=body)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"HTTP {exc.response.status_code} from Travelopro API: {exc.response.text}"
        ) from exc
    except httpx.RequestError as exc:
        raise RuntimeError(f"Network error calling Travelopro API: {exc}") from exc

    # Surface Travelopro API-level errors as RuntimeError
    if isinstance(data, dict) and "Errors" in data:
        err = data["Errors"]
        code = err.get("ErrorCode", "UNKNOWN")
        msg  = err.get("ErrorMessage", "Unknown error.")
        raise RuntimeError(f"[{code}] {msg}")

    return data


# ---------------------------------------------------------------------------
# FastMCP Server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="travelopro-car-rental",
    instructions=(
        "Travelopro Car Rental API server. "
        "Use get_destinations to find location IDs, then search_cars to find "
        "available vehicles, then get_rental_conditions for full details, "
        "then book_car to complete a reservation."
    ),
)


# ---------------------------------------------------------------------------
# Tool 1 — Languages
# ---------------------------------------------------------------------------
@mcp.tool(
    description=(
        "Retrieve the list of supported languages for the Travelopro car booking flow. "
        "Returns language names and their codes (e.g. 'EN-US')."
    )
)
async def get_languages() -> str:
    """Fetch all available language options from the Travelopro API."""
    data = await _post("languages", _auth_payload())
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Destinations cache — loaded once at server startup
# ---------------------------------------------------------------------------
_DESTINATIONS_FILE = _here / "destinations.json"
_destinations_cache: list[dict] = []

def _load_destinations_cache() -> None:
    """Load the locally-cached destinations file into memory."""
    global _destinations_cache
    if not _DESTINATIONS_FILE.exists():
        logger.warning(
            f"Destinations cache not found at {_DESTINATIONS_FILE}. "
            "Run fetch_destinations.py first to populate it."
        )
        _destinations_cache = []
        return
    with open(_DESTINATIONS_FILE, encoding="utf-8") as f:
        _destinations_cache = json.load(f)
    logger.info(f"Loaded {len(_destinations_cache):,} destinations from cache.")

# Load at import time
_load_destinations_cache()


# ---------------------------------------------------------------------------
# Tool 2 — Destinations (local cache with text filter)
# ---------------------------------------------------------------------------
@mcp.tool(
    description=(
        "Search for car-rental pickup/drop-off locations from the locally cached "
        "Travelopro destination list. Pass a 'query' string (city name, airport name, "
        "or airport code) to filter results. Each result includes an 'id' field "
        "that must be used as 'pickup_id' or 'dropoff_id' in search_cars. "
        "Returns up to 'limit' matches (default 20)."
    )
)
async def get_destinations(query: str, limit: int = 20) -> str:
    """
    Filter locally-cached Travelopro destinations by a search query.

    Args:
        query: Text to search for (city name, location name, or airport code).
        limit: Maximum number of results to return (default 20, max 50).
    """
    if not _destinations_cache:
        return json.dumps({
            "error": "Destinations cache is empty. Run fetch_destinations.py first.",
            "results": []
        }, indent=2)

    q = query.lower().strip()
    limit = min(max(1, limit), 50)

    matches = [
        dest for dest in _destinations_cache
        if q in dest.get("location_name", "").lower()
        or q in dest.get("city", "").lower()
        or q in (dest.get("airport_code") or "").lower()
    ]

    return json.dumps({
        "query": query,
        "total_matches": len(matches),
        "returned": min(len(matches), limit),
        "results": matches[:limit],
    }, indent=2)


# ---------------------------------------------------------------------------
# Tool 3 — Car Search
# ---------------------------------------------------------------------------
@mcp.tool(
    description=(
        "Search for available rental cars. Returns a list of vehicles with pricing, "
        "car details, vendor info, and a sessionId required for subsequent API calls."
    )
)
async def search_cars(
    pickup_id: str,
    dropoff_id: str,
    pickup_location: str,
    dropoff_location: str,
    pickup_date: str,
    pickup_time: str,
    dropoff_date: str,
    dropoff_time: str,
    driver_age: int,
    country_res: str,
    currency: str = "USD",
    language: str = "EN-US",
    sorting: Optional[str] = None,
    max_results: int = 10,
) -> str:
    """
    Search for available rental cars.

    Args:
        pickup_id: Location ID for pickup (from get_destinations).
        dropoff_id: Location ID for drop-off (from get_destinations).
        pickup_location: Pickup coordinates as "latitude,longitude".
        dropoff_location: Drop-off coordinates as "latitude,longitude".
        pickup_date: Pickup date in YYYY-MM-DD format.
        pickup_time: Pickup time in HH:MM format (24h).
        dropoff_date: Drop-off date in YYYY-MM-DD format.
        dropoff_time: Drop-off time in HH:MM format (24h).
        driver_age: Age of the primary driver.
        country_res: 2-letter country code of driver's residence (e.g. "US").
        currency: 3-letter currency code for pricing (default: "USD").
        language: Language code for results (default: "EN-US").
        sorting: Optional sort order (e.g. "price-high-low").
        max_results: Maximum number of cars to return (default: 10, max: 20).
    """
    payload = {
        **_auth_payload(),
        "pickup_id": pickup_id,
        "dropoff_id": dropoff_id,
        "pickup_location": pickup_location,
        "dropoff_location": dropoff_location,
        "pickup_date": pickup_date,
        "pickup_time": pickup_time,
        "dropoff_date": dropoff_date,
        "dropoff_time": dropoff_time,
        "driver_age": str(driver_age),
        "country_res": country_res,
        "currency": currency,
        "language": language,
    }
    if sorting:
        payload["sorting"] = sorting

    data = await _post("search", payload)

    # Limit results and prioritize POSTPAID to avoid LLM context overflow
    limit = min(max(1, max_results), 20)
    if isinstance(data, dict) and "data" in data:
        cars = data["data"]
        
        # Sort: POSTPAID (Pay at Pickup) first, then PREPAID
        # Also include a clear label for the LLM
        for car in cars:
            rate_type = car.get("rateQualifier", "PREPAID").upper()
            car["payment_type"] = "Pay at Pickup" if rate_type == "POSTPAID" else "Pay Now"

        # Stable sort: POSTPAID first
        cars.sort(key=lambda x: 0 if x.get("rateQualifier") == "POSTPAID" else 1)

        total = data.get("count", len(cars))
        data["data"] = cars[:limit]
        data["count"] = len(data["data"])
        data["total_available"] = total
        data["note"] = (
            f"Showing top {limit} of {total} available cars. "
            "POSTPAID (Pay at Location) options are prioritized for your convenience."
        )

    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Tool 4 — Rental Conditions & Details
# ---------------------------------------------------------------------------
@mcp.tool(
    description=(
        "Fetch full rental conditions and additional details for a selected vehicle. "
        "Returns charges, coverage options, extra equipment, and rental policy text. "
        "Must be called before booking."
    )
)
async def get_rental_conditions(session_id: str, reference_id: str) -> str:
    """
    Get rental conditions and details for a specific car result.

    Args:
        session_id: The sessionId returned by search_cars.
        reference_id: The referenceId of the specific car from search_cars results.
    """
    payload = {
        "session_id": session_id,
        "reference_id": reference_id,
    }
    data = await _post("rental_condition_details", payload)
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Tool 5 — Car Insurance
# ---------------------------------------------------------------------------
@mcp.tool(
    description=(
        "Fetch available insurance plans for a car rental. "
        "Should be called after search_cars, as pricing varies by driver and rental cost. "
        "Returns insurance plan IDs that can be included in book_car."
    )
)
async def get_car_insurance(
    session_id: str,
    reference_id: str,
    first_name: str,
    last_name: str,
) -> str:
    """
    Retrieve insurance options for a selected rental.

    Args:
        session_id: The sessionId returned by search_cars.
        reference_id: The referenceId of the specific car from search_cars results.
        first_name: Driver's first name.
        last_name: Driver's last name.
    """
    payload = {
        "session_id": session_id,
        "reference_id": reference_id,
        "first_name": first_name,
        "last_name": last_name,
    }
    data = await _post("car_insurance", payload)
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Tool 6 — Car Book
# ---------------------------------------------------------------------------
@mcp.tool(
    description=(
        "Create a car rental booking. Requires session_id and reference_id from "
        "search_cars, passenger details, and payment card information. "
        "Returns a confirmation number on success."
    )
)
async def book_car(
    session_id: str,
    reference_id: str,
    no_of_passenger: int,
    # Passenger details
    title: str,
    first_name: str,
    last_name: str,
    email_id: str,
    area_code: str,
    phone: str,
    address: str,
    city: str,
    state: str,
    country: str,
    pincode: str,
    # Payment details
    card_type: str,
    card_code: str,
    card_no: str,
    card_cvv: str,
    expiry_date: str,
    card_holder_name: str,
    # Optional fields
    client_reference: Optional[str] = None,
    insurance_plan_id: Optional[str] = None,
    remark: Optional[str] = None,
    extra_services: Optional[list[dict]] = None,
    airline_code: Optional[str] = None,
    airline_number: Optional[str] = None,
) -> str:
    """
    Book a rental car.

    Args:
        session_id: Session ID from search_cars.
        reference_id: Reference ID from search_cars.
        no_of_passenger: Number of passengers.
        title: Passenger title (Mr/Mrs/Miss).
        first_name: Passenger first name.
        last_name: Passenger last name.
        email_id: Passenger email address.
        area_code: Country dial code (e.g. "1" for US).
        phone: Passenger phone number.
        address: Passenger street address.
        city: Passenger city.
        state: Passenger state/region.
        country: Passenger country.
        pincode: Passenger ZIP/PIN code.
        card_type: "1" for Credit Card, "2" for Debit Card.
        card_code: Card code (e.g. "VI" for Visa, "MC" for MasterCard).
        card_no: Card number.
        card_cvv: Card CVV.
        expiry_date: Expiry date in MMYY format (e.g. "0928").
        card_holder_name: Name on the card.
        client_reference: Optional unique reference for your system.
        insurance_plan_id: Optional insurance plan ID from get_car_insurance.
        remark: Optional remark for the supplier.
        extra_services: Optional list of extra services, each with 'equip_type' and 'quantity'.
        airline_code: Optional airline code if pickup is at an airport.
        airline_number: Optional flight number if pickup is at an airport.
    """
    payload: dict[str, Any] = {
        "session_id": session_id,
        "reference_id": reference_id,
        "no_of_passenger": str(no_of_passenger),
        "pax_details": {
            "title": title,
            "first_name": first_name,
            "last_name": last_name,
            "email_id": email_id,
            "area_code": area_code,
            "phone": phone,
            "address": address,
            "city": city,
            "state": state,
            "country": country,
            "pincode": pincode,
        },
        "payment_details": {
            "card_type": card_type,
            "card_code": card_code,
            "card_no": card_no,
            "card_cvv": card_cvv,
            "expiry_date": expiry_date,
            "card_holder_name": card_holder_name,
        },
    }

    if client_reference:
        payload["client_reference"] = client_reference
    if insurance_plan_id:
        payload["insurance_plan_id"] = insurance_plan_id
    if remark:
        payload["remark"] = remark
    if extra_services:
        payload["extra_services"] = extra_services
    if airline_code and airline_number:
        payload["airline_details"] = {
            "airline_code": airline_code,
            "airline_number": airline_number,
        }

    data = await _post("car_book", payload)
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Tool 7 — Cancel Booking
# ---------------------------------------------------------------------------
@mcp.tool(
    description=(
        "Cancel an existing car rental booking using the confirmation number. "
        "Returns the updated booking status."
    )
)
async def cancel_car_booking(confirmation_id: str) -> str:
    """
    Cancel a car rental booking.

    Args:
        confirmation_id: The booking confirmation number to cancel.
    """
    payload = {
        **_auth_payload(),
        "confirmation_id": confirmation_id,
    }
    data = await _post("cancel_booking", payload)
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Tool 8 — Booking Details
# ---------------------------------------------------------------------------
@mcp.tool(
    description=(
        "Retrieve full details of an existing car rental booking using the "
        "confirmation number. Returns rental info, passenger details, and current status."
    )
)
async def get_booking_details(confirmation_id: str) -> str:
    """
    Retrieve details of an existing booking.

    Args:
        confirmation_id: The booking confirmation number to look up.
    """
    payload = {
        **_auth_payload(),
        "confirmation_id": confirmation_id,
    }
    data = await _post("booking_details", payload)
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info(f"Starting Travelopro MCP Server against: {BASE_URL}")
    mcp.run(transport="stdio")
