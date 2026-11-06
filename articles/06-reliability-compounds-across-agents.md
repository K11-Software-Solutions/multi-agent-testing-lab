# 06. Reliability Compounds Across Agents

> **Series:** Multi-Agent Testing Lab · [Index](../README.md#articles) · Previous: [05](05-blame-assignment.md) · Next: [07. Duplicates, Loops and Idempotency](07-duplicates-loops-idempotency.md)

Splitting a job across agents gives each one a smaller task. It also means a request now depends on more steps, and every step can fail.

## The experiment

Experiment E1 makes every tool call fail at random with probability *p*, gives each call two retries, and runs each scenario with 200 seeds. It compares requests that stay with one agent and use one tool call (an order status, a policy question) with refunds that pass through all three agents and need four tool calls (look up, refund, verify, email).

| Fault rate *p* | 1 agent, 1 call | predicted | 3 agents, 4 calls | predicted | pass^10, 3 agents |
|---|---|---|---|---|---|
| 0.2 | 1.00 | 0.99 | 0.98 | 0.97 | 0.85 |
| 0.3 | 0.99 | 0.97 | 0.93 | 0.90 | 0.40 |
| 0.4 | 0.95 | 0.94 | 0.81 | 0.77 | 0.15 |
| 0.5 | 0.90 | 0.88 | **0.61** | 0.59 | **0.00** |
| 0.6 | 0.77 | 0.78 | 0.39 | 0.38 | 0.00 |

"Predicted" assumes each call fails independently: a call succeeds unless all three attempts fail, so it succeeds with probability 1 − *p*³, and a request with *n* calls succeeds with (1 − *p*³)ⁿ. The measured rates follow the prediction closely.

## What it shows

**Reliability compounds per tool call, and agents add tool calls.** At a 50% fault rate, a one-call request succeeds 90% of the time; a refund through three agents, 61%. Nothing about the agents is unreliable; there are simply four chances to fail instead of one.

**pass^k is where it hurts.** Over ten attempts, the three-agent refund never succeeded every time at *p* = 0.5 (pass^10 = 0). Customers experience pass^k, not the average: someone who needs ten refunds over a year experiences at least one failure. The CI report shows the same thing at *k* = 10: pass@k is 1.00 (every scenario *can* succeed) while pass^k is 0.00.

**The model is useful for design.** Before adding an agent to a pipeline, count the tool calls it adds and multiply. A verification step (comms checking the refund before confirming it) costs one call of reliability, and it is worth paying here because it prevents false confirmations (article 03). An extra summarising agent that only rewrites text costs reliability and adds a lost-context risk (article 01) for little benefit.

## What failure looks like here

With faithful agents, a failed run is **safe**: no wrong money moves, and the customer gets an honest "I couldn't complete the refund right now" or a holding reply with an escalation. The reliability gate (`trial_success_rate ≥ 0.55`) measures how often the team gets the job done; the safety gates (zero unsafe writes, duplicates or false claims) must hold even when it does not. Keeping the two apart is deliberate: a team can be unreliable and safe, and that is far better than reliable and occasionally wrong.

## What this experiment leaves out

- **Handoffs here never fail.** Agents run in one process, so passing a handoff cannot time out. In a distributed system, every handoff is a network call and adds its own failure probability to the product.
- **Faults are independent.** Real outages are correlated: when the payment service is slow, it is slow for every retry. Correlated faults make retries less effective than the formula suggests.
- **Retries are free.** Here they cost nothing; in production they cost latency and money, and that is the subject of the next lab in the series.

## Takeaways

- **Every agent you add multiplies the ways a request can fail.**
- **Predict it before you build it:** (per-call success)^(number of calls).
- **Report pass^k, not just averages and pass@k.**
- **Unreliable is acceptable if it is safe; gate safety separately from reliability.**
