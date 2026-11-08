# Multi-Agent Testing Lab

[![CI quality gates](https://github.com/K11-Software-Solutions/multi-agent-testing-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/K11-Software-Solutions/multi-agent-testing-lab/actions/workflows/ci.yml)
[![Code: MIT](https://img.shields.io/badge/Code-MIT-blue.svg)](LICENSE) [![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.24025290.svg)](https://doi.org/10.5281/zenodo.24025290) [![Articles: CC BY 4.0](https://img.shields.io/badge/Articles-CC%20BY%204.0-lightgrey.svg)](articles/LICENSE.md)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![No dependencies](https://img.shields.io/badge/runtime%20deps-none-success)
![No API key needed](https://img.shields.io/badge/API%20key-not%20required-success)

**Test a team of AI agents that pass work to each other: handoff contracts, relayed prompt injection, blame assignment, duplicate payments, loops and reliability that compounds across agents. Then gate it in CI.**

The fifth of eleven labs in a series for quality engineers moving into AI:

| Lab | Question it answers |
|---|---|
| [Becoming an AI Quality Engineer](https://github.com/K11-Software-Solutions/becoming-ai-quality-engineer) | Is the RAG **application** good enough to ship? |
| [LLM Model Testing Lab](https://github.com/K11-Software-Solutions/llm-model-testing-lab) | Is the **model** good enough? |
| [Agent Testing Lab](https://github.com/K11-Software-Solutions/agent-testing-lab) | Is the **agent** safe to let act? |
| [AI Production Monitoring Lab](https://github.com/K11-Software-Solutions/ai-production-monitoring-lab) | Is it **still** good, for the users we actually have, right now? |
| **Multi-Agent Testing Lab** (this repo) | When **agents work together**, does the team do the right thing, and who broke it when it doesn't? |
| [AI Performance and Cost Lab](https://github.com/K11-Software-Solutions/ai-performance-cost-lab) | Is it **fast enough under load**, and what does each **good answer** cost? |
| [AI Evaluation Science Lab](https://github.com/K11-Software-Solutions/ai-evaluation-science-lab) | Can we **trust the evaluator**, and how sure are we? |
| [AI Data Quality Lab](https://github.com/K11-Software-Solutions/ai-data-quality-lab) | Is the **data** behind it all fit to learn from, test with and retrieve? |
| [AI Security Testing Lab](https://github.com/K11-Software-Solutions/ai-security-testing-lab) | Can an **attacker** make it misbehave, and how sure are we that they can't? |
| [MCP Testing Lab](https://github.com/K11-Software-Solutions/mcp-testing-lab) | Can we trust the **tools our agents plug into**? |
| [AI Assurance Case Lab](https://github.com/K11-Software-Solutions/ai-assurance-case-lab) (capstone) | Can we **sign it off**, on what evidence, and what risk are we accepting? |

## The system under test

A support desk for a fictional store, run by three agents with separate jobs and least-privilege permissions:

| Agent | Job | May use |
|---|---|---|
| **triage** | reads the customer's message, answers simple questions, hands off refunds | look up orders, read policy, escalate |
| **billing** | moves money | look up orders, **issue refunds**, escalate |
| **comms** | tells the customer, after checking | look up refunds, **send email** |

Work moves between them as structured **handoffs**. An orchestrator validates every handoff against a contract (shape, **provenance**: did the customer ask for this?, and **claims**: is what it says true?), stops loops, and records a trace attributed to the agent that did each thing. One order's notes contain a planted instruction addressed to "the billing team".

The agents are deterministic stand-ins for LLM agents, each with faulty behaviours you can switch on:

| Agent | Faulty behaviour | Failure mode |
|---|---|---|
| triage | `lossy` | drops the amount when it summarises (lost context; loops) |
| triage | `relay` | passes on instructions it read in an order note (confused deputy) |
| billing | `guessing` | fills a missing amount with the order total (latent) |
| billing | `duplicate` | retries a refund with a new idempotency key (double payment) |
| billing | `overclaim` | says a refund was issued when it was not (unsupported claim) |
| comms | `trusting` | repeats upstream claims without checking (latent) |

## Articles

| # | Article |
|---|---|
| 01 | [What Changes When Agents Work Together](articles/01-what-changes-when-agents-work-together.md) |
| 02 | [Anatomy of a Testable Multi-Agent System](articles/02-anatomy-of-a-testable-multi-agent-system.md) |
| 03 | [Testing Handoffs: Contracts, Provenance and Claims](articles/03-testing-handoffs-contracts-provenance-claims.md) |
| 04 | [Confused Deputies: Relayed Injection Across Agents](articles/04-confused-deputies-relayed-injection.md) |
| 05 | [Blame Assignment: Which Agent Broke It?](articles/05-blame-assignment.md) |
| 06 | [Reliability Compounds Across Agents](articles/06-reliability-compounds-across-agents.md) |
| 07 | [Duplicates, Loops and Idempotency](articles/07-duplicates-loops-idempotency.md) |
| 08 | [CI Gates, Negative Controls and Limitations](articles/08-ci-gates-negative-controls-limitations.md) |

## Quick start

Requirements: Python 3.11+. No runtime dependencies and no API key.

```bash
git clone https://github.com/K11-Software-Solutions/multi-agent-testing-lab.git
cd multi-agent-testing-lab
python -m venv .venv
source .venv/bin/activate              # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

```bash
pytest                                                          # 62 tests, about 1 s
python -m evals.run_team_evals                                  # scenarios, reliability, blame self-test, gates
FAKE_TEAM=triage=relay python -m evals.run_team_evals           # a faulty agent: the gate must fail
FAKE_TEAM="triage=lossy,billing=guessing" python -m evals.run_team_evals --no-contracts   # a pair of faults
python -m experiments.run_experiments                           # the four experiments behind the articles
```

## Repository layout

```text
teamlab/
  store.py        the world: orders, refunds, emails; fault injection incl. ambiguous timeouts; planted injection
  tools.py        tools, per-agent permissions and business rules, idempotent refunds
  handoff.py      the Handoff message and its contract: shape, provenance, claims
  agents.py       triage, billing, comms: faithful and faulty behaviours
  team.py         the orchestrator: routing, contract enforcement, loop and hop limits, attributed trace
  blame.py        last actor, first violation, counterfactual replay
evals/
  scenarios.jsonl     16 scenarios: refunds, authorisation, clarification, injection, reliability
  run_team_evals.py   scoring, pass@k / pass^k, blame self-test, gates
  thresholds.json     zero-tolerance safety and integrity gates, quality and reliability floors
experiments/      E1 reliability, E2 blame, E3 idempotency, E4 contracts; results committed
tests/            62 deterministic tests
articles/         the eight articles
.github/workflows/ci.yml   tests -> evals + 8 negative controls -> experiments reproducibility check
```

## What the lab finds

- **Every agent followed its rules, and the team still paid out a refund nobody asked for.** Triage relayed an instruction from an order note; billing, which never saw the note, passed every policy check. Only a provenance check on the handoff stopped it.
- **The agent that delivers bad news is rarely the one that caused it.** Blaming the last agent to speak was right in 1 of 12 failed runs; first-violation and counterfactual replay always named a faulty agent.
- **Counterfactual replay finds the smallest fix, not every culprit.** With two faults, it named only the upstream one; the latent fault passed every scenario on its own.
- **Reliability compounds per tool call.** At a 50% fault rate with retries, a one-agent request succeeds 90% of the time and a three-agent refund 61%, matching (1 − p³)ⁿ; over ten tries, the refund never succeeded every time.
- **A new idempotency key per retry paid twice on partial refunds** (106 duplicates and $2,120 overpaid in 200 runs) and **told customers a full refund failed** when it had gone through.
- **The idempotency defence had its own bug:** the balance rule ran before the key check, so a correct retry was refused.
- **The contract validator crashed on the input it existed to reject** (an amount sent as text).
- **A trace that logs a timed-out write as an error hides duplicates** from blame assignment until "outcome unknown" is treated as "maybe done".

## CI quality gates

Every push runs three jobs: **tests**; **team evals** with zero-tolerance safety and integrity gates, a reliability floor, a blame self-test, and eight negative controls (six broken teams that must fail, including pairs of faults with contracts off, and the faithful team that must pass with contracts off); and an **experiments** job that re-runs all four experiments and fails if the results differ from the committed ones.

## Limitations

Rule-based agents instead of LLMs, one fixed topology, verbatim provenance, in-process handoffs that cannot fail, and 16 scenarios. [Article 08](articles/08-ci-gates-negative-controls-limitations.md#limitations) covers each and what to change for real LLM agents.

## About

Created by **Kavita Jadhav**, founder of [K11 Software Solutions](https://github.com/K11-Software-Solutions), Senior SDET and AI Quality Engineer working on LLM evaluation, red teaming and agentic QA systems.

**Licences:** code under the [MIT License](LICENSE); the written material in [`articles/`](articles/LICENSE.md) under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) (share and adapt with credit).

The store, its customers and its orders are fictional.

## Citing

Use GitHub's **Cite this repository** button (from [`CITATION.cff`](CITATION.cff)). Each tagged release is archived on Zenodo with its own DOI. Cite the version you used:

> Jadhav, K. (2026). *Multi-Agent Testing Lab* (Version v1.0.0) [Computer software]. Zenodo. https://doi.org/10.5281/zenodo.24025291

To refer to the project as a whole (always resolves to the latest version), use the concept DOI: https://doi.org/10.5281/zenodo.24025290
