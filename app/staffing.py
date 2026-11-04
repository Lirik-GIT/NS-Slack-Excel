import math
from dataclasses import dataclass


class StaffingError(Exception):
    pass


@dataclass(frozen=True)
class StaffingPlan:
    grid: dict
    person_weeks: dict
    calendar_weeks: int
    compressed: bool


def validate_policy(staffing_policy, catalog):
    role_labels = {r.label for r in catalog.roles}
    head_labels = {h.label for h in catalog.heads}
    mapped = []
    for role, heads in staffing_policy["role_heads"].items():
        if role not in role_labels:
            raise StaffingError(f"Policy role '{role}' is not a priced role in Step 3")
        for head in heads:
            if head not in head_labels:
                raise StaffingError(f"Policy head '{head}' is not a Step 2 head")
            mapped.append(head)
    missing = head_labels - set(mapped)
    if missing:
        raise StaffingError(f"Step 2 heads not assigned to a role: {sorted(missing)}")
    dupes = {h for h in mapped if mapped.count(h) > 1}
    if dupes:
        raise StaffingError(f"Step 2 heads assigned to more than one role: {sorted(dupes)}")
    if staffing_policy["duration_role"] not in staffing_policy["role_heads"]:
        raise StaffingError("duration_role must be one of the policy roles")


def _spread(total, weeks):
    if total <= 0:
        return [0.0] * weeks
    per = math.floor(total / weeks * 100) / 100
    values = [per] * weeks
    values[-1] = round(total - per * (weeks - 1), 4)
    return values


def plan(head_days, staffing_policy, max_weeks):
    person_weeks = {}
    for role, heads in staffing_policy["role_heads"].items():
        person_weeks[role] = round(sum(head_days[h] for h in heads) / 5, 4)

    lead = person_weeks[staffing_policy["duration_role"]]
    weeks = math.ceil(round(lead / staffing_policy["max_fte_on_duration_role"], 6))
    weeks = max(weeks, staffing_policy["min_weeks"], 1)
    compressed = weeks > max_weeks
    weeks = min(weeks, max_weeks)

    grid = {role: _spread(pw, weeks) + [0.0] * (max_weeks - weeks) for role, pw in person_weeks.items()}
    return StaffingPlan(grid=grid, person_weeks=person_weeks, calendar_weeks=weeks, compressed=compressed)
