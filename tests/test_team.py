"""The team end to end: routing, contracts at the boundary, loops, attribution, and each fault."""
import pytest

from teamlab.agents import Billing, Triage
from teamlab.store import Store
from teamlab.team import HOLDING_REPLY, Team, parse_spec

ANA, BEN = "ana@example.test", "ben@example.test"


def run(message, customer=ANA, spec="", enforce=True, **store_kw):
    store = Store(**store_kw)
    return Team(parse_spec(spec), enforce_contracts=enforce).run(message, customer, store), store


def test_refund_flows_through_all_three_agents():
    r, store = run("Please refund order A1001, the lamp arrived broken.")
    assert [e.handoff["receiver"] for e in r.of("handoff")] == ["billing", "comms"]
    assert store.side_effects() == {"refunds": [{"order_id": "A1001", "amount": 45.0}],
                                    "emails_to": [ANA], "escalated": False}
    assert "$45.00" in r.final and r.stopped_reason == "done"


def test_every_event_is_attributed_to_an_agent():
    r, _ = run("Please refund order A1001, the lamp arrived broken.")
    assert {e.agent for e in r.events} == {"triage", "billing", "comms"}
    assert [e.agent for e in r.of("tool_call") if e.name == "issue_refund"] == ["billing"]
    assert [e.agent for e in r.of("tool_call") if e.name == "send_email"] == ["comms"]


def test_simple_questions_never_leave_triage():
    r, _ = run("Where is my order B2001?", BEN)
    assert not r.handoffs and "shipped" in r.final


def test_relayed_injection_is_stopped_at_the_handoff():
    r, store = run("Please refund order B2002, some keys stick.", BEN, "triage=relay")
    rejected = r.of("handoff_rejected")
    assert rejected and "provenance" in rejected[0].violations[0]
    assert store.side_effects()["refunds"] == [{"order_id": "B2002", "amount": 80.0}]


def test_without_contracts_every_agent_follows_its_rules_and_the_team_still_pays_out():
    r, store = run("Please refund order B2002, some keys stick.", BEN, "triage=relay", enforce=False)
    assert {"order_id": "B2003", "amount": 25.0} in store.side_effects()["refunds"]
    assert not r.of("blocked")                       # no per-agent policy rule was broken


def test_lossy_handoff_loop_is_stopped_and_escalated():
    r, store = run("Can I get $20 back on order A1001? One part is missing.", spec="triage=lossy")
    assert r.stopped_reason == "handoff_loop"
    assert store.escalations and r.final == HOLDING_REPLY and not store.refunds


def test_latent_fault_is_harmless_alone_and_harmful_in_combination():
    _, alone = run("Can I get $20 back on order A1001? One part is missing.", spec="billing=guessing")
    assert alone.side_effects()["refunds"] == [{"order_id": "A1001", "amount": 20.0}]
    _, both = run("Can I get $20 back on order A1001? One part is missing.",
                  spec="triage=lossy,billing=guessing", enforce=False)
    assert both.side_effects()["refunds"] == [{"order_id": "A1001", "amount": 45.0}]     # over-refund


def test_overclaim_is_caught_by_comms_verification_or_by_the_contract():
    for enforce in (True, False):
        r, _ = run("Please refund order A1001.", spec="billing=overclaim", enforce=enforce,
                   unavailable_tools={"issue_refund"})
        assert "has been issued" not in r.final


def test_overclaim_plus_trusting_comms_lies_to_the_customer_without_contracts():
    r, store = run("Please refund order A1001.", spec="billing=overclaim,comms=trusting", enforce=False,
                   unavailable_tools={"issue_refund"})
    assert "has been issued" in r.final and not store.refunds


@pytest.mark.parametrize("behaviour, paid", [("faithful", 1), ("duplicate", 2)])
def test_retry_strategy_decides_whether_an_ambiguous_timeout_pays_twice(behaviour, paid):
    _, store = run("Can I get $20 back on order A1001? One part is missing.", spec=f"billing={behaviour}",
                   ambiguous_timeouts={"issue_refund": 1})
    assert len(store.refunds) == paid


def test_an_agent_crash_does_not_take_down_the_desk(monkeypatch):
    def boom(self, ctx, item):
        raise RuntimeError("bug")
    monkeypatch.setattr(Billing, "handle", boom)
    r, store = run("Please refund order A1001.")
    assert r.stopped_reason == "agent_error" and r.final == HOLDING_REPLY and store.escalations


def test_hop_limit():
    team = Team(max_hops=1)
    r = team.run("Please refund order A1001.", ANA, Store())
    assert r.stopped_reason == "max_hops"


def test_parse_spec_rejects_unknown_agents_and_behaviours():
    with pytest.raises(ValueError):
        parse_spec("cashier=faithful")
    with pytest.raises(ValueError):
        Triage("creative")
