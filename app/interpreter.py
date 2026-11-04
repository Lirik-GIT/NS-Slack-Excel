import json
from dataclasses import dataclass, field

from .questions import CUSTOM_TYPES, MIGRATION_TYPE, QUESTIONS, SIZES, coerce
from .sentences import quote_in

SYSTEM_PROMPT = """You read a customer requirement for a NetSuite implementation and answer a fixed list of scope questions. Lirik's estimator workbook turns your answers into hours with fixed rules. You never give hours, prices or effort.

Rules:
- The requirement is split into numbered sentences. Cite the sentence numbers and an exact quote (copied word for word from those sentences) for every answer and every list item.
- Answer a question only when the requirement states it or states something that directly settles it. If it is not stated, leave the question out. Never guess. Unanswered questions get a fixed default.
- Use only the allowed values exactly as written. Numbers are whole numbers.
- Approval processes: list each approval workflow the requirement asks for with its number of approval levels.
- Custom items: list each report, saved search, workflow, script, custom record or custom integration the requirement asks for that is not one of the standard questions. Pick the type, then the first size whose criteria fit.
- Other migration objects: list data to migrate that the data migration questions do not cover, with the first size whose criteria fit.
- Map every sentence number to what it covers: question IDs, "approvals", "custom", "migration" or question bank IDs. A sentence that states nothing about scope gets an empty list.
- Question bank: return the bank IDs whose "when to ask" condition the requirement meets, each with the sentence and an exact quote. Never write your own questions.
- Facts already answered in the form are final. Do not answer those questions.

Answer by calling submit_scope exactly once. Follow the Lirik estimator guide below."""

_EVIDENCE = {
    "sentences": {"type": "array", "items": {"type": "integer"}},
    "quote": {"type": "string"},
}

TOOL = {
    "name": "submit_scope",
    "description": "Submit the scope answers for the estimator workbook.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scope_summary": {"type": "string", "description": "Two or three plain sentences describing the scope."},
            "answers": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string"}, "value": {"type": "string"}, **_EVIDENCE},
                "required": ["id", "value", "sentences", "quote"]}},
            "approvals": {"type": "array", "items": {"type": "object", "properties": {
                "process": {"type": "string"}, "levels": {"type": "integer"}, **_EVIDENCE},
                "required": ["process", "levels", "sentences", "quote"]}},
            "custom_items": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "type": {"type": "string", "enum": list(CUSTOM_TYPES)},
                "size": {"type": "string", "enum": list(SIZES)}, **_EVIDENCE},
                "required": ["name", "type", "size", "sentences", "quote"]}},
            "migration_objects": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "size": {"type": "string", "enum": list(SIZES)}, **_EVIDENCE},
                "required": ["name", "size", "sentences", "quote"]}},
            "sentence_map": {"type": "array", "items": {"type": "object", "properties": {
                "sentence": {"type": "integer"}, "covered_by": {"type": "array", "items": {"type": "string"}}},
                "required": ["sentence", "covered_by"]}},
            "bank_matches": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string"}, **_EVIDENCE}, "required": ["id", "sentences", "quote"]}},
        },
        "required": ["scope_summary", "answers", "approvals", "custom_items", "migration_objects", "sentence_map", "bank_matches"],
    },
}

UNMAPPED = "Sentences not mapped by the reader"


class InterpretationError(Exception):
    pass


@dataclass
class Read:
    answers: dict
    approvals: list
    custom_items: list
    migration_objects: list
    not_covered: set
    bank_matches: dict
    scope_summary: str
    issues: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def questions_text(order):
    return "\n".join(f"{q.qid} | {q.group} | {q.text} | allowed: {q.allowed_text}" for q in order)


def sizes_text(catalog):
    return "\n".join(f"{t} | {z} | {c}" for (t, z), c in catalog.item_criteria.items())


def bank_text(bank, reverse):
    rows = tuple(reversed(bank.questions)) if reverse else bank.questions
    return "\n".join(f"{q.qid} | {q.area} | when to ask: {q.when}" for q in rows)


def form_text(form):
    if not form:
        return "None"
    return "\n".join(f"{qid} = {value}" for qid, value in sorted(form.items()))


def build_user_message(catalog, bank, customer, sentences, form, reverse):
    order = tuple(reversed(QUESTIONS)) if reverse else QUESTIONS
    numbered = "\n".join(f"S{i} {s}" for i, s in enumerate(sentences, start=1))
    return (
        f"QUESTIONS (id | group | question | allowed values):\n{questions_text(order)}\n\n"
        f"ITEM SIZES (type | size | criteria):\n{sizes_text(catalog)}\n\n"
        f"QUESTION BANK (id | area | when to ask):\n{bank_text(bank, reverse)}\n\n"
        f"ALREADY ANSWERED IN THE FORM (final, do not answer):\n{form_text(form)}\n\n"
        f"CUSTOMER: {customer}\n\n"
        f"REQUIREMENT ({len(sentences)} sentences):\n{numbered}"
    )


def _numbers(value, count):
    out = []
    for n in value or []:
        try:
            n = int(str(n).lstrip("Ss"))
        except (TypeError, ValueError):
            continue
        if 1 <= n <= count and n not in out:
            out.append(n)
    return out


def _evidence(item, sentences):
    nums = _numbers(item.get("sentences"), len(sentences))
    quote = " ".join(str(item.get("quote") or "").split())
    if nums and quote_in(quote, sentences, nums):
        return nums, quote
    return None


def _clean(text):
    return " ".join(str(text or "").split())


