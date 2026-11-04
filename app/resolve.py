from collections import Counter
from dataclasses import dataclass, field

from .questions import (ADDON_OPTIONS, AREA_OPTIONS, BY_ID, DEFAULT_APPROVAL, DEFAULT_APPROVAL_ASSUMPTION,
                        DEFAULT_ORDER, LIST_APPROVALS, LIST_CUSTOM, LIST_MIGRATION, NEW, default_for, rank)

FORM = "Form"
REQUIREMENT = "Requirement"
DEFAULT = "Default"
REVIEWER = "Reviewer"


@dataclass
class Scope:
    answers: dict
    approvals: list
    custom_items: list
    migration_objects: list
    sentences: list
    not_covered: list
    conflicts: list
    questions: list
    assumptions: list
    form_flags: list
    flags: list
    issues: list
    scope_summary: str
    reads: list = field(default_factory=list)

    def value(self, qid):
        return self.answers[qid]["value"]

    def to_dict(self):
        return {
            "answers": self.answers,
            "approvals": self.approvals,
            "custom_items": self.custom_items,
            "migration_objects": self.migration_objects,
            "sentences": self.sentences,
            "not_covered": self.not_covered,
            "conflicts": self.conflicts,
            "questions": self.questions,
            "assumptions": self.assumptions,
            "form_flags": self.form_flags,
            "flags": self.flags,
            "issues": self.issues,
            "scope_summary": self.scope_summary,
        }


def _fmt(value):
    return f"{value:,}" if isinstance(value, int) else str(value)


def _approval_weight(items):
    return sum(40 if a["levels"] >= 2 else 24 for a in items)


def _item_weight(items, hours):
    return sum(hours.get((i["type"], i["size"]), 0) for i in items)


def _signature(items, keys):
    return Counter(tuple(i[k] for k in keys) for i in items)


def _pick_list(label, a, b, keys, weight, conflicts):
    if _signature(a, keys) == _signature(b, keys):
        return a
    conflicts.append(f"{label}: the two reads differ ({len(a)} vs {len(b)} items); the larger set is used")
    return a if weight(a) >= weight(b) else b


def _answered(qid, answers, lists):
    if qid in lists:
        return bool(lists[qid])
    entry = answers.get(qid)
    return bool(entry) and entry["source"] in (FORM, REQUIREMENT)


def resolve(form, read_a, read_b, sentences, item_hours, bank, keyword_hits, standard_exclusions):
    answers, conflicts, defaults_used = {}, [], []
    for qid in DEFAULT_ORDER:
        q = BY_ID[qid]
        if qid in form:
            answers[qid] = {"value": form[qid], "source": FORM, "evidence": "Selected in the form"}
            continue
        va, vb = read_a.answers.get(qid), read_b.answers.get(qid)
        current = {k: v["value"] for k, v in answers.items()}
        default, ask, assumption = default_for(qid, current)
        if va is None and vb is None:
            answers[qid] = {"value": default, "source": DEFAULT, "evidence": ""}
            defaults_used.append((qid, ask, assumption))
            continue
        if va is not None and vb is not None and va[0] == vb[0]:
            answers[qid] = {"value": va[0], "source": REQUIREMENT, "evidence": va[2], "sentences": va[1]}
            continue
        candidates = [(va[0] if va else default, va), (vb[0] if vb else default, vb)]
        chosen, ev = max(candidates, key=lambda c: rank(qid, c[0]))
        shown = " vs ".join(_fmt(c[0]) if c[1] else f"not stated ({_fmt(default)})" for c in candidates)
        conflicts.append(f"{qid} {q.text} Reads differ: {shown}. Using {_fmt(chosen)}.")
        if ev:
            answers[qid] = {"value": chosen, "source": REQUIREMENT, "evidence": ev[2], "sentences": ev[1], "conflict": True}
        else:
            answers[qid] = {"value": chosen, "source": DEFAULT, "evidence": "", "conflict": True}
            defaults_used.append((qid, ask, assumption))

    form_flags = []
    for qid, label in AREA_OPTIONS + ADDON_OPTIONS:
        if qid not in form and answers[qid]["value"] == "Yes" and answers[qid]["source"] == REQUIREMENT:
            form_flags.append(f"{label} is in the requirement but was not selected in the form; included")

    approvals = _pick_list("Approval processes", read_a.approvals, read_b.approvals, ("levels",), _approval_weight, conflicts)
    custom = _pick_list("Custom items", read_a.custom_items, read_b.custom_items, ("type", "size"),
                        lambda x: _item_weight(x, item_hours), conflicts)
    migration = _pick_list("Other migration objects", read_a.migration_objects, read_b.migration_objects, ("type", "size"),
                           lambda x: _item_weight(x, item_hours), conflicts)
    lists = {LIST_APPROVALS: approvals, LIST_CUSTOM: custom, LIST_MIGRATION: migration}

    assumptions = []
    if not approvals and answers["Q01"]["value"] == NEW and answers["Q07"]["value"] == "Yes":
        approvals = [{**DEFAULT_APPROVAL, "source": DEFAULT, "quote": ""}]
        assumptions.append(DEFAULT_APPROVAL_ASSUMPTION)

    bank_questions, flags = [], []
    for bq in bank.questions:
        trigger = None
        if bq.qid in keyword_hits:
            hit = keyword_hits[bq.qid]
            trigger = {"how": "keyword", "detail": hit["keyword"], "sentence": hit["sentence"]}
        elif bq.qid in read_a.bank_matches and bq.qid in read_b.bank_matches:
            m = read_a.bank_matches[bq.qid]
            trigger = {"how": "matched", "detail": m["quote"], "sentence": m["sentences"][0]}
        if trigger is None:
            continue
        if bq.skip and all(_answered(s, answers, lists) for s in bq.skip):
            continue
        bank_questions.append({"type": "Bank", "id": bq.qid, "area": bq.area, "question": bq.question,
                               "trigger": trigger, "assumes": bq.assumes})
        if bq.flags_reviewer:
            flags.append(f"{bq.qid} {bq.area}: {bq.changes}")

    covered_by_bank = {s for bq in bank_questions for s in bank.get(bq["id"]).skip}
    gap_questions = []
    for qid, ask, assumption in defaults_used:
        if assumption:
            assumptions.append(assumption)
        if ask and qid not in covered_by_bank:
            gap_questions.append({"type": "Gap", "id": qid, "area": BY_ID[qid].group, "question": BY_ID[qid].text,
                                  "trigger": None, "assumes": assumption or ""})

    for bq in bank_questions:
        if bq["assumes"]:
            assumptions.append(bq["assumes"])
    assumptions.extend(standard_exclusions)

    not_covered = sorted(read_a.not_covered | read_b.not_covered)
    return Scope(
        answers=answers,
        approvals=approvals,
        custom_items=custom,
        migration_objects=migration,
        sentences=list(sentences),
        not_covered=[{"n": n, "text": sentences[n - 1]} for n in not_covered],
        conflicts=conflicts,
        questions=gap_questions + bank_questions,
        assumptions=list(dict.fromkeys(assumptions)),
        form_flags=form_flags,
        flags=flags,
        issues=list(dict.fromkeys(read_a.issues + read_b.issues)),
        scope_summary=read_a.scope_summary,
        reads=[read_a.raw, read_b.raw],
    )
