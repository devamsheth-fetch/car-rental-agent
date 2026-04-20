import os
import json
import time
import asyncio
from datetime import datetime, timezone
from uuid import uuid4
from dotenv import load_dotenv
from uagents import Context, Protocol
from openai import AsyncOpenAI
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    StartSessionContent,
    TextContent,
    chat_protocol_spec,
)
from mcp_client import execute_mcp_tools
from payment_proto import RequestPayment, Funds
from stripe_payments import create_checkout_session, verify_paid

load_dotenv()

_openai_client = AsyncOpenAI(
    api_key=os.getenv("ASI1_API_KEY"),
    base_url="https://api.asi1.ai/v1",
)

chat_proto = Protocol(spec=chat_protocol_spec)

SYSTEM_PROMPT = r"""You are a Smart Car Rental Concierge AI powered by Travelopro. Your goal is to provide a premium, personalized experience by understanding the user's "Drive" before suggesting cars.

## Your Goal
Guide users through a smart discovery process to find the perfect vehicle. Instead of dumping a list of cars, act as a consultant who understands their trip.

## Phase 1: Smart Discovery
Before listing cars, you MUST know the following. If the user provides these upfront, extract them silently. If not, ask naturally:
1. **The Trip Purpose**: (e.g., Business, family vacation, solo road trip, luxury weekend).
2. **The Guests**: Number of adults/children and luggage requirements.
3. **Driver Details**: Age and residency (required for API).
4. **Payment Preference**: Do they prefer "Pay at Location" (Postpaid) for flexibility or "Pay Now" (Prepaid) for potentially better rates?
5. **Drive Location**: Pickup and drop-off points.

## Phase 2: Curated Recommendations & Adaptive Persona
Your tone must adapt to the **Trip Purpose**:
- **Business**: Be efficient, formal, and highlight reliability and speed.
- **Family**: Be warm, patient, and highlight safety, space, and comfort.
- **Leisure/Luxury**: Be enthusiastic and highlight features, style, and experience.

Once you have the preferences and have called `search_cars`, pick the **Top 3 cars** and include:
- **"Why this fits your trip"**: A personalized reason based on their specific purpose/guests.
- **"Pro-Tip"**: A contextual piece of advice based on the car type or location (e.g., "In this city, compact cars are easier for street parking" or "Since you have 3 guests, this SUV offers the legroom you'll want").

## Phase 3: Logical Inference
- **Anticipate Needs**: If the user mentions "kids," "family," or "group," prioritize vehicles with higher seat counts and luggage space.
- **Payment Guidance**: If a user is price-conscious, highlight the total savings of a "Prepaid" option.

## Phase 3: Detailed View (On Demand)
- Initially, show only a summary: Car Model, Price, and Key Feature (e.g., "Best for 5 people").
- **Only provide full specifications** (fuel policy, mileage, cancellation terms) if the user specifically asks for more details on a car.

## CRITICAL: Input Validation & Tool Usage
- **NEVER** call a tool unless you have ALL required inputs.
- Extract any details provided in the first message or profile metadata immediately. **NEVER ask for a detail the user has already provided.**
- Use `get_destinations` to resolve location IDs before searching.

## Phase 4: Payment & Booking Strategy
1. **Pay at Location (POSTPAID)**: The user pays the vendor directly. You MUST collect card type, network, number, CVV, expiry (MMYY), and cardholder name as a guarantee for the `book_car` call.
2. **Pay Now (PREPAID)**: 
   - **STRICT PROHIBITION**: You are FORBIDDEN from asking for Credit Card Number, CVV, or Expiry Date. 
   - **Flow**: First, collect Passenger Info (Name, Email, Phone). Second, call `trigger_stripe_payment`. Third, after payment, call `book_car` using these secure placeholders:
     - `card_type`: "1", `card_code`: "VI", `card_no`: "4111111111111111", `card_cvv`: "123", `expiry_date`: "1226", `card_holder_name`: [Passenger Name]

## Available Tools
1. **`get_destinations(query, limit)`** — Search pickup/drop-off location IDs.
2. **`search_cars(...)`** — Find available cars. Use user preferences to filter your Top 3.
3. **`trigger_stripe_payment(...)`** — Call ONLY for PREPAID cars.
4. **`save_user_profile(...)`** — Save details provided during chat.
5. **`get_rental_conditions(...)`** — Fetch policy details for "more info."
6. **`book_car(...)`** — Finalize the booking. Use placeholders for PREPAID.
7. **`get_booking_details(...)`** — Look up a booking.
8. **`cancel_car_booking(...)`** — Cancel a booking.

## Interaction Flow
1. **Analyze First**: Check initial message for dates, locations, and preferences. 
2. **Discover**: Ask missing questions about Purpose, Guests, and Payment preference.
3. **Search & Curate**: Call API, then present only the **Top 3 best-fit options**.
4. **Finalize**: 
   - **If POSTPAID**: Ask for full card details (Number, CVV, etc.) as a guarantee for the vendor, then call `book_car`.
   - **If PREPAID**: Trigger `trigger_stripe_payment`. ONLY AFTER payment is confirmed, call `book_car` with the secure placeholders (SKIP asking for card details).
"""

