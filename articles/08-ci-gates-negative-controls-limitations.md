# 08. CI Gates, Negative Controls and Limitations

> **Series:** Multi-Agent Testing Lab · [Index](../README.md#articles) · Previous: [07](07-duplicates-loops-idempotency.md)

The previous articles found problems. This one makes sure they stay found.

## The gates

[`evals/thresholds.json`](../evals/thresholds.json), checked by `python -m evals.run_team_evals` on every push:

| Kind | Metric | Gate |
|---|---|---|
| Safety (zero tolerance) | unsafe writes, duplicate payments, false success claims, emails attempted to anyone but the customer | 0 |
| Integrity (zero tolerance) | handoff contract violations, unsupported claims | 0 |
| Security | injection resistance | 1.0 |
| Flow | completion rate (no loops, crashes or silence) | 1.0 |
| Quality | task success, state accuracy, routing accuracy | ≥ 0.95 |
| Efficiency | mean hops per request | ≤ 1.5 |
| Reliability | trial success at a 50% fault rate, k = 10 | ≥ 0.55 |
| Diagnosis | counterfactual blame names the injected agent | 1.0 |

The last gate is unusual: it tests the **debugging tool**, not the team. Each CI run injects four known faults, one at a time, and checks that counterfactual replay names the right agent. If a change to the team made blame assignment stop working, you would want to know before the next incident, not during it.

## Negative controls

A gate that never fails proves nothing. CI runs the eval against broken teams that **must** fail:

| Broken team | Contracts | Caught by |
|---|---|---|
| `triage=relay` | on | handoff violation (provenance), injection resistance |
| `triage=lossy` | on | completion rate, task success |
| `billing=duplicate` | on | duplicate payments |
| `billing=overclaim` | on | unsupported claim |
| `triage=lossy,billing=guessing` | **off** | unsafe writes (over-refund), contract violations |
| `billing=overclaim,comms=trusting` | **off** | false success claim |

The last two use **pairs** of faults with contracts off, because the latent faults (article 05) only show in combination, and the contracts would otherwise stop them first. A gate tested only against single faults may have nothing to say about the combinations that cause real incidents.

CI also runs the faithful team with contracts off, which must pass: contract enforcement must not be what makes a healthy team pass.

## Experiments are reproducible, and CI checks that

The four experiments behind these articles are deterministic. CI re-runs them and fails if the results differ from the committed [`experiments/results/summary.md`](../experiments/results/summary.md). Every number quoted in these articles is therefore reproducible from the commit you are reading, and a change that alters a finding cannot go unnoticed.

## Limitations

This lab is built to teach and to be repeatable. What it does not cover:

- **The agents are rule systems, not LLMs.** That makes every failure reproducible, but real agents fail in more varied ways: they paraphrase, hallucinate fields, and behave differently from run to run. The test design carries over; the fault catalogue would grow.
- **One topology.** A fixed pipeline (triage → billing → comms) with one return path. Planner–worker hierarchies, debates and agents that spawn agents add failure modes not covered here: delegation without limits, conflicting instructions, shared memory corruption.
- **Verbatim provenance.** Strong for a lab, too strict for agents that paraphrase. Use offsets held by the orchestrator in production (article 04).
- **Handoffs cannot fail.** Agents share a process. In a distributed system every handoff is a network call with its own failures and duplicates.
- **Small scenario set.** 16 scenarios are a smoke test, not coverage. Grow them from production incidents, as the [AI Production Monitoring Lab](https://github.com/K11-Software-Solutions/ai-production-monitoring-lab) describes.
- **Blame on a short trace.** First violation works because the contract checks see the departures. Faults the contracts do not model (a correct-looking but wrong summary) leave nothing for it to find.

## What to change for real LLM agents

1. Keep the orchestrator, the contract and the trace; replace the rule-based agents with model calls that emit the same `Handoff` structure (tool calling or structured output).
2. Run the scenarios several times per configuration and report pass^k, since runs are no longer deterministic.
3. Keep the faulty behaviours as **mutants**: wrap a real agent to drop a field, append a relayed instruction or overclaim, so the negative controls still prove the gates work.
4. Replay with fixed seeds or temperature 0 for counterfactual blame, and confirm with repeated runs.

## Takeaways

- **Gate safety and integrity at zero; gate quality and reliability against a measured baseline.**
- **Prove each gate with a broken team that must fail, including pairs of faults.**
- **Test your debugging tools in CI, too.**
- **Make findings reproducible, and let CI check them.**
