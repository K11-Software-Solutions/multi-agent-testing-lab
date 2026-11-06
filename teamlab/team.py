"""The orchestrator: routes handoffs between agents and records who did what.

    customer message -> triage -> (handoff) -> billing -> (handoff) -> comms -> reply

At every handoff the orchestrator:
  - validates it against the contract (teamlab.handoff); with enforcement on, a
    handoff that fails provenance or makes an unsupported claim is dropped, and one
    with missing fields goes back to its sender as a request to clarify;
  - stops loops: the same handoff sent repeatedly is not progress;
  - caps the total number of hops.
If the run ends without a reply to the customer, it escalates and sends a holding reply.

Every tool call, retry, block, handoff and reply is recorded as an Event attributed
to the agent that did it. Blame assignment (teamlab.blame) works from that trace.
"""
from __future__ import annotations

import os
from collections import deque
from dataclasses import asdict, dataclass, field

from teamlab.agents import AGENTS, BEHAVIOURS
from teamlab.handoff import Handoff, violations
from teamlab.store import Store
from teamlab.tools import TOOLS, PolicyBlocked, Session, ToolError, check

SECURITY_VIOLATIONS = ("provenance", "unsupported claim", "not the one the customer named",
                       "not the amount the customer asked for", "route")
HOLDING_REPLY = "Thanks for your patience. A colleague is looking into this and will follow up by email."


@dataclass
class Event:
    seq: int
    agent: str
    kind: str          # tool_call | tool_error | blocked | retry | handoff | handoff_rejected | reply | stopped
    name: str = ""
    args: dict = field(default_factory=dict)
    detail: str = ""
    handoff: dict | None = None
    violations: list[str] = field(default_factory=list)


@dataclass
class TeamRun:
    final: str
    stopped_reason: str          # done | handoff_loop | max_hops | agent_error | no_reply
    events: list[Event]
    side_effects: dict
    config: dict

    def of(self, kind: str) -> list[Event]:
        return [e for e in self.events if e.kind == kind]

    @property
    def handoffs(self) -> list[Event]:
        return [e for e in self.events if e.kind in ("handoff", "handoff_rejected")]

    def to_dict(self) -> dict:
        return {"final": self.final, "stopped_reason": self.stopped_reason, "config": self.config,
                "side_effects": self.side_effects, "events": [asdict(e) for e in self.events]}


class Context:
    """What an agent can touch: tools (checked and recorded) and the signed-in customer."""

    def __init__(self, session: Session, events: list[Event], run_id: str):
        self.session, self.events, self.run_id = session, events, run_id
        self.agent = ""

    @property
    def customer(self) -> str:
        return self.session.customer

    def _event(self, **kw) -> None:
        self.events.append(Event(seq=len(self.events), agent=self.agent, **kw))

    def note(self, kind: str, name: str, args: dict) -> None:
        self._event(kind=kind, name=name, args=dict(args))

    def call(self, name: str, args: dict):
        try:
            check(self.agent, self.session, name, args)
        except PolicyBlocked as exc:
            self._event(kind="blocked", name=name, args=dict(args), detail=str(exc))
            raise
        try:
            result = TOOLS[name](self.session, **args)
        except ToolError as exc:
            self._event(kind="tool_error", name=name, args=dict(args), detail=str(exc))
            raise
        self._event(kind="tool_call", name=name, args=dict(args), detail=str(result)[:300])
        return result


def parse_spec(spec: str | None) -> dict[str, str]:
    """'triage=relay,billing=duplicate' -> {'triage': 'relay', 'billing': 'duplicate', 'comms': 'faithful'}"""
    config = {role: "faithful" for role in BEHAVIOURS}
    for part in filter(None, (spec or "").replace(" ", "").split(",")):
        role, _, behaviour = part.partition("=")
        if role not in config:
            raise ValueError(f"unknown agent '{role}'")
        config[role] = behaviour
    return config


class Team:
    def __init__(self, config: dict[str, str] | None = None, enforce_contracts: bool = True,
                 max_hops: int = 8, repeat_limit: int = 2):
        self.config = {**parse_spec(None), **(config or {})}
        self.agents = {role: AGENTS[role](b) for role, b in self.config.items()}
        self.enforce_contracts = enforce_contracts
        self.max_hops = max_hops
        self.repeat_limit = repeat_limit

    @classmethod
    def from_env(cls, **kw) -> "Team":
        return cls(parse_spec(os.environ.get("FAKE_TEAM")), **kw)

    def run(self, message: str, customer: str, store: Store | None = None, run_id: str = "run") -> TeamRun:
        store = store or Store()
        events: list[Event] = []
        ctx = Context(Session(customer, store), events, run_id)
        queue: deque = deque([("triage", message)])
        seen: dict[tuple, int] = {}
        replies: list[str] = []
        hops, stopped = 0, "done"

        while queue:
            receiver, item = queue.popleft()
            if isinstance(item, Handoff):
                hops += 1
                ctx.agent = item.sender
                problems = violations(item, message, store)
                seen[item.key()] = seen.get(item.key(), 0) + 1
                if seen[item.key()] > self.repeat_limit:
                    stopped = "handoff_loop"
                    ctx.agent = "orchestrator"
                    ctx._event(kind="stopped", detail=f"same handoff {item.sender}->{item.receiver} repeated")
                    break
                if hops > self.max_hops:
                    stopped = "max_hops"
                    ctx.agent = "orchestrator"
                    ctx._event(kind="stopped", detail="hop limit reached")
                    break
                if problems and self.enforce_contracts:
                    ctx._event(kind="handoff_rejected", handoff=item.to_dict(), violations=problems)
                    if not any(s in p for p in problems for s in SECURITY_VIOLATIONS):
                        # shape problems go back to the sender, as if the receiver had asked
                        queue.append((item.sender, Handoff(item.receiver, item.sender, "clarify", order_id=item.order_id,
                                                           reason="; ".join(problems),
                                                           facts={"original_reason": item.reason,
                                                                  "original_quote": item.customer_quote})))
                    continue
                ctx._event(kind="handoff", handoff=item.to_dict(), violations=problems)
            ctx.agent = receiver
            try:
                out = self.agents[receiver].handle(ctx, item)
            except Exception as exc:                  # one agent's bug must not take down the desk
                ctx._event(kind="stopped", detail=f"agent error: {exc!r}")
                stopped = "agent_error"
                break
            if out.reply:
                ctx._event(kind="reply", detail=out.reply)
                replies.append(out.reply)
            queue.extend((h.receiver, h) for h in out.handoffs)

        if not replies:
            ctx.agent = "orchestrator"
            try:
                ctx.session.store.escalations.append({"ticket": "T-auto", "summary": f"no reply ({stopped})"})
            finally:
                ctx._event(kind="reply", detail=HOLDING_REPLY)
            replies.append(HOLDING_REPLY)
            stopped = stopped if stopped != "done" else "no_reply"
        return TeamRun(replies[-1], stopped, events, store.side_effects(), dict(self.config))
