import logging
from dotenv import load_dotenv
from uagents import Agent, Context, Protocol
from mcp_client import fetch_mcp_tools
from chat_protocol import chat_proto, _build_send_response
from payment_proto import build_payment_proto, CommitPayment, RejectPayment, CompletePayment
from stripe_payments import verify_paid
import time

# Load environment configuration
load_dotenv()

# Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(name)-24s │ %(message)s",
    datefmt="%H:%M:%S",
)

# Suppress noisy logs
logging.getLogger("uagents").setLevel(logging.WARNING)
logging.getLogger("mcp").setLevel(logging.WARNING)

# Initialize Enterprise Agent
agent = Agent(
    name="travelopro_agent",
    port=8006,
    seed="enterprise_agent_seed_phrase",
    mailbox=True
)

@agent.on_event("startup")
async def startup(ctx: Context):
    """
    At startup, connect to the Travelopro MCP server to fetch tool definitions.
    """
    ctx.logger.info("Bootstrapping: Fetching tools from Travelopro MCP...")
    
    # 1. Cleanup old storage entries
    try:
        import time
        storage_dict = getattr(ctx.storage, "_data", {})
        count = 0
        for key in list(storage_dict.keys()):
            if key.startswith("mcp_cache:"):
                val = storage_dict.get(key)
                if isinstance(val, dict) and time.time() > val.get("expiry", 0):
                    storage_dict.pop(key, None)
                    count += 1
        if count > 0:
            ctx.logger.info(f"[MCP] [CLEANUP] Pruned {count} stale cache entries.")
    except Exception as e:
        ctx.logger.debug(f"Cleanup failure: {e}")

    # 2. Fetch Tools
    tools_metadata = await fetch_mcp_tools()
    if tools_metadata:
        ctx.storage.set("tools_metadata", tools_metadata)
        ctx.logger.info(f"Bootstrap complete. {len(tools_metadata)} Travelopro tool(s) loaded.")
    else:
        ctx.logger.warning("Bootstrap failed: Could not load tools. Agent will attempt lazy bootstrap on first message.")
        ctx.storage.set("tools_metadata", [])


async def on_payment_commit(ctx: Context, sender: str, msg: CommitPayment):
    """Handle successful payment confirmation from the user."""
    if msg.funds.payment_method != "stripe" or not msg.transaction_id:
        await ctx.send(sender, RejectPayment(reason="Unsupported payment method (expected stripe)."))
        return

    ctx.logger.info(f"Payment commit received from {sender}: {msg.transaction_id}")
    
    # 1. Verify with Stripe
    try:
        paid = await verify_paid(msg.transaction_id)
    except Exception as e:
        ctx.logger.error(f"Stripe verification failed: {e}")
        await ctx.send(sender, RejectPayment(reason="Stripe verification failed."))
        return
        
    if not paid:
        await ctx.send(sender, RejectPayment(reason="Payment not confirmed by Stripe."))
        return
        
    # 2. Complete payment protocol
    await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))
    
    # 3. Trigger automatic booking finalization
    from chat_protocol import process_payment_confirmation
    await process_payment_confirmation(ctx, sender, msg.transaction_id)

async def on_payment_reject(ctx: Context, sender: str, msg: RejectPayment):
    """Handle rejected payment."""
    ctx.logger.warning(f"Payment rejected by {sender}: {msg.reason}")
    await ctx.send(sender, _build_send_response(f"Payment was not completed: {msg.reason}. Let me know if you'd like to try again or pick another car."))

# Register the protocols
agent.include(chat_proto, publish_manifest=True)
agent.include(build_payment_proto(on_payment_commit, on_payment_reject), publish_manifest=True)

if __name__ == "__main__":
    agent.run()
