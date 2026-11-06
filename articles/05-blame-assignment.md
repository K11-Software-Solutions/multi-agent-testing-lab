# 05. Blame Assignment: Which Agent Broke It?

> **Series:** Multi-Agent Testing Lab · [Index](../README.md#articles) · Previous: [04](04-confused-deputies-relayed-injection.md) · Next: [06. Reliability Compounds Across Agents](06-reliability-compounds-across-agents.md)

When a single agent fails, you know what to fix. When a team fails, you first have to find out **which agent** to fix. Get that wrong and you spend a sprint improving an agent that was never the problem.

## Three methods

[`teamlab/blame.py`](../teamlab/blame.py) implements three, from cheapest to most expensive:

| Method | How it works | Cost |
|---|---|---|
| **Last actor** | blame the agent that last spoke to the customer | free |
| **First violation** | walk the trace and blame the first observable departure: a handoff that breaks its contract, a blocked attempt to email someone else, or the same payment made under a new key | free (the trace already exists) |
| **Counterfactual replay** | re-run the scenario, swapping one agent at a time for a known-good reference version; blame the agent whose swap makes the run pass; if none does, try pairs | one extra run per agent (here, four replays per failure) |

## The experiment

Experiment E2 ([`experiments/run_experiments.py`](../experiments/run_experiments.py)) injects faults with **known ground truth** (four single faults, two pairs of faults and two latent faults on their own), with handoff contracts on and off, and asks each method to name the culprit:

| On the 12 failed runs | Exactly right | Names at least one faulty agent |
|---|---|---|
| Last actor | 0.08 | 0.33 |
| First violation | 0.67 | **1.00** |
| Counterfactual replay | 0.67 | **1.00** |

**Last actor is almost always wrong.** Comms delivers the news, so comms gets blamed: for the relayed injection, the lost amount, the duplicate payment. It is the messenger. Yet "the agent that talked to the customer" is exactly what a support ticket, a complaint or a quick look at the logs points to.

## Why neither good method is always exact

Both good methods named a faulty agent every time, but each was exactly right on only two thirds of the runs. The misses are the **pairs** of faults:

- **triage=lossy + billing=guessing.** Triage drops the amount; billing guesses the full total; a $20 request becomes a $45 refund. Both methods blame **triage**. Swapping triage for the reference version fixes the run on its own, because billing's guessing is latent: it never fires while handoffs are complete.
- **billing=overclaim + comms=trusting.** Billing claims a refund that did not happen; comms repeats it. Both methods blame **billing**.

In both cases the answer is **true but incomplete**. Counterfactual replay answers "what is the smallest change that makes this pass?", which is not the same question as "who is at fault?". The latent fault stays hidden until the upstream fault is fixed, and then it is waiting for the next upstream fault.

The two latent faults on their own (`billing=guessing`, `comms=trusting`) **pass every scenario they were tested on**. No end-to-end test, however good, finds a fault that the rest of the system never triggers. That is what tests with deliberately bad inputs are for: `test_latent_fault_is_harmless_alone_and_harmful_in_combination` runs billing's guessing behaviour alone (it passes) and behind a triage that drops the amount (it over-refunds), so the latent fault is visible without waiting for the upstream one.

## A trace that lies by omission

First violation initially **missed the duplicate payment**. The first refund attempt timed out after the money moved, so the trace recorded it as `tool_error`, and the method only counted successful calls as payments. Reading "error" as "nothing happened" is exactly how duplicates hide in real logs. The fix treats a write whose outcome is unknown as possibly done:

```python
if e.name == "issue_refund" and (e.kind == "tool_call" or "outcome unknown" in e.detail):
```

## A practical recipe

1. **Never blame the last speaker by default.**
2. **Start with first violation:** it is free and it was never wrong about *who* departed first in these runs, as long as the contract checks can see the departure.
3. **Confirm with counterfactual replay** when you have reference versions (the last release of each agent works well), and when runs are deterministic enough to replay. With real LLMs, replay with a fixed seed or temperature 0 and repeat a few times.
4. **After fixing the blamed agent, re-run the failing scenario with the other agents' inputs perturbed**, to look for latent faults the fix has just uncovered.

## Takeaways

- **The agent that delivers bad news is rarely the agent that caused it.**
- **Contracts make blame cheap:** the first violation is in the trace.
- **Counterfactual replay finds the smallest fix, not every culprit.**
- **Latent faults pass end-to-end tests; test components with bad inputs.**
- **Treat "outcome unknown" as "maybe done", in code and in traces.**
