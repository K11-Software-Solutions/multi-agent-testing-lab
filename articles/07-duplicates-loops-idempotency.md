# 07. Duplicates, Loops and Idempotency

> **Series:** Multi-Agent Testing Lab · [Index](../README.md#articles) · Previous: [06](06-reliability-compounds-across-agents.md) · Next: [08. CI Gates, Negative Controls and Limitations](08-ci-gates-negative-controls-limitations.md)

Retries make a team more reliable (article 06). They also make it possible to do the same thing twice. And when agents can send work back to each other, they can send it back and forth forever.

## The ambiguous timeout

The dangerous failure is not "the refund failed". It is **"the refund went through, and the reply was lost"**. The caller sees a timeout and cannot tell whether the money moved. The store reproduces it:

```python
Store(ambiguous_timeouts={"issue_refund": 1})   # performs the refund, then reports a timeout
```

Every refund carries an **idempotency key**. A second request with the same key returns the first refund instead of paying again. That only works if the agent **reuses the key** when it retries. The faithful billing agent derives the key from the request (`run:order:amount`); the `duplicate` behaviour adds the attempt number, so each retry looks like a new payment.

## Experiment E3

200 runs per row; in each run the first refund attempt has the given chance of an ambiguous timeout.

| Retry strategy | Refund | Timeout rate | Duplicate payments | Overpaid | Customer told it failed |
|---|---|---|---|---|---|
| same key | full ($45) or partial ($20) | 0.1–0.5 | **0** | $0 | 0 |
| new key per attempt | full ($45) | 0.5 | 0 | $0 | **106** |
| new key per attempt | partial ($20) | 0.5 | **106** | **$2,120** | 0 |

Two different failures come from the same bug:

- **Partial refunds pay twice.** The balance allows a second $20, so the retry succeeds and the customer gets $40.
- **Full refunds tell the customer it failed.** The retry is refused because the first attempt already used the whole balance, so billing reports "couldn't refund" for a refund that went through. No money is lost, but the customer is told something false and will probably ask again.

A test suite that only tried full refunds would conclude the duplicate behaviour was harmless, because the balance rule hides it. Scenario `reliab-05` exists for exactly this reason: **test idempotency with an operation that is allowed to happen twice.**

## A bug in the defence itself

Scenario `reliab-02` (a full refund with an ambiguous timeout) failed even for the **faithful** team when it first ran. Billing retried correctly, with the same key, and the policy layer refused it: "amount exceeds refundable balance of $0.00". The policy checked the balance **before** it checked the idempotency key, so a correct replay looked like a second refund.

The fix recognises a replayed key first, and refuses a key reused for a *different* refund:

```python
replay = next((r for r in store.refunds if r["idempotency_key"] == args["idempotency_key"]), None)
if replay is not None:
    if (replay["order_id"], replay["amount"]) != (args["order_id"], amount):
        raise PolicyBlocked("idempotency key reused for a different refund")
    return
```

The ordering lesson applies well beyond agents: **idempotency must be checked before any rule that the first attempt changes.**

## Loops

With the `lossy` fault, triage drops the amount, billing sends a `clarify` back, and triage re-sends the same incomplete handoff. Two agents, each behaving reasonably on its own, loop.

The orchestrator stops it two ways:

- **Repeated handoffs.** Each handoff has an identity (sender, receiver, intent, order, amount, reason). The same handoff sent more than twice is not progress, and the run stops with `handoff_loop`.
- **A hop limit** (8 by default) catches loops that change a field every time.

When a run stops without a reply, the orchestrator escalates to a human and sends a holding reply. In the CI report, the lossy team completes only half of the scenarios, and every incomplete one ends in an escalation, not silence. `completion_rate` is gated at 1.0, so the faulty team fails the build.

## Takeaways

- **The dangerous failure is "it worked, but I didn't hear back".**
- **Derive idempotency keys from the request, and reuse them on retry.**
- **Test idempotency with an operation that is allowed to happen twice**, or other rules will hide the duplicates.
- **Check idempotency before any rule the first attempt changes.**
- **Detect repeated handoffs, cap hops, and never end a run in silence.**
