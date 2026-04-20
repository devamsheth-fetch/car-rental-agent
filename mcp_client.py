import os
import json
import logging
import hashlib
import time
from contextlib import AsyncExitStack
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


def _get_server_params() -> StdioServerParameters:
    """Return StdioServerParameters for the Travelopro MCP server."""
    command = os.getenv("TRAVELOPRO_MCP_COMMAND", "python")
    args_str = os.getenv("TRAVELOPRO_MCP_ARGS", "mcp-server/server.py")
    args = args_str.split(",") if "," in args_str else [args_str]
    return StdioServerParameters(command=command, args=args)


def mcp_to_openai_tool(mcp_tool):
    """Maps an MCP tool definition to the OpenAI function-calling schema."""
    return {
        "type": "function",
        "function": {
            "name": mcp_tool.name,
            "description": mcp_tool.description,
            "parameters": mcp_tool.inputSchema,
        },
    }


async def fetch_mcp_tools() -> list:
    """Connect to the Travelopro MCP server and return its OpenAI-formatted tool list."""
    params = _get_server_params()
    try:
        async with AsyncExitStack() as stack:
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            tools_result = await session.list_tools()
            tools = [mcp_to_openai_tool(t) for t in tools_result.tools]
            
            # Add virtual Stripe tool
            tools.append({
                "type": "function",
                "function": {
                    "name": "trigger_stripe_payment",
                    "description": "Trigger a Stripe payment request to the user. Use this when the user selects a PREPAID car. Requires the amount in cents and a description.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "amount_cents": {"type": "integer", "description": "The amount to charge in cents (e.g. 1000 for $10.00)"},
                            "description": {"type": "string", "description": "Description of the car being booked"}
                        },
                        "required": ["amount_cents", "description"]
                    }
                }
            })
            
            # Add virtual User Profile tool
            tools.append({
                "type": "function",
                "function": {
                    "name": "save_user_profile",
                    "description": "Save user personal details (name, email, phone, address) to the agent's persistent storage for future bookings.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "enum": ["Mr", "Ms", "Mrs", "Miss"]},
                            "first_name": {"type": "string"},
                            "last_name": {"type": "string"},
                            "email": {"type": "string"},
                            "phone": {"type": "string", "description": "Phone with area code"},
                            "address": {"type": "string"},
                            "city": {"type": "string"},
                            "state": {"type": "string"},
                            "country": {"type": "string"},
                            "zip": {"type": "string"}
                        },
                        "required": ["first_name", "last_name", "email", "phone"]
                    }
                }
            })

            logger.info(f"[MCP] Travelopro tools loaded: {[t.name for t in tools_result.tools]} + trigger_stripe_payment + save_user_profile")
            return tools
    except Exception as e:
        logger.error(f"[MCP] Failed to fetch Travelopro tools: {e}")
        return []


async def _call_single_tool(session, tool_call, storage) -> tuple:
    """Execute a single MCP tool call within an already-open session."""
    t_name = tool_call.function.name
    t_args = json.loads(tool_call.function.arguments)
    logger.info(f"[MCP] Calling tool '{t_name}'")

    if t_name == "trigger_stripe_payment":
        # Special case: virtual tool handled by agent.py / chat_protocol.py
        # We return a marker that the chat protocol will recognize
        return (tool_call.id, json.dumps({"status": "PAYMENT_REQUESTED", "args": t_args})), 1

    if t_name == "save_user_profile":
        # Special case: virtual tool for persistence
        return (tool_call.id, json.dumps({"status": "PROFILE_SAVED", "args": t_args})), 1

    try:
        tool_result = await session.call_tool(t_name, t_args)
        raw_content = "".join(
            part.text for part in tool_result.content if hasattr(part, "text")
        )
        # Cache result
        ttl = 300 if "search" in t_name else 3600
        args_hash = hashlib.md5(json.dumps(t_args, sort_keys=True).encode()).hexdigest()
        storage.set(f"mcp_cache:{t_name}:{args_hash}", {"content": raw_content, "expiry": time.time() + ttl})
        return (tool_call.id, raw_content), 1
    except Exception as e:
        logger.error(f"[MCP] Tool '{t_name}' failed: {e}")
        return (tool_call.id, f"Error calling '{t_name}': {str(e)}"), 1


async def execute_mcp_tools(storage, tool_calls, status_callback=None) -> tuple[list, int]:
    """Open a session to the Travelopro MCP server and execute all tool calls."""
    params = _get_server_params()
    results = []
    try:
        async with AsyncExitStack() as stack:
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()

            for tc in tool_calls:
                res, _ = await _call_single_tool(session, tc, storage)
                results.append(res)
    except Exception as e:
        logger.error(f"[MCP] Critical session failure: {e}")
        for tc in tool_calls:
            results.append((tc.id, f"Session error: {str(e)}"))

    return results, len(tool_calls)