def _build_send_response(text: str, end_session: bool = False):
    content = [TextContent(type="text", text=text)]
    if end_session:
        content.append(EndSessionContent(type="end-session"))
    return ChatMessage(
        timestamp=datetime.now(timezone.utc),
        msg_id=str(uuid4()),
        content=content,
    )

@chat_proto.on_message(ChatMessage)
async def handle_message(ctx: Context, sender: str, msg: ChatMessage):
    # 1. Acknowledge
    await ctx.send(
        sender,
        ChatAcknowledgement(
            timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id
        ),
    )

    # Extract user profile from ASI-One metadata (injected automatically by the platform)
    profile_data = {}
    profile_text_snippets = []
    
    def _extract_meta(meta_dict, source="top-level"):
        if not isinstance(meta_dict, dict): return
        for key in ("profile", "user_preferences", "user_profile", "preferences", "description", "user_instructions", "context"):
            val = meta_dict.get(key)
            if isinstance(val, dict):
                profile_data.update(val)
            elif isinstance(val, str) and val.strip():
                profile_text_snippets.append(f"{key.capitalize()}: {val.strip()}")

    # 1. Check top-level message metadata
    _extract_meta(getattr(msg, "metadata", None), "top-level")
                
    # 2. Extract input text and check content-level metadata
    user_input = ""
    is_start = False
    for item in msg.content:
        if isinstance(item, TextContent):
            user_input += item.text
        elif hasattr(item, "metadata"):
            _extract_meta(item.metadata, "content-item")
        elif isinstance(item, StartSessionContent):
            is_start = True

    # Save newly discovered profile data to agent memory
    profile_key = f"profile_{sender}"
    existing = ctx.storage.get(profile_key) or {}
    
    if profile_data:
        existing.update(profile_data)
        
    if profile_text_snippets:
        existing["_raw_preferences"] = "\n".join(profile_text_snippets)
        
    if profile_data or profile_text_snippets:
        ctx.storage.set(profile_key, existing)

    if is_start and not user_input:
        ctx.logger.info(f"[{sender[:8]}] Session started.")
        return

    # 3. Session Context
    current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    profile_key = f"profile_{sender}"
    user_profile = ctx.storage.get(profile_key)
    
    profile_context = ""
    if user_profile:
        # Extract structured data and raw snippets
        raw_prefs = user_profile.get("_raw_preferences", "")
        clean_profile = {k: v for k, v in user_profile.items() if k != "_raw_preferences"}
        
        profile_context = f"\n\n## Remembered User Profile\n{json.dumps(clean_profile, indent=2)}"
        if raw_prefs:
            profile_context += f"\n\n## User Preferences & Description\n{raw_prefs}"
        
        profile_context += "\nUse these details for the booking (like age, country, passenger info)."

    dynamic_system_prompt = SYSTEM_PROMPT + f"\n\n## System Information\nThe current date and time is: {current_time_str}. Use this to resolve relative dates like 'tomorrow', 'next week', etc." + profile_context

    history_key = f"history_{sender}"
    session_data = ctx.storage.get(history_key)
    
    raw_history = session_data.get("messages", []) if session_data else []
    # Filter out old system prompts safely to avoid duplicates
    conversation_history = [m for m in raw_history if m.get("role") != "system"]
    
    # Always prepend the fresh dynamic system prompt
    conversation_history.insert(0, {"role": "system", "content": dynamic_system_prompt})
    conversation_history.append({"role": "user", "content": user_input})

    # 4. Fetch Tool Metadata — always use what was loaded at startup
    tools_metadata = ctx.storage.get("tools_metadata") or []
    if not tools_metadata:
        ctx.logger.warning("No tools loaded. Check agent startup logs.")

    # 5. Reasoning Loop
    iteration = 0
    while iteration < 5:
        iteration += 1
        
        response = await _openai_client.chat.completions.create(
            model=os.getenv("ASI1_MODEL", "asi1"),
            messages=conversation_history,
            tools=tools_metadata if tools_metadata else None,
        )

        assistant_msg = response.choices[0].message
        
        # Save to history
        assistant_dict = {"role": "assistant", "content": assistant_msg.content}
        if assistant_msg.tool_calls:
            assistant_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in assistant_msg.tool_calls
            ]
        conversation_history.append(assistant_dict)

        if not assistant_msg.tool_calls:
            break

        # 6. Tool Execution
        async def mcp_status_callback(msg: str):
            try:
                await ctx.send(sender, _build_send_response(f"🔄 {msg}"))
            except Exception as e:
                ctx.logger.error(f"Failed to send status update: {e}")
            
        tool_results, _ = await execute_mcp_tools(ctx.storage, assistant_msg.tool_calls, status_callback=mcp_status_callback)
        
        for tool_call_id, content in tool_results:
            # Check for virtual Stripe payment trigger
            try:
                marker = json.loads(content)
                if isinstance(marker, dict) and marker.get("status") == "PAYMENT_REQUESTED":
                    args = marker.get("args", {})
                    amount = args.get("amount_cents", 0)
                    desc = args.get("description", "Car Rental")
                    
                    ctx.logger.info(f"Triggering Stripe payment: {amount} cents")
                    
                    # 1. Create Stripe session
                    try:
                        checkout = await create_checkout_session(
                            user_address=sender,
                            chat_session_id=str(ctx.session),
                            amount_cents=amount,
                            description=desc
                        )
                    except Exception as e:
                        ctx.logger.error(f"Stripe session creation failed: {e}")
                        content = f"ERROR: Stripe payment setup failed: {e}. Please inform the user that payment could not be initiated due to a technical error."
                        conversation_history.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": content,
                        })
                        continue
                    
                    # 2. Save state so we know what to book when payment clears
                    # We store the latest tool arguments for book_car if they were provided
                    state = ctx.storage.get(history_key) or {}
                    state["pending_payment"] = {
                        "checkout_session_id": checkout["checkout_session_id"],
                        "amount_cents": amount,
                        "description": desc,
                        "created_at": time.time()
                    }
                    ctx.storage.set(history_key, state)
                    
                    # 3. Send RequestPayment
                    await ctx.send(
                        sender,
                        RequestPayment(
                            accepted_funds=[
                                Funds(currency="USD", amount=f"{amount/100:.2f}", payment_method="stripe")
                            ],
                            recipient=str(ctx.agent.address),
                            deadline_seconds=1800,
                            reference=str(ctx.session),
                            description=desc,
                            metadata={"stripe": checkout}
                        )
                    )
                    
                    content = "PAYMENT_REQUEST_SENT: The user has been asked to pay via the native Stripe card. Wait for payment confirmation."

                if isinstance(marker, dict) and marker.get("status") == "PROFILE_SAVED":
                    profile_key = f"profile_{sender}"
                    args = marker.get("args", {})
                    ctx.storage.set(profile_key, args)
                    ctx.logger.info(f"User profile updated for {sender}")
                    content = "PROFILE_SAVED: User details have been securely stored for future bookings."

            except (json.JSONDecodeError, TypeError):
                pass

            conversation_history.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
            })
        
        conversation_history.append({
            "role": "system",
            "content": "SYSTEM: Tool results received. Synthesize them for the user into a helpful response.",
        })

    # 7. Persist and Send
    persisted_history = [m for m in conversation_history if m.get("role") != "system"]
    ctx.storage.set(history_key, {"messages": persisted_history[-20:], "last_active": time.time()})

    # Sanitize: if the LLM leaked raw tool-call XML into the content, strip it
    import re
    raw_text = assistant_msg.content or ""
    clean_text = re.sub(r"<tool_call>.*?</tool_call>", "", raw_text, flags=re.DOTALL).strip()
    if not clean_text:
        clean_text = "I'm sorry, I encountered an issue processing your request. Please try again."

    await ctx.send(sender, _build_send_response(clean_text))

