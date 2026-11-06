"""The three agents: triage, billing and comms.

They are deterministic rule-based stand-ins for LLM agents, so every test and CI run
is repeatable and needs no API key. Each has a `faithful` behaviour and one or more
faulty behaviours that reproduce a real multi-agent failure mode:

    triage   faithful | lossy     drops the amount when it summarises the request
                      | relay     passes on instructions it read in an order note
    billing  faithful | guessing  fills a missing amount with the order total
                      | duplicate retries a refund with a NEW idempotency key
                      | overclaim tells comms a refund was issued when it was not
    comms    faithful | trusting  repeats upstream claims to the customer without checking

Some faults are latent: `guessing` does nothing wrong while triage sends complete
handoffs, and `trusting` does nothing wrong while billing tells the truth. They only
show when combined with an upstream fault (article 05).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from teamlab.handoff import AMOUNT_RE, CLAIM_REFUND_ISSUED, ORDER_RE, Handoff
from teamlab.tools import PolicyBlocked, ToolError, TransientToolError

BEHAVIOURS = {
    "triage": ("faithful", "lossy", "relay"),
    "billing": ("faithful", "guessing", "duplicate", "overclaim"),
    "comms": ("faithful", "trusting"),
}
REFUND_WORDS = re.compile(r"\b(refund|refunded|money back|reimburse)\b", re.I)
STATUS_WORDS = re.compile(r"\b(where|status|track|arrive|shipped)\b", re.I)
POLICY_WORDS = {"return": "returns", "ship": "shipping", "refund policy": "refund", "policy": "refund"}


@dataclass
class Output:
    handoffs: list[Handoff] = field(default_factory=list)
    reply: str | None = None


def sentence_with(text: str, pattern: re.Pattern) -> str:
    """The sentence of the customer's message that contains the request, verbatim."""
    for s in re.split(r"(?<=[.!?])\s+", text.strip()):
        if pattern.search(s):
            return s
    return ""


def wants_refund(text: str) -> bool:
    return bool(REFUND_WORDS.search(text) or (AMOUNT_RE.search(text) and re.search(r"\bback\b", text, re.I)))


class Agent:
    role = ""

    def __init__(self, behaviour: str = "faithful"):
        if behaviour not in BEHAVIOURS[self.role]:
            raise ValueError(f"unknown {self.role} behaviour '{behaviour}'")
        self.behaviour = behaviour

    def call_with_retry(self, ctx, name: str, args: dict, retries: int = 2):
        """Retry transient errors with the SAME arguments. Raises the last error."""
        for attempt in range(retries + 1):
            try:
                return ctx.call(name, args)
            except TransientToolError:
                if attempt == retries:
                    raise
                ctx.note("retry", name, args)


class Triage(Agent):
    """Front desk: reads the customer's message, looks things up, answers or hands off."""
    role = "triage"

    def handle(self, ctx, item) -> Output:
        if isinstance(item, Handoff):            # a handoff came back: billing needs something
            return self._clarify(ctx, item)
        return self._request(ctx, item)

    def _request(self, ctx, text: str) -> Output:
        orders = ORDER_RE.findall(text)
        if wants_refund(text):
            return self._refund(ctx, text, orders)
        if orders and STATUS_WORDS.search(text):
            return self._status(ctx, orders[0])
        for word, topic in POLICY_WORDS.items():
            if word in text.lower():
                return Output(reply=self.call_with_retry(ctx, "search_policy", {"topic": topic}))
        return Output(reply="I can help with refunds, order status and store policy. What do you need?")

    def _lookup(self, ctx, order_id: str):
        try:
            return self.call_with_retry(ctx, "lookup_order", {"order_id": order_id}), None
        except ToolError as exc:
            if "not found" in str(exc):
                return None, Output(reply=f"I couldn't find order {order_id} on your account.")
            ctx.call("escalate_to_human", {"summary": f"Order system unavailable while handling {order_id}"})
            return None, Output(reply="Our order system is not responding, so I've passed this to a colleague.")

    def _status(self, ctx, order_id: str) -> Output:
        order, failed = self._lookup(ctx, order_id)
        if failed:
            return failed
        return Output(reply=f"Order {order_id} ({order['item']}) is {order['status']}.")

    def _refund(self, ctx, text: str, orders: list[str]) -> Output:
        if not orders:
            return Output(reply="Which order would you like refunded? Please send the order number, for example A1001.")
        order_id = orders[0]
        order, failed = self._lookup(ctx, order_id)
        if failed:
            return failed
        if order["status"] != "delivered":
            return Output(reply=f"Order {order_id} is {order['status']}; it can be refunded once it has been delivered.")
        remaining = round(order["total"] - order["refunded"], 2)
        if remaining <= 0:
            return Output(reply=f"Order {order_id} has already been refunded in full.")
        asked = [float(a) for a in AMOUNT_RE.findall(text)]
        amount = asked[0] if asked else remaining
        if amount > remaining:
            return Output(reply=f"The most I can refund on order {order_id} is ${remaining:.2f}.")
        if amount > 100:
            ctx.call("escalate_to_human", {"summary": f"Refund of ${amount:.2f} on {order_id} needs approval"})
            return Output(reply=f"A refund of ${amount:.2f} needs approval, so I've passed it to a colleague.")
        quote = sentence_with(text, REFUND_WORDS) or sentence_with(text, AMOUNT_RE)
        handoff = Handoff("triage", "billing", "refund", order_id=order_id, amount=amount,
                          reason=f"customer request: {order['item']}", customer_quote=quote,
                          facts={"order_status": order["status"], "remaining": remaining})
        if self.behaviour == "lossy":
            handoff.amount = None                    # the summary kept the order and the reason, not the amount
        out = Output(handoffs=[handoff])
        if self.behaviour == "relay" and "BILLING TEAM" in order["customer_notes"]:
            out.handoffs += self._relay(ctx, order["customer_notes"])
        return out

    def _relay(self, ctx, note: str) -> list[Handoff]:
        """The faulty behaviour: treat text found in a tool result as a request to pass on."""
        out = []
        for oid in ORDER_RE.findall(note):
            order, failed = self._lookup(ctx, oid)
            if failed or order["status"] != "delivered":
                continue
            out.append(Handoff("triage", "billing", "refund", order_id=oid, amount=order["total"],
                               reason="requested in order note", customer_quote=sentence_with(note, ORDER_RE),
                               facts={"order_status": order["status"], "cc": "audit@evil.test"}))
        return out

    def _clarify(self, ctx, h: Handoff) -> Output:
        if self.behaviour == "lossy":                # re-summarises the same request and loses the amount again
            again = Handoff("triage", "billing", "refund", order_id=h.order_id, amount=None,
                            reason=h.facts.get("original_reason", "customer request"),
                            customer_quote=h.facts.get("original_quote", ""), facts={"order_status": "delivered"})
            return Output(handoffs=[again])
        return Output(reply=f"Could you tell me how much you'd like refunded on order {h.order_id}?")


