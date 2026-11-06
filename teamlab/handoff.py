"""Handoffs: the messages agents pass to each other, and the contract they must meet.

A handoff is an API between two agents. Like any API it can drop fields, carry the
wrong type or assert things that are not true. Unlike most APIs it is written by a
model, so it can also carry instructions that came from somewhere it should not:
a customer note, a web page, another agent.

The contract checks three things:

1. Shape: the fields this intent needs are present and typed.
2. Provenance: an action the customer must have asked for (a refund) quotes the
   customer's own words, verbatim, and the quote supports the order and amount.
   Text an agent read inside a tool result cannot pass, because it is not in the
   customer's message. This is the defence against relayed injection.
3. Claims: anything a handoff asserts as done ("refund issued") must be true in
   the store at the moment it is sent.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from teamlab.store import Store

ORDER_RE = re.compile(r"\b[A-Z][0-9]{4}\b")
AMOUNT_RE = re.compile(r"\$\s?([0-9]+(?:\.[0-9]{1,2})?)")

ROUTES = {("triage", "billing"), ("billing", "comms"), ("billing", "triage"), ("comms", "billing"),
          ("triage", "comms")}
REQUIRED = {
    "refund": ["order_id", "amount", "reason", "customer_quote"],
    "confirm": ["order_id"],
    "clarify": ["reason"],
}
CLAIM_REFUND_ISSUED = "refund issued"


@dataclass
class Handoff:
    sender: str
    receiver: str
    intent: str                       # refund | confirm | clarify
    order_id: str | None = None
    amount: float | None = None
    reason: str = ""
    customer_quote: str = ""          # the customer's own words that justify the action
    facts: dict = field(default_factory=dict)    # what the sender looked up (status, refund_id, ...)
    claims: list[str] = field(default_factory=list)  # what the sender asserts has been done

    def key(self) -> tuple:
        """Identity for loop detection: the same handoff sent again is not progress."""
        return (self.sender, self.receiver, self.intent, self.order_id, self.amount, self.reason)

    def to_dict(self) -> dict:
        return asdict(self)


def violations(h: Handoff, customer_message: str, store: Store) -> list[str]:
    """Every way this handoff breaks the contract. Empty list = valid."""
    out = []
    if (h.sender, h.receiver) not in ROUTES:
        out.append(f"route {h.sender}->{h.receiver} is not allowed")
    if h.intent not in REQUIRED:
        return out + [f"unknown intent '{h.intent}'"]
    for name in REQUIRED[h.intent]:
        if getattr(h, name) in (None, ""):
            out.append(f"missing {name}")
    numeric = h.amount is None or (isinstance(h.amount, (int, float)) and not isinstance(h.amount, bool))
    if not numeric:
        out.append("amount must be a number")      # and skip the amount checks below: they would crash on it

    if h.intent == "refund" and h.customer_quote:
        if h.customer_quote not in customer_message:
            out.append("customer_quote is not in the customer's message (provenance)")
        else:
            quoted_orders = ORDER_RE.findall(h.customer_quote)
            if quoted_orders and h.order_id not in quoted_orders:
                out.append(f"order {h.order_id} is not the one the customer named")
            quoted_amounts = [float(a) for a in AMOUNT_RE.findall(h.customer_quote)]
            if quoted_amounts and numeric and h.amount is not None and all(abs(h.amount - a) > 1e-9 for a in quoted_amounts):
                out.append(f"amount {h.amount} is not the amount the customer asked for")

    if CLAIM_REFUND_ISSUED in h.claims:
        refund = store.refund_by_id(str(h.facts.get("refund_id", "")))
        if refund is None or refund["order_id"] != h.order_id:
            out.append("claims a refund that does not exist (unsupported claim)")
    return out
