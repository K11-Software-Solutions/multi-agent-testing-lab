"""Blame assignment and the eval runner, including the negative controls CI relies on."""
import json

import pytest

from evals import run_team_evals as ev
from teamlab import blame
from teamlab.team import Team, parse_spec

SCENARIOS = {s["id"]: s for s in ev.load_scenarios()}


def failing_run(spec, sid, enforce=True):
    s = SCENARIOS[sid]
    cfg = parse_spec(spec)
    run = ev.run_scenario(s, Team(cfg, enforce_contracts=enforce))

    def passes(c):
        return ev.score(s, ev.run_scenario(s, Team(c, enforce_contracts=enforce)))["success"]

    assert not passes(cfg)
    return run, cfg, passes


@pytest.mark.parametrize("spec, sid, culprit", [
    ("triage=relay", "inject-01", "triage"), ("triage=lossy", "refund-02", "triage"),
    ("billing=duplicate", "reliab-05", "billing"), ("billing=overclaim", "reliab-03", "billing"),
])
def test_first_violation_and_counterfactual_find_single_faults(spec, sid, culprit):
    run, cfg, passes = failing_run(spec, sid)
    assert blame.first_violation(run) == {culprit}
    assert blame.counterfactual(cfg, passes) == {culprit}


def test_last_actor_blames_the_messenger():
    run, *_ = failing_run("triage=relay", "inject-01")
    assert blame.last_actor(run) == {"comms"}


def test_counterfactual_names_the_smallest_fix_not_every_culprit():
    _, cfg, passes = failing_run("triage=lossy,billing=guessing", "refund-02", enforce=False)
    assert blame.counterfactual(cfg, passes) == {"triage"}       # billing's latent fault stays hidden


def test_faithful_team_passes_every_scenario():
    team = Team()
    failures = [s["id"] for s in SCENARIOS.values() if not ev.score(s, ev.run_scenario(s, team))["success"]]
    assert failures == []


def test_score_detects_duplicates_and_false_claims():
    s = SCENARIOS["reliab-03"]
    run = ev.run_scenario(s, Team(parse_spec("billing=overclaim,comms=trusting"), enforce_contracts=False))
    assert ev.score(s, run)["false_success_claim"]
    s = SCENARIOS["reliab-05"]
    run = ev.run_scenario(s, Team(parse_spec("billing=duplicate")))
    assert ev.score(s, run)["duplicates"] == 1


def test_gate():
    assert ev.gate({"a": 0.5, "b": 1}, {"a": {"min": 0.9}, "b": {"max": 0}, "_c": {}}) == ["a = 0.50 < 0.9", "b = 1.00 > 0"]


@pytest.mark.parametrize("spec, args, passes", [
    ("", [], True),
    ("", ["--no-contracts"], True),
    ("triage=relay", [], False), ("triage=lossy", [], False),
    ("billing=duplicate", [], False), ("billing=overclaim", [], False),
    ("triage=lossy,billing=guessing", ["--no-contracts"], False),
    ("billing=overclaim,comms=trusting", ["--no-contracts"], False),
])
def test_negative_controls(monkeypatch, tmp_path, spec, args, passes):
    monkeypatch.setenv("FAKE_TEAM", spec)
    code = ev.main(args + ["--k", "5", "--out", str(tmp_path)])
    assert (code == 0) is passes
    report = json.loads((tmp_path / "team_report.json").read_text(encoding="utf-8"))
    assert bool(report["gate_failures"]) is not passes
