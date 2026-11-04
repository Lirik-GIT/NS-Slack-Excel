import shutil
from dataclasses import dataclass
from pathlib import Path

from .catalog import load_catalog
from .question_bank import keyword_hits
from .resolve import resolve
from .review import review_reasons
from .sentences import split
from .workbook import (check_template, read_inputs, read_line_hours, read_results, temp_dir, write_inputs,
                       write_pricing)


@dataclass
class Estimate:
    scope: object
    results: object
    reasons: list
    line_hours: dict
    workbook_path: Path


class Estimator:
    def __init__(self, catalog, policy, interpreter, recalculator, template_path, files_dir):
        self.catalog = catalog
        self.policy = policy
        self.interpreter = interpreter
        self.recalc = recalculator
        self.template_path = Path(template_path)
        self.files_dir = Path(files_dir)

    @property
    def bank(self):
        return self.interpreter.bank

    def scope_for(self, customer, requirement, form):
        sentences = split(requirement)
        read_a, read_b = self.interpreter.read_twice(self.catalog, customer, sentences, form)
        return resolve(form, read_a, read_b, sentences, self.catalog.item_hours, self.bank,
                       keyword_hits(self.bank, sentences), self.policy.get("standard_exclusions", []))

    def estimate(self, request_id, customer, requirement, form, strategic):
        scope = self.scope_for(customer, requirement, form)
        dest = self.files_dir / request_id / f"{request_id}_v1.xlsx"
        work = temp_dir(self.files_dir / "tmp")
        try:
            book = work / "estimate.xlsx"
            write_inputs(self.template_path, book, self.catalog, scope, self.bank.sha256)
            self.recalc.recalc(book)
            results = read_results(book, self.catalog)
            hours = read_line_hours(book, self.catalog)
            if results.dev_days <= 0:
                raise ValueError("No hours resulted from the answers; the requirement does not describe any NetSuite scope")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(book), dest)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        reasons = review_reasons(self.policy["review"], scope, results, strategic)
        return Estimate(scope, results, reasons, hours, dest)

    def _recalculate(self, source, request_id, version, pricing=None):
        dest = self.files_dir / request_id / f"{request_id}_v{version}.xlsx"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        check_template(dest)
        catalog = load_catalog(dest)
        if pricing:
            write_pricing(dest, catalog, pricing)
        self.recalc.recalc(dest)
        answers, approvals, custom, migration = read_inputs(dest, catalog)
        return (read_results(dest, catalog), read_line_hours(dest, catalog), answers, approvals, custom, migration, dest)

    def recalculate_upload(self, request_id, upload_path, version):
        return self._recalculate(upload_path, request_id, version)

    def reprice(self, request_id, current_path, version, onsite_rate, offshore_rate, onsite_share):
        return self._recalculate(current_path, request_id, version,
                                 {"onsite_rate": onsite_rate, "offshore_rate": offshore_rate, "onsite_share": onsite_share})
