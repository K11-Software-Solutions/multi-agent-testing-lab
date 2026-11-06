# 03. Testing Handoffs: Contracts, Provenance and Claims

> **Series:** Multi-Agent Testing Lab · [Index](../README.md#articles) · Previous: [02](02-anatomy-of-a-testable-multi-agent-system.md) · Next: [04. Confused Deputies](04-confused-deputies-relayed-injection.md)

When two microservices talk, nobody accepts "the payload is usually fine". There is a schema, a contract test and validation at the boundary. A handoff between agents deserves the same treatment, with one difference: it is written by a model, so it can be wrong in ways a serializer never is.

## The contract has three parts

[`teamlab/handoff.py`](../teamlab/handoff.py) checks every handoff for:

| Part | Question | Example violation |
|---|---|---|
| **Shape** | Are the fields this intent needs present and typed? | `missing amount`, `amount must be a number`, `route comms->triage is not allowed` |
| **Provenance** | Did the customer actually ask for this? | `customer_quote is not in the customer's message`, `order A1002 is not the one the customer named`, `amount 45.0 is not the amount the customer asked for` |
| **Claims** | Is what the sender says it did actually true? | `claims a refund that does not exist (unsupported claim)` |

Shape is ordinary API validation. The other two are specific to agents.

### Provenance

A refund handoff must carry `customer_quote`: the sentence of the customer's message that asks for it, **verbatim**. The check is mechanical:

1. the quote must appear in the customer's message exactly;
2. if the quote names an order, the handoff must be for that order;
3. if the quote names an amount, the handoff must be for that amount.

Text an agent read inside a tool result (an order note, a web page, another agent's output) cannot pass, because it is not in the customer's message. Article 04 shows why that matters.

### Claims

A handoff that says `"refund issued"` must point at a refund that exists in the store **when it is sent**. A claim is a test oracle you can check, as long as the system of record is reachable.

## Where to enforce it

The orchestrator validates every handoff before delivering it, and can run in two modes:

- **Enforce** (default). A handoff with only shape problems goes back to its sender as a `clarify` request. A handoff that fails provenance or makes an unsupported claim is **dropped** and logged: sending it back would give a manipulated agent another try.
- **Monitor** (`--no-contracts`). Violations are recorded in the trace but the handoff is delivered anyway. This is how you would roll contracts out on a live system: measure first, then enforce.

Even in monitor mode, the eval counts every violation, so the gate still fails. You see the problem either way; enforcement decides whether the customer is affected by it.

## Testing the contract itself

[`tests/test_handoff.py`](../tests/test_handoff.py) tests each rule directly, without agents: a complete handoff is valid; each missing field, wrong type and illegal route is reported; a quote from the order note fails provenance; a mismatched order or amount fails; a claim fails until the refund exists and passes after.

Writing those tests found a real bug. A handoff whose amount was the **text** `"20"` did not produce a violation: it **crashed** the validator, because the amount check subtracted a string from a number. A validator that crashes on malformed input is a validator that a malformed handoff can take down. The fix records "amount must be a number" and skips the checks that need a number. That is the first rule of contract testing, and it applies to agents too: **test the validator with the inputs it exists to reject.**

## What contracts cannot do

- **They check structure, not meaning.** A handoff that quotes the customer correctly but misreads the request still passes.
- **Verbatim provenance is strict.** It works because agents copy the customer's sentence. An agent that paraphrases the request ("customer wants their money back") fails provenance even when it is right. With real LLM agents, either instruct them to quote, or check provenance at the **span** level (the tool returns character offsets into the message) instead of matching strings.
- **Claims need a system of record.** If the refund service is down, a true claim cannot be confirmed. Comms then has to choose between caution and helpfulness (scenario `reliab-04`, article 07).

## Takeaways

- **A handoff is an API: give it a contract and validate it at the boundary.**
- **Check where a request came from, not only what it says.**
- **Make claims explicit so they can be verified.**
- **Roll out in monitor mode, then enforce.**
- **Test the validator with bad input; ours crashed on it.**
