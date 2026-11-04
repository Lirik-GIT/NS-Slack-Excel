import pytest

from app.staffing import StaffingError, plan, validate_policy


def heads(dev):
    pct = {
        "Requirement understanding & Sign-off": .15, "High Level Design & Sign-off": .1,
        "Development Effort (Coding + Configuration + Unit Testing)": 1, "Demo (3 max)": .1,
        "Manual QA": .25, "Project Management": .25, "Deployments - Dev/QA/Sandbox": .15,
        "Handover & Final UAT": .15, "Contingency": .3, "Warranty Support": .2,
        "User Experience Dev": .03, "User Training": .03, "Documentation": .04,
    }
    return {k: v * dev for k, v in pct.items()}


def test_policy_covers_every_head_once(policy, catalog):
    validate_policy(policy["staffing"], catalog)


def test_policy_rejects_unmapped_head(policy, catalog):
    bad = {**policy["staffing"], "role_heads": {k: list(v) for k, v in policy["staffing"]["role_heads"].items()}}
    bad["role_heads"]["QA"] = []
    with pytest.raises(StaffingError):
        validate_policy(bad, catalog)


def test_staffed_weeks_equal_calculated_effort(policy):
    h = heads(62.5)
    p = plan(h, policy["staffing"], 12)
    for role, weekly in p.grid.items():
        assert round(sum(weekly), 4) == round(p.person_weeks[role], 4)
        assert len(weekly) == 12
    assert round(sum(p.person_weeks.values()), 4) == round(sum(h.values()) / 5, 4)
    assert p.calendar_weeks == 11 and not p.compressed


def test_large_effort_is_compressed_and_flagged(policy):
    p = plan(heads(200), policy["staffing"], 12)
    assert p.compressed and p.calendar_weeks == 12


def test_small_effort_respects_minimum_weeks(policy):
    assert plan(heads(3), policy["staffing"], 12).calendar_weeks == policy["staffing"]["min_weeks"]
