import json
from email import message_from_bytes, policy as email_policy

import pytest

from app import tokens
from app.mailer import FAILED_TEXT, Mailer, handle_ses_event, one_line
from app.store import Store
from tests.test_bot_flow import FakeSlack
from tests.test_pdf_leak import CANARIES, INTERP, POLICY, RESULTS

CFG = {"email": {"sender": "estimates@lirik.example", "form_url": "https://forms.example.invalid/", "configuration_set": "cs"},
       "reviewers": {"lirik_presales_owner_email": "owner@lirik.example"}, "limits": {"resubmit_link_days": 14}}
KEY = "test-hmac-key"


class FakeSES:
    def __init__(self):
        self.calls = []

    def send_raw_email(self, **kw):
        self.calls.append(kw)
        return {"MessageId": f"mid-{len(self.calls)}"}


class StubRenderer:
    def render(self, doc, results):
        return b"%PDF-stub " + doc["headline"].encode()


@pytest.fixture
def setup(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'db.sqlite'}")
    rid = store.create("email:ae@oracle.com", "ae@oracle.com", "Acme\r\nBcc: evil@example.com", "", "requirement text here", False, "h", "m",
                       source="web", submission_id="sub-1", meta={"currency": "CAD", "country": "CA"})
    store.update(rid, review_channel="CREVIEW", review_ts="1.1", decision_note="CANARY_NOTE", error="CANARY_ERROR")
    ses = FakeSES()
    return store, store.get(rid), ses, Mailer(store, ses, CFG, POLICY, StubRenderer(), KEY)


def parsed(call):
    return message_from_bytes(call["RawMessage"]["Data"], policy=email_policy.default)


def test_approved_email_has_pdf_cc_and_no_internals(setup):
    store, req, ses, mailer = setup
    mid = mailer.send("approved", req, interp=INTERP, results=RESULTS, title="t", approved_by="UREV")
    call = ses.calls[0]
    msg = parsed(call)
    assert mid == "mid-1" and call["Destinations"] == ["ae@oracle.com", "owner@lirik.example"] and call["ConfigurationSetName"] == "cs"
    assert msg["From"] == "estimates@lirik.example" and msg["Cc"] == "owner@lirik.example" and msg["Bcc"] is None
    assert "\n" not in msg["Subject"] and "evil@example.com" not in str(msg["To"]) + str(msg["Cc"])
    assert [p.get_content_type() for p in msg.iter_attachments()] == ["application/pdf"]
    assert msg.get_body().get_content().count("C$21,000") == 1 and "$" in msg.get_body().get_content()
    everything = call["RawMessage"]["Data"].decode("utf-8", "ignore")
    assert not [c for c in CANARIES if c in everything]


def test_returned_email_has_note_and_verifiable_link(setup):
    store, req, ses, mailer = setup
    mailer.send("returned", req, note="Which US states do you collect tax in?")
    body = parsed(ses.calls[0]).get_body().get_content()
    assert "Which US states do you collect tax in?" in body
    link = [w for w in body.split() if w.startswith("https://forms.example.invalid/resubmit?t=")][0]
    claims = tokens.verify(KEY, link.split("t=")[1])
    assert claims["sid"] == "sub-1" and claims["k"] == "resubmit"
    assert "CANARY" not in body and parsed(ses.calls[0])["Cc"] is None


def test_failed_email_is_generic(setup):
    store, req, ses, mailer = setup
    mailer.send("failed", req, error="Traceback CANARY_ERROR at /srv/app")
    text = parsed(ses.calls[0]).get_body().get_content()
    assert FAILED_TEXT in text and "CANARY" not in text and "Traceback" not in text


def test_unknown_kind_and_missing_link_config(setup):
    store, req, ses, mailer = setup
    with pytest.raises(ValueError):
        mailer.send("nope", req)
    with pytest.raises(RuntimeError):
        Mailer(store, ses, {**CFG, "email": {"sender": "a@b"}}, POLICY, StubRenderer(), KEY).send("returned", req, note="x")
    assert ses.calls == []


def test_header_injection_is_flattened():
    assert one_line("Acme\r\nBcc: evil@example.com\x00") == "Acme Bcc: evil@example.com"


def test_bounce_and_complaint_posted_in_review_thread(setup):
    store, req, ses, mailer = setup
    mailer.send("failed", req)
    slack = FakeSlack()
    bounce = {"eventType": "Bounce", "mail": {"messageId": "mid-1"}, "bounce": {"bounceType": "Permanent", "bounceSubType": "General"}}
    assert handle_ses_event(json.dumps({"Message": json.dumps(bounce)}), store, slack, "CDEFAULT")
    post = slack.posts("CREVIEW")[0]
    assert post[4] == "1.1" and "bounced (Permanent/General)" in post[2] and "a***@oracle.com" in post[2] and "ae@oracle.com" not in post[2]
    assert handle_ses_event(json.dumps({"notificationType": "Complaint", "mail": {"messageId": "mid-1"}, "complaint": {}}), store, slack, "C")
    assert "spam" in slack.posts("CREVIEW")[1][2]
    assert not handle_ses_event(json.dumps({"eventType": "Delivery", "mail": {"messageId": "mid-1"}}), store, slack, "C")
    assert not handle_ses_event(json.dumps({"eventType": "Bounce", "mail": {"messageId": "other"}}), store, slack, "C")


def test_tokens_reject_tamper_expiry_and_garbage():
    t = tokens.sign(KEY, {"sid": "s"}, 60, now=1000)
    assert tokens.verify(KEY, t, now=1030)["sid"] == "s"
    assert tokens.verify(KEY, t, now=1061) is None  # expired
    assert tokens.verify("other-key", t, now=1030) is None
    body, sig = t.split(".")
    forged = tokens.sign(KEY, {"sid": "attacker"}, 60, now=1000).split(".")[0] + "." + sig
    assert tokens.verify(KEY, forged, now=1030) is None
    for junk in ("", "a", "a.b", "....", "💥.💥"):
        assert tokens.verify(KEY, junk) is None
