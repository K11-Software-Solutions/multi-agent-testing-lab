"""Tools and per-agent permissions: least privilege, business rules, idempotency."""
import pytest

from teamlab import tools
from teamlab.tools import PolicyBlocked, ToolError, TransientToolError, check


def refund_args(**kw):
    return {"order_id": "A1001", "amount": 45.0, "reason": "broken", "idempotency_key": "k1", **kw}


@pytest.mark.parametrize("agent, tool, allowed", [
    ("triage", "lookup_order", True), ("triage", "issue_refund", False), ("triage", "send_email", False),
    ("billing", "issue_refund", True), ("billing", "send_email", False),
    ("comms", "send_email", True), ("comms", "issue_refund", False), ("comms", "lookup_refund", True),
])
def test_least_privilege(ana, agent, tool, allowed):
    args = {"issue_refund": refund_args(), "send_email": {"to": "ana@example.test", "subject": "s", "body": "b"},
            "lookup_order": {"order_id": "A1001"}, "lookup_refund": {"refund_id": "R001"}}[tool]
    if allowed:
        check(agent, ana, tool, args)
    else:
        with pytest.raises(PolicyBlocked, match="may not call"):
            check(agent, ana, tool, args)


@pytest.mark.parametrize("args, message", [
    (refund_args(order_id="B2002"), "not found"),                 # another customer's order
    (refund_args(order_id="C3001"), "not found"),
    (refund_args(amount=50.0), "refundable balance"),
    (refund_args(order_id="A1002", amount=150.0), "human approval"),
    (refund_args(amount=-5), "positive"),
    (refund_args(idempotency_key=""), "idempotency key"),
])
def test_refund_rules(ana, args, message):
    with pytest.raises(PolicyBlocked, match=message):
        check("billing", ana, "issue_refund", args)


def test_shipped_orders_cannot_be_refunded(ben):
    with pytest.raises(PolicyBlocked, match="shipped"):
        check("billing", ben, "issue_refund", refund_args(order_id="B2001", amount=10.0))


def test_email_only_to_the_signed_in_customer(ana):
    with pytest.raises(PolicyBlocked, match="only be sent"):
        check("comms", ana, "send_email", {"to": "audit@evil.test", "subject": "s", "body": "b"})


def test_same_idempotency_key_never_pays_twice(ana, store):
    first = tools.issue_refund(ana, **refund_args())
    check("billing", ana, "issue_refund", refund_args())          # a replay passes the balance rule
    second = tools.issue_refund(ana, **refund_args())
    assert second["refund_id"] == first["refund_id"] and second["duplicate_request"]
    assert len(store.refunds) == 1


def test_reusing_a_key_for_a_different_refund_is_blocked(ana):
    tools.issue_refund(ana, **refund_args(amount=10.0))
    with pytest.raises(PolicyBlocked, match="different refund"):
        check("billing", ana, "issue_refund", refund_args(amount=20.0))


def test_ambiguous_timeout_acts_then_reports_failure(ana, store):
    store.ambiguous_timeouts["issue_refund"] = 1
    with pytest.raises(TransientToolError, match="outcome unknown"):
        tools.issue_refund(ana, **refund_args())
    assert len(store.refunds) == 1                                 # the money moved anyway


def test_fault_injection(ana, store):
    store.transient_failures["lookup_order"] = 1
    with pytest.raises(TransientToolError):
        tools.lookup_order(ana, "A1001")
    assert tools.lookup_order(ana, "A1001")["status"] == "delivered"
    store.unavailable_tools.add("lookup_refund")
    with pytest.raises(ToolError, match="unavailable"):
        tools.lookup_refund(ana, "R001")
