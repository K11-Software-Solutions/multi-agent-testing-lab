"""Multi-seed experiments behind the articles' findings.

    python -m experiments.run_experiments            # ~1 minute; writes experiments/results/

E1  Reliability compounds with the number of tool calls a request needs, across agents.
E2  Blame assignment: three methods against injected faults with known ground truth,
    single faults and pairs of faults, with handoff contracts on and off.
E3  Idempotency: duplicate payments under ambiguous timeouts, by retry strategy.
E4  Handoff contracts: what enforcing them changes, per fault, across all scenarios.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from evals.run_team_evals import load_scenarios, run_scenario, score
from teamlab import blame
from teamlab.store import Store
from teamlab.team import Team, parse_spec

OUT = Path(__file__).parent / "results"
SEEDS = 200
RETRIES = 2


def e1_reliability(scenarios: dict) -> dict:
    """Trial success by fault rate, for one-agent and three-agent requests, against a per-call model."""
    groups = {"1 agent, 1 tool call": ["status-01", "policy-01"], "3 agents, 4 tool calls": ["refund-01", "inject-01"]}
    calls = {"1 agent, 1 tool call": 1, "3 agents, 4 tool calls": 4}
    rows = []
    team = Team()
    for p in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6):
        per_call = 1 - p ** (RETRIES + 1)            # a call succeeds unless every attempt fails
        for g, ids in groups.items():
            ok = [score(scenarios[i], run_scenario(scenarios[i], team, p, seed))["success"]
                  for i in ids for seed in range(SEEDS)]
            rate = sum(ok) / len(ok)
            chunks = [ok[j:j + 10] for j in range(0, len(ok), 10)]
            rows.append({"failure_rate": p, "group": g, "trial_success": round(rate, 3),
                         "predicted": round(per_call ** calls[g], 3),
                         "pass_hat_10": round(sum(all(c) for c in chunks) / len(chunks), 3)})
    return {"seeds_per_scenario": SEEDS, "retries": RETRIES, "rows": rows}


FAULTS = [
    ("triage=relay", "inject-01"), ("triage=lossy", "refund-02"),
    ("billing=duplicate", "reliab-05"), ("billing=overclaim", "reliab-03"),
    ("triage=lossy,billing=guessing", "refund-02"), ("billing=overclaim,comms=trusting", "reliab-03"),
    ("billing=guessing", "refund-02"), ("comms=trusting", "reliab-03"),      # latent: should not fail alone
]


def e2_blame(scenarios: dict) -> dict:
    rows = []
    for enforce in (True, False):
        for spec, sid in FAULTS:
            s = scenarios[sid]
            config = parse_spec(spec)
            truth = sorted(r for r, b in config.items() if b != "faithful")
            runs = {"n": 0}

            def passes(cfg, s=s, enforce=enforce):
                runs["n"] += 1
                return score(s, run_scenario(s, Team(cfg, enforce_contracts=enforce)))["success"]

            run = run_scenario(s, Team(config, enforce_contracts=enforce))
            failed = not score(s, run)["success"]
            row = {"contracts": "on" if enforce else "off", "fault": spec, "scenario": sid, "truth": truth, "failed": failed}
            if failed:
                runs["n"] = 0
                row.update(last_actor=sorted(blame.last_actor(run)), first_violation=sorted(blame.first_violation(run)),
                           counterfactual=sorted(blame.counterfactual(config, passes)), replays=runs["n"])
            rows.append(row)
    failed = [r for r in rows if r["failed"]]
    acc = {m: {"exact": round(sum(r[m] == r["truth"] for r in failed) / len(failed), 2),
               "names_a_faulty_agent": round(sum(bool(set(r[m]) & set(r["truth"])) for r in failed) / len(failed), 2)}
           for m in ("last_actor", "first_violation", "counterfactual")}
    return {"rows": rows, "accuracy_on_failed_runs": acc, "failed_runs": len(failed)}


def e3_idempotency(scenarios: dict) -> dict:
    """Ambiguous timeouts on issue_refund, by retry strategy and refund type."""
    rows = []
    for behaviour in ("faithful", "duplicate"):
        for sid, label in (("refund-01", "full refund ($45)"), ("refund-02", "partial refund ($20)")):
            s = scenarios[sid]
            for rate in (0.1, 0.3, 0.5):
                dup = overpaid = told_failed = 0
                for seed in range(SEEDS):
                    rng = random.Random(seed)
                    store = Store(ambiguous_timeouts={"issue_refund": 1} if rng.random() < rate else {})
                    run = Team({"billing": behaviour}).run(s["message"], s["customer"], store, run_id=f"e3-{seed}")
                    paid = sum(r["amount"] for r in store.refunds)
                    expected = s["expect"]["refunds"][0]["amount"]
                    dup += paid > expected + 1e-9
                    overpaid += max(0.0, paid - expected)
                    told_failed += "couldn't" in run.final
                rows.append({"retry": "same key" if behaviour == "faithful" else "new key per attempt", "refund": label,
                             "ambiguous_timeout_rate": rate, "runs": SEEDS, "duplicate_payments": dup,
                             "overpaid": round(overpaid, 2), "customer_told_it_failed": told_failed})
    return {"rows": rows}


def e4_contracts(scenarios: dict) -> dict:
    rows = []
    for spec in ("faithful", "triage=relay", "triage=lossy", "billing=duplicate", "billing=overclaim",
                 "triage=lossy,billing=guessing", "billing=overclaim,comms=trusting"):
        for enforce in (True, False):
            team = Team(parse_spec("" if spec == "faithful" else spec), enforce_contracts=enforce)
            res = [score(s, run_scenario(s, team)) for s in scenarios.values()]
            rows.append({"team": spec, "contracts": "on" if enforce else "off",
                         "task_success": round(sum(r["success"] for r in res) / len(res), 2),
                         "unsafe_writes": sum(len(r["unexpected_writes"]) for r in res),
                         "duplicate_payments": sum(r["duplicates"] for r in res),
                         "false_success_claims": sum(r["false_success_claim"] for r in res),
                         "stopped_or_escalated": sum(r["stopped_reason"] != "done" for r in res)})
    return {"rows": rows}


def table(rows: list[dict], cols: list[str]) -> list[str]:
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(", ".join(r[c]) if isinstance(r.get(c), list) else str(r.get(c, "")) for c in cols) + " |")
    return out


def main() -> int:
    scenarios = {s["id"]: s for s in load_scenarios()}
    results = {"E1": e1_reliability(scenarios), "E2": e2_blame(scenarios),
               "E3": e3_idempotency(scenarios), "E4": e4_contracts(scenarios)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "experiments.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    acc = results["E2"]["accuracy_on_failed_runs"]
    L = ["# Experiment results", "", "Generated by `python -m experiments.run_experiments`. Deterministic: rerunning gives the same numbers.", "",
         f"## E1. Reliability compounds per tool call ({SEEDS} seeds per scenario, {RETRIES} retries per call)", "",
         "`predicted` assumes each call fails independently: (1 - p^3)^calls.", ""]
    L += table(results["E1"]["rows"], ["failure_rate", "group", "trial_success", "predicted", "pass_hat_10"])
    L += ["", "## E2. Blame assignment against known injected faults", ""]
    L += table(results["E2"]["rows"], ["contracts", "fault", "scenario", "failed", "truth", "last_actor", "first_violation", "counterfactual", "replays"])
    L += ["", f"On the {results['E2']['failed_runs']} failed runs: "
          + "; ".join(f"{m.replace('_', ' ')} exact {v['exact']:.2f}, names a faulty agent {v['names_a_faulty_agent']:.2f}" for m, v in acc.items()), "",
          f"## E3. Duplicate payments under ambiguous timeouts ({SEEDS} runs per row)", ""]
    L += table(results["E3"]["rows"], ["retry", "refund", "ambiguous_timeout_rate", "duplicate_payments", "overpaid", "customer_told_it_failed"])
    L += ["", "## E4. What enforcing handoff contracts changes (all scenarios, no random faults)", ""]
    L += table(results["E4"]["rows"], ["team", "contracts", "task_success", "unsafe_writes", "duplicate_payments", "false_success_claims", "stopped_or_escalated"])
    (OUT / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
