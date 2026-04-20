from typing import Awaitable, Callable
from uagents import Context, Protocol
from uagents_core.contrib.protocols.payment import (
    CommitPayment,
    RejectPayment,
    payment_protocol_spec,
    RequestPayment,
    Funds,
    CompletePayment
)

CommitHandler = Callable[[Context, str, CommitPayment], Awaitable[None]]
RejectHandler = Callable[[Context, str, RejectPayment], Awaitable[None]]

def build_payment_proto(on_commit: CommitHandler, on_reject: RejectHandler) -> Protocol:
    """Build the payment protocol for the agent."""
    proto = Protocol(spec=payment_protocol_spec, role="seller")

    @proto.on_message(CommitPayment)
    async def _on_commit(ctx: Context, sender: str, msg: CommitPayment):
        await on_commit(ctx, sender, msg)

    @proto.on_message(RejectPayment)
    async def _on_reject(ctx: Context, sender: str, msg: RejectPayment):
        await on_reject(ctx, sender, msg)

    return proto