class Billing(Agent):
    """Moves money. The only agent allowed to call issue_refund."""
    role = "billing"

    def handle(self, ctx, h: Handoff) -> Output:
        if h.intent != "refund":
            return Output()
        amount = h.amount
        if amount is None:
            if self.behaviour != "guessing":
                return Output(handoffs=[Handoff("billing", "triage", "clarify", order_id=h.order_id,
                                                reason="refund handoff has no amount",
                                                facts={"original_reason": h.reason, "original_quote": h.customer_quote})])
            order = self.call_with_retry(ctx, "lookup_order", {"order_id": h.order_id})
            amount = round(order["total"] - order["refunded"], 2)    # the guess: "probably the whole thing"
        base_key = f"{ctx.run_id}:{h.order_id}:{amount:.2f}"
        refund, status, detail = None, "failed", ""
        for attempt in range(3):
            key = base_key if self.behaviour != "duplicate" else f"{base_key}:{attempt}"
            try:
                refund = ctx.call("issue_refund", {"order_id": h.order_id, "amount": amount,
                                                   "reason": h.reason, "idempotency_key": key})
                status = "issued"
                break
            except PolicyBlocked as exc:
                status, detail = "blocked", str(exc)
                break
            except TransientToolError as exc:
                detail = str(exc)
                if attempt < 2:
                    ctx.note("retry", "issue_refund", {"idempotency_key": key})
            except ToolError as exc:
                detail = str(exc)
                break
        if status == "blocked" and "human approval" in detail:
            ctx.call("escalate_to_human", {"summary": f"Refund of ${amount:.2f} on {h.order_id} needs approval"})
        facts = {"refund_status": status, "amount": amount, "detail": detail}
        claims = []
        if refund:
            facts["refund_id"] = refund["refund_id"]
            claims = [CLAIM_REFUND_ISSUED]
        elif self.behaviour == "overclaim":
            facts.update(refund_status="issued", refund_id="R-pending")
            claims = [CLAIM_REFUND_ISSUED]
        if "cc" in h.facts:
            facts["cc"] = h.facts["cc"]
        return Output(handoffs=[Handoff("billing", "comms", "confirm", order_id=h.order_id, amount=amount,
                                        reason=h.reason, facts=facts, claims=claims)])


class Comms(Agent):
    """Talks to the customer. The only agent allowed to send email."""
    role = "comms"

    def handle(self, ctx, h: Handoff) -> Output:
        if h.intent != "confirm":
            return Output()
        f = h.facts
        if f.get("refund_status") == "issued":
            if self.behaviour == "faithful" and not self._verified(ctx, h):
                return Output(reply=f"I'm sorry, I couldn't confirm the refund for order {h.order_id} yet. "
                                    "A colleague will follow up by email.")
            text = f"Your refund of ${h.amount:.2f} for order {h.order_id} has been issued."
            self._email(ctx, ctx.customer, text)
            if self.behaviour == "trusting" and f.get("cc"):
                self._email(ctx, f["cc"], text)       # blocked by policy: comms may only email the customer
            return Output(reply=f"Done: {text} A confirmation email is on its way.")
        if f.get("refund_status") == "blocked":
            if "human approval" in f.get("detail", ""):
                return Output(reply=f"The refund on order {h.order_id} needs approval, so I've passed it to a colleague.")
            return Output(reply=f"I couldn't refund order {h.order_id}: {f.get('detail')}.")
        return Output(reply=f"I couldn't complete the refund for order {h.order_id} right now. "
                            "A colleague will follow up.")

    def _verified(self, ctx, h: Handoff) -> bool:
        """Check the claim against the system of record before repeating it to the customer."""
        try:
            r = self.call_with_retry(ctx, "lookup_refund", {"refund_id": str(h.facts.get("refund_id", ""))})
        except ToolError:
            return False
        return r["order_id"] == h.order_id and abs(r["amount"] - h.amount) < 1e-9

    def _email(self, ctx, to: str, text: str) -> None:
        try:
            self.call_with_retry(ctx, "send_email", {"to": to, "subject": "Your refund", "body": text})
        except ToolError:
            pass                                       # blocked or down: the trace records it


AGENTS = {"triage": Triage, "billing": Billing, "comms": Comms}
