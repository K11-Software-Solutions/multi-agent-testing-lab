"""Blame assignment: which agent caused a failed run?

Three methods, from cheapest to most expensive:

1. last_actor        the agent that last spoke to the customer. What a support ticket
                     usually blames, and usually wrong: comms delivers bad news it did
                     not cause.
2. first_violation   the first observable departure in the trace: a handoff that breaks
                     its contract, an attempt the policy blocked, or a repeated write.
                     Free, because the trace is already there, but it only sees what
                     the contract checks can see.
3. counterfactual    replay the run, swapping one agent at a time for a known-good
                     reference version, and see which swap makes the run pass. If no
                     single swap does, try pairs. It costs extra runs and needs a
                     reference version, and it answers "which change fixes it", which
                     is not always "who was at fault" (article 05).
"""
from __future__ import annotations

from itertools import combinations
from typing import Callable

from teamlab.team import TeamRun

ROLES = ("triage", "billing", "comms")


def last_actor(run: TeamRun) -> set[str]:
    for e in reversed(run.events):
        if e.kind == "reply" and e.agent != "orchestrator":
            return {e.agent}
    for e in reversed(run.events):
        if e.agent in ROLES:
            return {e.agent}
    return set()


def first_violation(run: TeamRun) -> set[str]:
    # A write whose outcome is unknown (timed out after acting) may have happened: treat it as done.
    # Reading "tool_error" as "nothing happened" is how duplicate payments hide in a trace.
    attempted: dict[tuple, str] = {}
    for e in run.events:
        if e.kind in ("handoff", "handoff_rejected") and e.violations:
            return {e.handoff["sender"]}
        if e.kind == "blocked" and e.name == "send_email":
            return {e.agent}
        if e.name == "issue_refund" and (e.kind == "tool_call" or "outcome unknown" in e.detail):
            payment = (e.args["order_id"], e.args["amount"])
            key = e.args.get("idempotency_key")
            if payment in attempted and attempted[payment] != key:
                return {e.agent}                 # the same payment again, under a different key
            attempted.setdefault(payment, key)
    return set()


def counterfactual(config: dict[str, str], passes: Callable[[dict[str, str]], bool]) -> set[str]:
    """Smallest set of agents whose replacement by the faithful version makes `passes` true.

    `passes(config)` runs the scenario with that team configuration and returns True if
    the outcome is correct. Agents that are already faithful are swapped too: in a real
    system you do not know which one is faulty, only which versions are known-good.
    """
    if passes(config):
        return set()
    for size in (1, 2, 3):
        fixes = [set(group) for group in combinations(ROLES, size)
                 if passes({**config, **{r: "faithful" for r in group}})]
        if fixes:
            return set().union(*fixes) if size == 1 else min(fixes, key=sorted)
    return set(ROLES)
