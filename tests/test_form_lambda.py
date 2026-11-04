"""Unit tests for the form Lambda that need no AWS: validation, the page, routing, link tokens and the contract with app/intake.py.
The confirm -> SQS -> intake -> approve flow against fake AWS services is covered in phase 4."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app import intake, pdf
from tests.conftest import ROOT
from tools.build_form_options import options

FORM_DIR = ROOT / "lambda" / "form"
sys.path.insert(0, str(FORM_DIR))  # the Lambda imports its own tokens.py
spec = importlib.util.spec_from_file_location("form_lambda", FORM_DIR / "app.py")
form = importlib.util.module_from_spec(spec)
spec.loader.exec_module(form)

GOOD = {"email": "Ae.One@Oracle.com", "customer": "RiverBed", "opportunity": "OPP-9", "deal_type": "Add-on to existing account",
        "country": "us", "currency": "CAD", "subsidiaries": 3, "areas": ["Q07"], "addons": ["Q39"],
        "requirement": "They need Advanced Revenue Management and 2,500 contracts migrated.", "privacy_ack": True}


class Counter:
    """Stands in for DynamoDB counters, SSM and nothing else: enough for the paths that stop before any other AWS call."""
    def __init__(self, approved="ae.one@oracle.com"):
        self.counts, self.approved, self.calls = {}, approved, []

    def update_item(self, **kw):
        key = kw["Key"]["pk"]["S"]
        self.counts[key] = self.counts.get(key, 0) + 1
        return {"Attributes": {"n": {"N": str(self.counts[key])}}}

    def get_parameter(self, Name, WithDecryption):
        return {"Parameter": {"Value": self.approved if "approved" in Name else "test-hmac-key"}}

    def __getattr__(self, name):  # any other AWS call in these tests is a bug
        raise AssertionError(f"unexpected AWS call {name}")


def event(method, path, body=None, query=None):
    return {"httpMethod": method, "path": path, "queryStringParameters": query, "body": json.dumps(body) if body is not None else None,
            "requestContext": {"domainName": "abc.execute-api.us-east-1.amazonaws.com", "stage": "prod", "identity": {"sourceIp": "203.0.113.9"}}}


@pytest.fixture
def lam(monkeypatch):
    fake = Counter()
    form.aws.cache_clear()
    form._secret.cache_clear()
    monkeypatch.setattr(form, "aws", lambda service: fake)
    for k, v in {"BUCKET": "bkt", "TABLE": "tbl", "HMAC_PARAM": "/p/hmac", "APPROVED_PARAM": "/p/approved", "SENDER": "estimates@lirik.example",
                 "CONFIG_SET": "cs", "DAILY_LIMIT": "10", "IP_HOURLY_LIMIT": "20"}.items():
        monkeypatch.setenv(k, v)
    return fake


def test_options_json_matches_the_bot():
    assert json.loads((FORM_DIR / "options.json").read_text(encoding="utf-8")) == json.loads(json.dumps(options()))


def test_token_code_is_the_same_as_the_bots():
    assert (FORM_DIR / "tokens.py").read_text(encoding="utf-8") == (ROOT / "app" / "tokens.py").read_text(encoding="utf-8")


def test_reference_matches_the_bots():
    sid = "0123456789abcdef0123456789abcdef"
    assert form.reference(sid) == pdf.reference({"id": "X", "source": "web", "submission_id": sid}) == "01234567"


def test_valid_form_is_accepted_and_matches_the_intake_contract():
    f, bad = form.validate(GOOD)
    assert bad == [] and f["email"] == "ae.one@oracle.com" and f["country"] == "US"
    parsed = intake.parse(f, ["US", "CA"], {"max_files": 3, "max_mb_each": 10})  # exactly what the Lambda puts on the queue
    assert parsed["form"]["Q01"] == GOOD["deal_type"] and parsed["form"]["Q39"] == "Yes" and parsed["meta"]["currency"] == "CAD"


@pytest.mark.parametrize("over, field", [
    ({"email": "ae@gmail.com"}, "email"), ({"email": "a@oracle.com.evil.io"}, "email"), ({"customer": ""}, "customer"),
    ({"customer": "x" * 201}, "customer"), ({"requirement": "short"}, "requirement"), ({"requirement": "x" * 3001}, "requirement"),
    ({"deal_type": "Other"}, "deal_type"), ({"country": "USA"}, "country"), ({"currency": "EUR"}, "currency"),
    ({"privacy_ack": "true"}, "privacy_ack"), ({"subsidiaries": 0}, "subsidiaries"), ({"subsidiaries": True}, "subsidiaries"),
    ({"areas": ["Q99"]}, "areas"), ({"addons": "Q39"}, "addons"), ({"submission_id": "../x"}, "submission_id"),
    ({"attachments": [{}] * 4}, "attachments"),
])
def test_invalid_fields_are_named_and_nothing_else(over, field):
    _, bad = form.validate({**GOOD, **over})
    assert field in bad


def test_control_characters_are_stripped():
    f, _ = form.validate({**GOOD, "customer": "Acme\r\nBcc: x@y.z", "requirement": GOOD["requirement"] + "\x00\x07 ok\nline"})
    assert "\n" not in f["customer"] and "\x00" not in f["requirement"] and "\nline" in f["requirement"]


def test_page_has_nonce_csp_and_escapes_hostile_prefill(lam, monkeypatch):
    monkeypatch.setenv("CAPTCHA_SCRIPT_URL", "https://abc.eu-west-1.captcha-sdk.awswaf.com/abc/jsapi.js")
    r = form.handler(event("GET", "/"))
    assert r["statusCode"] == 200 and r["headers"]["Content-Type"].startswith("text/html")
    nonce = r["headers"]["Content-Security-Policy"].split("'nonce-")[1].split("'")[0]
    assert f'nonce="{nonce}"' in r["body"] and "{{" not in r["body"] and "unsafe-inline" not in r["headers"]["Content-Security-Policy"]
    assert "captcha-sdk.awswaf.com" in r["headers"]["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in r["headers"]["Content-Security-Policy"] and r["headers"]["X-Content-Type-Options"] == "nosniff"
    hostile = form.page(event("GET", "/resubmit"), {"customer": "</script><script>alert(1)</script>"}, "</script>")
    assert "</script><script>alert(1)" not in hostile["body"] and "\\u003c/script" in hostile["body"]


def test_unknown_route_bad_json_and_errors_are_generic(lam):
    assert form.handler(event("GET", "/admin"))["statusCode"] == 404
    bad = event("POST", "/submit")
    bad["body"] = "{not json"
    assert form.handler(bad)["statusCode"] == 400
    assert form.handler(event("POST", "/submit", ["list"]))["statusCode"] == 400
    r = form.handler(event("GET", "/confirm", query={"t": "garbage"}))
    assert r["statusCode"] == 400 and "expired" in r["body"] and "garbage" not in r["body"]
    boom = event("POST", "/submit", GOOD)
    boom["requestContext"] = {}
    r = form.handler(boom)
    assert r["statusCode"] == 500 and json.loads(r["body"]) == {"error": "Something went wrong. Please try again later."}


def test_invalid_submission_returns_field_names_only(lam):
    r = form.handler(event("POST", "/submit", {**GOOD, "email": "x@gmail.com", "currency": "EUR"}))
    assert r["statusCode"] == 400 and set(json.loads(r["body"])["fields"]) == {"email", "currency"}


def test_unapproved_address_gets_the_same_answer_as_an_approved_one(lam):
    lam.approved = "someone.else@oracle.com"
    r = form.handler(event("POST", "/submit", GOOD))
    assert r["statusCode"] == 200 and json.loads(r["body"]) == form.GENERIC_OK  # no mail sent: send_mail would hit the stub and fail


def test_ip_rate_limit(lam):
    lam.approved = ""
    codes = [form.handler(event("POST", "/submit", GOOD))["statusCode"] for _ in range(22)]
    assert codes[:20] == [200] * 20 and codes[20:] == [429, 429]


def test_daily_limit_per_ae_is_silent(lam, monkeypatch):
    monkeypatch.setenv("IP_HOURLY_LIMIT", "1000")
    monkeypatch.setenv("DAILY_LIMIT", "2")
    lam.approved = "ae.one@oracle.com"
    lam.put_item = lambda **kw: (_ for _ in ()).throw(AssertionError("must not store"))
    statuses = []
    for i in range(3):
        try:
            statuses.append(form.handler(event("POST", "/submit", GOOD))["statusCode"])
        except AssertionError:
            statuses.append("stored")
    assert statuses[2] == 200 and lam.counts[next(k for k in lam.counts if k.startswith("AE#"))] == 3


def test_upload_urls_reject_bad_files(lam):
    for files in ([], [{"name": "a.exe", "size": 10}], [{"name": "a.pdf", "size": 11 * 1024 * 1024}], [{"name": "noext", "size": 5}],
                  [{"name": "a.pdf", "size": 5}] * 4):
        r = form.handler(event("POST", "/upload-urls", {"files": files}))
        assert r["statusCode"] == 400 and json.loads(r["body"]) == {"fields": ["attachments"]}


def test_template_and_form_files_exist():
    assert (ROOT / "infra" / "oracle-web-form" / "template.yaml").exists() and Path(FORM_DIR / "page.html").exists()


def test_email_domains_setting(lam, monkeypatch):
    monkeypatch.setenv("EMAIL_DOMAINS", "oracle.com, lirik.example")
    assert form.validate({**GOOD, "email": "Pilot@Lirik.example"})[1] == []
    assert form.validate({**GOOD, "email": "a@lirik.example.evil.io"})[1] == ["email"]