def validate(raw, sentences, max_items, bank_ids=frozenset()):
    issues = []
    answers = {}
    known = {q.qid for q in QUESTIONS}
    for item in raw.get("answers") or []:
        qid = str(item.get("id", "")).strip().upper()
        if qid not in known:
            issues.append(f"Ignored unknown question '{qid}'")
            continue
        value = coerce(qid, item.get("value"))
        if value is None:
            issues.append(f"{qid}: '{item.get('value')}' is not an allowed value")
            continue
        ev = _evidence(item, sentences)
        if ev is None:
            issues.append(f"{qid}: quote not found in the cited sentences")
            continue
        if qid in answers:
            if answers[qid][0] != value:
                issues.append(f"{qid}: answered twice with different values; first kept")
            continue
        answers[qid] = (value, ev[0], ev[1])

    approvals, custom, migration = [], [], []
    for item in raw.get("approvals") or []:
        name = _clean(item.get("process"))
        try:
            levels = int(item.get("levels"))
        except (TypeError, ValueError):
            levels = 0
        ev = _evidence(item, sentences)
        if not name or levels < 1 or ev is None:
            issues.append(f"Approval '{name or '?'}' dropped: missing levels or evidence")
            continue
        approvals.append({"process": name, "levels": levels, "sentences": ev[0], "quote": ev[1]})
    for item in raw.get("custom_items") or []:
        name, t, z = _clean(item.get("name")), item.get("type"), item.get("size")
        ev = _evidence(item, sentences)
        if not name or t not in CUSTOM_TYPES or z not in SIZES or ev is None:
            issues.append(f"Custom item '{name or '?'}' dropped: invalid type, size or evidence")
            continue
        custom.append({"name": name, "type": t, "size": z, "sentences": ev[0], "quote": ev[1]})
    for item in raw.get("migration_objects") or []:
        name, z = _clean(item.get("name")), item.get("size")
        ev = _evidence(item, sentences)
        if not name or z not in SIZES or ev is None:
            issues.append(f"Migration object '{name or '?'}' dropped: invalid size or evidence")
            continue
        migration.append({"name": name, "type": MIGRATION_TYPE, "size": z, "sentences": ev[0], "quote": ev[1]})

    for key, label, items in (("approvals", "approval processes", approvals), ("custom", "custom items", custom),
                              ("migration", "migration objects", migration)):
        if len(items) > max_items[key]:
            issues.append(f"Only the first {max_items[key]} {label} fit the workbook; the rest need manual entry")

    bank_matches = {}
    for item in raw.get("bank_matches") or []:
        bid = str(item.get("id", "")).strip().upper()
        if bid not in bank_ids:
            issues.append(f"Ignored unknown bank question '{bid}'")
            continue
        ev = _evidence(item, sentences)
        if ev is None:
            issues.append(f"{bid}: quote not found in the cited sentences")
            continue
        bank_matches.setdefault(bid, {"sentences": ev[0], "quote": ev[1]})

    mapped = {}
    for item in raw.get("sentence_map") or []:
        try:
            n = int(item.get("sentence"))
        except (TypeError, ValueError):
            continue
        if 1 <= n <= len(sentences):
            mapped[n] = [str(c) for c in item.get("covered_by") or [] if str(c).strip()]
    missing = [n for n in range(1, len(sentences) + 1) if n not in mapped]
    if missing:
        issues.append(f"{UNMAPPED}: {', '.join(f'S{n}' for n in missing)}")

    return Read(
        answers=answers,
        approvals=approvals[:max_items["approvals"]],
        custom_items=custom[:max_items["custom"]],
        migration_objects=migration[:max_items["migration"]],
        not_covered={n for n, c in mapped.items() if not c} | set(missing),
        bank_matches=bank_matches,
        scope_summary=_clean(raw.get("scope_summary")),
        issues=issues,
        raw=raw,
    )


class Interpreter:
    def __init__(self, client, model, max_items, bank, guide_text):
        self._client = client
        self._model = model
        self._max_items = max_items
        self.bank = bank
        self._bank_ids = frozenset(q.qid for q in bank.questions)
        self._system = SYSTEM_PROMPT + "\n\n" + guide_text.strip()

    def _call(self, catalog, customer, sentences, form, reverse):
        try:
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=12000,
                system=self._system,
                tools=[TOOL],
                # current models reject temperature and forced tool_choice; the system prompt asks for submit_scope
                tool_choice={"type": "auto"},
                messages=[{"role": "user", "content": build_user_message(catalog, self.bank, customer, sentences, form, reverse)}],
            )
        except Exception as exc:
            raise InterpretationError(f"Claude request failed: {exc}") from exc
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == TOOL["name"]:
                return block.input if isinstance(block.input, dict) else json.loads(block.input)
        raise InterpretationError("Claude did not return scope answers")

    def read(self, catalog, customer, sentences, form, reverse=False):
        if not sentences:
            raise InterpretationError("The requirement is empty")
        result = validate(self._call(catalog, customer, sentences, form, reverse), sentences, self._max_items, self._bank_ids)
        if any(i.startswith(UNMAPPED) for i in result.issues):
            retry = validate(self._call(catalog, customer, sentences, form, reverse), sentences, self._max_items, self._bank_ids)
            if not any(i.startswith(UNMAPPED) for i in retry.issues):
                return retry
        return result

    def read_twice(self, catalog, customer, sentences, form):
        return (self.read(catalog, customer, sentences, form, False),
                self.read(catalog, customer, sentences, form, True))