@chat_proto.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    ctx.logger.info(f"Ack from {sender[:8]} for msg {msg.acknowledged_msg_id}")

async def process_payment_confirmation(ctx: Context, sender: str, transaction_id: str):
    """
    Called by agent.py when Stripe payment is verified.
    Injects the confirmation into history and runs the LLM to finalize booking.
    """
    history_key = f"history_{sender}"
    session_data = ctx.storage.get(history_key) or {}
    messages = session_data.get("messages", [])
    
    # Inject confirmation
    messages.append({
        "role": "user", 
        "content": f"SYSTEM: Stripe payment confirmed (ID: {transaction_id}). The user has successfully paid. You can now proceed with any remaining steps (like summarizing rental conditions or insurance if not already done) and then finalize the booking with `book_car`. DO NOT ask for card details; use the placeholders provided in your instructions."
    })
    
    # We save and then call handle_message with a dummy input to trigger the reasoning loop
    # Actually, we can just call handle_message directly with an empty string but the is_start check might skip it.
    # Better to just run a one-off completion here or call handle_message with a specific trigger.
    
    # Update storage so handle_message sees the new history
    ctx.storage.set(history_key, {"messages": messages, "last_active": time.time()})
    
    # Trigger the agent to respond to the confirmation
    await handle_message(ctx, sender, ChatMessage(
        timestamp=datetime.now(timezone.utc),
        msg_id=str(uuid4()),
        content=[TextContent(type="text", text="The payment has been confirmed. Please finalize my booking.")]
    ))
