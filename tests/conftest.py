from pathlib import Path
from types import SimpleNamespace

import pytest

from app.catalog import load_catalog
from app.config import load_guide, load_policy
from app.interpreter import Interpreter
from app.question_bank import load_bank
from app.workbook import Recalculator

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "NetSuite_Project_Estimator_Master.xlsx"
BLANK = ROOT / "templates" / "source" / "NetSuite_Project_Estimator_Master_Blank.xlsx"


@pytest.fixture(scope="session")
def catalog():
    return load_catalog(TEMPLATE)


@pytest.fixture
def policy():
    return load_policy(ROOT / "config" / "policy.json")


@pytest.fixture(scope="session")
def bank():
    return load_bank(ROOT / "config" / "question_bank.xlsx")


@pytest.fixture(scope="session")
def guide():
    return load_guide(ROOT / "config" / "estimator_guide.md")


@pytest.fixture
def make_interpreter(bank, guide):
    def make(*payloads):
        return Interpreter(FakeClaude(*payloads), "m", MAX_ITEMS, bank, guide)
    return make


@pytest.fixture(scope="session")
def recalculator(tmp_path_factory):
    return Recalculator("soffice", tmp_path_factory.mktemp("lo"), 120)


class FakeClaude:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        payload = self.payloads[min(len(self.calls), len(self.payloads) - 1)]
        self.calls.append(kwargs)
        block = SimpleNamespace(type="tool_use", name="submit_scope", input=payload)
        return SimpleNamespace(content=[block])


@pytest.fixture
def fake_claude():
    return FakeClaude


MAX_ITEMS = {"approvals": 20, "custom": 60, "migration": 20}


def payload(answers=(), approvals=(), custom=(), migration=(), sentence_map=None, n=1, summary="Scope", bank=()):
    return {
        "scope_summary": summary,
        "answers": [{"id": a[0], "value": str(a[1]), "sentences": [a[2]], "quote": a[3]} for a in answers],
        "approvals": [{"process": p, "levels": lv, "sentences": [s], "quote": q} for p, lv, s, q in approvals],
        "custom_items": [{"name": nm, "type": t, "size": z, "sentences": [s], "quote": q} for nm, t, z, s, q in custom],
        "migration_objects": [{"name": nm, "size": z, "sentences": [s], "quote": q} for nm, z, s, q in migration],
        "sentence_map": sentence_map if sentence_map is not None else [{"sentence": i, "covered_by": ["x"]} for i in range(1, n + 1)],
        "bank_matches": [{"id": b, "sentences": [s], "quote": q} for b, s, q in bank],
    }
