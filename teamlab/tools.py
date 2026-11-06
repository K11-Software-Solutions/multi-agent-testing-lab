"""Tools, and which agent may use which.

Least privilege per agent: only billing can move money, only comms can email the
customer, and comms can read refunds so it can check a claim before repeating it.
Every tool call goes through `check`, which is deterministic code: it holds even
when an agent is confused or manipulated, but it only knows what a single call
looks like, not whether the customer actually asked for it (see article 04).
"""
from __future__ import annotations

from dataclasses import dataclass

from teamlab.store import POLICY_TEXT, Store

REFUND_APPROVAL_LIMIT = 100.0

PERMISSIONS: dict[str, set[str]] = {
    "triage": {"lookup_order", "list_orders", "search_policy", "escalate_to_human"},
    "billing": {"lookup_order", "issue_refund", "escalate_to_human"},
    "comms": {"lookup_refund", "send_email"},
}
WRITE_TOOLS = {"issue_refund", "send_email", "escalate_to_human"}


class ToolError(Exception):
    """A tool failed permanently (not found, not allowed, service down)."""


class TransientToolError(ToolError):
    """A tool failed in a way that may succeed on retry (timeout, 503)."""


class PolicyBlocked(ToolError):
    """The call was refused before it ran."""


@dataclass
class Session:
    """Who the team is acting for. Tools never take a customer id from an agent."""
    customer: str
    store: Store


def _faults(store: Store, name: str) -> None:
    if name in store.unavailable_tools:
        raise ToolError(f"{name} is unavailable (service down)")
    if store.transient_failures.get(name, 0) > 0:
        store.transient_failures[name] -= 1
        raise TransientToolError(f"{name} timed out")
    if store.failure_rate and store.rng.random() < store.failure_rate:
        raise TransientToolError(f"{name} returned 503 (random fault)")


def _own_order(session: Session, order_id: str) -> dict:
    order = session.store.orders.get(order_id)
    if order is None or order["customer"] != session.customer:   # another customer's order looks missing
        raise ToolError(f"order {order_id} not found")
    return order


def check(agent: str, session: Session, name: str, args: dict) -> None:
    """Permission and business-rule checks before any call runs. Raises PolicyBlocked."""
    if name not in PERMISSIONS.get(agent, set()):
        raise PolicyBlocked(f"agent '{agent}' may not call {name}")
    if name == "issue_refund":
        amount = args.get("amount")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
            raise PolicyBlocked("amount must be a positive number")
        order = session.store.orders.get(args.get("order_id", ""))
        if order is None or order["customer"] != session.customer:
            raise PolicyBlocked(f"order {args.get('order_id')} not found")
        # A replay of a refund that already happened is not a new refund: recognise the idempotency
        # key BEFORE the balance rule, or a correct retry is refused because the first attempt
        # already used the balance (found by scenario reliab-02; see article 07).
        replay = next((r for r in session.store.refunds if r.get("idempotency_key") == args.get("idempotency_key")), None)
        if replay is not None:
            if (replay["order_id"], replay["amount"]) != (args["order_id"], round(float(amount), 2)):
                raise PolicyBlocked("idempotency key reused for a different refund")
            return
        if order["status"] != "delivered":
            raise PolicyBlocked(f"order is {order['status']}; only delivered orders can be refunded")
        remaining = order["total"] - session.store.refunded_total(args["order_id"])
        if amount > remaining + 1e-9:
            raise PolicyBlocked(f"amount exceeds refundable balance of ${remaining:.2f}")
        if amount > REFUND_APPROVAL_LIMIT:
            raise PolicyBlocked(f"refunds over ${REFUND_APPROVAL_LIMIT:.0f} need human approval")
        if not args.get("idempotency_key"):
            raise PolicyBlocked("refunds need an idempotency key")
    if name == "send_email" and str(args.get("to", "")).lower() != session.customer:
        raise PolicyBlocked("emails may only be sent to the customer in this conversation")


# --- tool functions ------------------------------------------------------------------

def lookup_order(session: Session, order_id: str) -> dict:
    _faults(session.store, "lookup_order")
    o = _own_order(session, order_id)
    return {"order_id": order_id, "item": o["item"], "total": o["total"], "status": o["status"],
            "refunded": session.store.refunded_total(order_id), "customer_notes": o["notes"]}


def list_orders(session: Session) -> list[dict]:
    _faults(session.store, "list_orders")
    return [{"order_id": k, "item": o["item"], "status": o["status"]}
            for k, o in sorted(session.store.orders.items()) if o["customer"] == session.customer]


def search_policy(session: Session, topic: str) -> str:
    _faults(session.store, "search_policy")
    return POLICY_TEXT.get(topic, "No policy found for that topic.")


def issue_refund(session: Session, order_id: str, amount: float, reason: str, idempotency_key: str) -> dict:
    store = session.store
    _faults(store, "issue_refund")
    _own_order(session, order_id)
    existing = next((r for r in store.refunds if r["idempotency_key"] == idempotency_key), None)
    if existing:
        return {"status": "refunded", "duplicate_request": True, **existing}
    refund = {"refund_id": f"R{len(store.refunds) + 1:03d}", "order_id": order_id,
              "amount": round(float(amount), 2), "reason": reason, "idempotency_key": idempotency_key}
    store.refunds.append(refund)
    if store.ambiguous_timeouts.get("issue_refund", 0) > 0:   # the money moved, but the caller never hears so
        store.ambiguous_timeouts["issue_refund"] -= 1
        raise TransientToolError("issue_refund timed out (outcome unknown)")
    return {"status": "refunded", **refund}


def lookup_refund(session: Session, refund_id: str) -> dict:
    _faults(session.store, "lookup_refund")
    r = session.store.refund_by_id(refund_id)
    if r is None:
        raise ToolError(f"refund {refund_id} not found")
    return {k: r[k] for k in ("refund_id", "order_id", "amount")}


def send_email(session: Session, to: str, subject: str, body: str) -> dict:
    _faults(session.store, "send_email")
    session.store.emails.append({"to": to, "subject": subject, "body": body})
    return {"status": "sent", "to": to}


def escalate_to_human(session: Session, summary: str) -> dict:
    _faults(session.store, "escalate_to_human")
    ticket = f"T{len(session.store.escalations) + 1:03d}"
    session.store.escalations.append({"ticket": ticket, "summary": summary})
    return {"status": "escalated", "ticket": ticket}


TOOLS = {f.__name__: f for f in (lookup_order, list_orders, search_policy, issue_refund, lookup_refund,
                                 send_email, escalate_to_human)}
