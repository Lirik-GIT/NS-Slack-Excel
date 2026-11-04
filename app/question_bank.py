import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook

from .questions import BY_ID, LIST_APPROVALS, LIST_CUSTOM, LIST_MIGRATION
from .sentences import normalise

SHEET = "Question bank"
COLUMNS = {
    "id": "ID",
    "area": "Area",
    "when": "When to ask (Claude reads this)",
    "keywords": "Keywords (code matches these)",
    "question": "Question (exact wording)",
    "changes": "What the answer changes",
    "linked": "Linked estimator item",
    "skip": "Skip if answered",
    "assumes": "ROM assumes if not answered",
}
_ID = re.compile(r"^[A-Z0-9]{2,4}-\d{2}$")


class BankError(Exception):
    pass


@dataclass(frozen=True)
class BankQuestion:
    qid: str
    area: str
    when: str
    keywords: tuple
    question: str
    changes: str
    linked: str
    skip: tuple
    assumes: str

    @property
    def flags_reviewer(self):
        return self.changes.lower().startswith("flagged")


@dataclass(frozen=True)
class Bank:
    questions: tuple
    sha256: str

    def get(self, qid):
        for q in self.questions:
            if q.qid == qid:
                return q
        return None


def _text(v):
    return " ".join(str(v).split()) if v is not None else ""


def load_bank(path):
    path = Path(path)
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise BankError(f"Cannot open question bank {path}: {exc}") from exc
    if SHEET not in wb.sheetnames:
        raise BankError(f"Question bank has no '{SHEET}' tab")
    rows = list(wb[SHEET].iter_rows(values_only=True))
    if not rows:
        raise BankError("Question bank is empty")
    header = [_text(h) for h in rows[0]]
    idx = {}
    for key, label in COLUMNS.items():
        if label not in header:
            raise BankError(f"Question bank is missing the column '{label}'")
        idx[key] = header.index(label)
    questions, seen = [], set()
    for n, row in enumerate(rows[1:], start=2):
        if not any(row):
            continue
        get = lambda k: _text(row[idx[k]]) if idx[k] < len(row) else ""
        qid = get("id")
        problems = []
        if not _ID.match(qid):
            problems.append("ID must look like P2P-01")
        if qid in seen:
            problems.append("duplicate ID")
        if not get("question"):
            problems.append("question is empty")
        if not get("when"):
            problems.append("'When to ask' is empty")
        skip = tuple(s for s in (p.strip().upper() for p in get("skip").split(",")) if s)
        bad = [s for s in skip if s not in BY_ID and s not in (LIST_APPROVALS, LIST_CUSTOM, LIST_MIGRATION)]
        if bad:
            problems.append(f"unknown question IDs in 'Skip if answered': {', '.join(bad)}")
        keywords = tuple(dict.fromkeys(normalise(k) for k in get("keywords").split(",") if normalise(k)))
        if problems:
            raise BankError(f"Question bank row {n} ({qid or 'no ID'}): {'; '.join(problems)}")
        seen.add(qid)
        questions.append(BankQuestion(qid, get("area"), get("when"), keywords, get("question"), get("changes"),
                                      get("linked"), skip, get("assumes")))
    if not questions:
        raise BankError("Question bank has no questions")
    return Bank(tuple(questions), hashlib.sha256(path.read_bytes()).hexdigest())


def _pattern(keyword):
    return re.compile(r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])")


def keyword_hits(bank, sentences):
    normalised = [normalise(s) for s in sentences]
    hits = {}
    for q in bank.questions:
        for kw in q.keywords:
            pat = _pattern(kw)
            for n, text in enumerate(normalised, start=1):
                if pat.search(text):
                    hits[q.qid] = {"sentence": n, "keyword": kw}
                    break
            if q.qid in hits:
                break
    return hits
