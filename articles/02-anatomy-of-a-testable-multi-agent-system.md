# 02. Anatomy of a Testable Multi-Agent System

> **Series:** Multi-Agent Testing Lab · [Index](../README.md#articles) · Previous: [01](01-what-changes-when-agents-work-together.md) · Next: [03. Testing Handoffs](03-testing-handoffs-contracts-provenance-claims.md)

A multi-agent system is testable when you can answer three questions after any run: **what changed in the world**, **what each agent did**, and **what each agent told the next one**. This lab is built so that all three are recorded.

## The pieces

```text
customer message
      │
      ▼
  ┌────────┐  handoff   ┌─────────┐  handoff   ┌───────┐
  │ triage │ ─────────► │ billing │ ─────────► │ comms │ ──► reply + email
  └────────┘            └─────────┘            └───────┘
      ▲                      │
      └──── clarify ─────────┘
               orchestrator: routes, validates every handoff, stops loops, records the trace
```

| File | What it is |
|---|---|
| [`teamlab/store.py`](../teamlab/store.py) | The world: customers, orders, refunds, emails, escalations; fault injection; a planted injection in one order note |
| [`teamlab/tools.py`](../teamlab/tools.py) | Tools and **per-agent permissions**; business rules checked before every call |
| [`teamlab/handoff.py`](../teamlab/handoff.py) | The `Handoff` message and its contract |
| [`teamlab/agents.py`](../teamlab/agents.py) | The three agents, each with a faithful behaviour and faulty ones |
| [`teamlab/team.py`](../teamlab/team.py) | The orchestrator and the trace |
| [`teamlab/blame.py`](../teamlab/blame.py) | Three ways to decide which agent caused a failure |

## Least privilege per agent

```python
PERMISSIONS = {
    "triage":  {"lookup_order", "list_orders", "search_policy", "escalate_to_human"},
    "billing": {"lookup_order", "issue_refund", "escalate_to_human"},
    "comms":   {"lookup_refund", "send_email"},
}
```

The check runs in code before every call, so a confused or manipulated agent still cannot use a tool it was not given. Business rules sit in the same place: only delivered orders, never more than the remaining balance, nothing over $100 without a human, emails only to the signed-in customer, and every refund needs an idempotency key. The customer's identity comes from the session, never from an agent.

## The handoff

```python
@dataclass
class Handoff:
    sender: str; receiver: str
    intent: str                 # refund | confirm | clarify
    order_id: str | None; amount: float | None; reason: str
    customer_quote: str         # the customer's own words that justify the action
    facts: dict                 # what the sender looked up
    claims: list[str]           # what the sender says it has done
```

Two fields are there purely for testing and safety. `customer_quote` ties an action back to what the customer actually wrote (article 04). `claims` makes assertions explicit, so they can be checked against the store (article 03).

## The orchestrator and the trace

The orchestrator runs a queue of work items. For every handoff it validates the contract, checks for loops and counts hops; for every agent it records each tool call, retry, block, handoff and reply as an `Event` **attributed to the agent that did it**. If a run ends without a reply to the customer (a loop, a crash, a dropped handoff), it escalates to a human and sends a holding reply, so the customer is never left with silence.

```text
triage   tool_call  lookup_order   A1001
triage   handoff    -> billing     refund A1001 $45.00
billing  tool_call  issue_refund   key run:A1001:45.00
billing  handoff    -> comms       confirm, claims: refund issued (R001)
comms    tool_call  lookup_refund  R001          <- checks the claim first
comms    tool_call  send_email     ana@example.test
comms    reply      Done: Your refund of $45.00 for order A1001 has been issued.
```

## Faithful and faulty agents

The agents are deterministic rule-based stand-ins for LLM agents. That is a choice for testability: every run is repeatable, so a failing test points at a cause, not at sampling noise. Faults are switched on per agent:

| Agent | Behaviour | What it does wrong |
|---|---|---|
| triage | `lossy` | drops the amount when it writes the handoff |
| triage | `relay` | passes on instructions it read in an order note |
| billing | `guessing` | fills a missing amount with the order total |
| billing | `duplicate` | retries a refund with a **new** idempotency key |
| billing | `overclaim` | tells comms a refund was issued when it was not |
| comms | `trusting` | repeats upstream claims without checking them |

```bash
FAKE_TEAM=triage=relay python -m evals.run_team_evals
FAKE_TEAM="triage=lossy,billing=guessing" python -m evals.run_team_evals --no-contracts
```

Two of these are **latent**: `guessing` does nothing wrong while triage sends complete handoffs, and `trusting` does nothing wrong while billing tells the truth. Each passes every scenario on its own. They only fail in combination, which matters for blame (article 05).

## Scenarios

[`evals/scenarios.jsonl`](../evals/scenarios.jsonl) has 16 scenarios: full and partial refunds, refunds that need a human, shipped orders, another customer's order, a request with no order number, a policy question, the planted injection, and five reliability scenarios with timeouts, an outage and an ambiguous timeout. Each one states the expected world state, which agents should take part, and what the reply must and must not say.
