import os
import time
import asyncio
from typing import Dict, Any

def _get_stripe():
    import stripe
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    return stripe

def _expires_at() -> int:
    # Default 1 hour expiry for checkout sessions
    return int(time.time()) + 3600

def _create_session(*, user_address: str, chat_session_id: str, amount_cents: int, description: str) -> Dict[str, Any]:
    stripe = _get_stripe()
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        mode="payment",
        ui_mode="embedded",
        redirect_on_completion="if_required",
        return_url=os.getenv("STRIPE_SUCCESS_URL", "https://example.com/success") + "?session_id={CHECKOUT_SESSION_ID}",
        expires_at=_expires_at(),
        line_items=[
            {
                "price_data": {
                    "currency": os.getenv("STRIPE_CURRENCY", "usd"),
                    "product_data": {
                        "name": "Car Rental Booking",
                        "description": description,
                    },
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }
        ],
        metadata={
            "user_address": user_address,
            "session_id": chat_session_id,
            "service": "car_rental",
        },
    )
    return {
        "client_secret": session.client_secret,
        "checkout_session_id": session.id,
        "publishable_key": os.getenv("STRIPE_PUBLISHABLE_KEY"),
        "currency": os.getenv("STRIPE_CURRENCY", "usd"),
        "amount_cents": str(amount_cents),
        "ui_mode": "embedded",
    }

async def create_checkout_session(*, user_address: str, chat_session_id: str, amount_cents: int, description: str) -> Dict[str, Any]:
    """Async wrapper for Stripe session creation."""
    return await asyncio.to_thread(
        _create_session,
        user_address=user_address,
        chat_session_id=chat_session_id,
        amount_cents=amount_cents,
        description=description
    )

def _verify_paid(transaction_id: str) -> bool:
    stripe = _get_stripe()
    session = stripe.checkout.Session.retrieve(transaction_id)
    return getattr(session, "payment_status", None) == "paid"

async def verify_paid(transaction_id: str) -> bool:
    """Async wrapper with retry logic for payment verification."""
    for delay in (0, 2, 5, 10):
        if delay:
            await asyncio.sleep(delay)
        try:
            paid = await asyncio.to_thread(_verify_paid, transaction_id)
            if paid:
                return True
        except Exception:
            if delay == 10:
                raise
    return False
