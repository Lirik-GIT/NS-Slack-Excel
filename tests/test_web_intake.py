import json
import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text

from app import intake, reminder
from app import slack_ui as ui
from app.intake import Intake, InvalidMessage, mask_email, parse
from app.questions import ADDON
from app.store import Store
from tests.conftest import payload
from tests.test_bot_flow import FakeSlack, make_bot
from tests.test_end_to_end import READ, REQ

LIMITS = {"max_files": 3, "max_mb_each": 10}


def message(**over):
    body = {"submission_id": "sub-0001-aaaa", "email": "Ae.One@Oracle.com", "customer": "RiverBed", "opportunity": "OPP-9",
            "deal_type": ADDON, "country": "us", "currency": "USD", "requirement": REQ, "privacy_ack": True,
            "areas": [], "addons": ["Q39"], "attachments": []}
    body.update(over)
    return body


class FakeSQS:
    def __init__(self, *bodies):
        self.messages = [{"MessageId": f"m{i}", "ReceiptHandle": f"r{i}", "Body": b if isinstance(b, str) else json.dumps(b)}
                         for i, b in enumerate(bodies)]
        self.deleted = []

    def receive_message(self, **kw):
        batch, self.messages = self.messages, []
        return {"Messages": batch}

    def delete_message(self, QueueUrl, ReceiptHandle):
        self.deleted.append(ReceiptHandle)


class FakeS3:
    def download_file(self, bucket, key, path):
        if key == "missing":
            raise OSError("not found")
        open(path, "wb").write(b"%PDF-1.4 fake")


class Inline:
    def submit(self, fn, *args):
        fn(*args)


class FakeMailer:
    def __init__(self):
        self.sent = []

    def send(self, kind, req, **ctx):
        self.sent.append((kind, req, ctx))


def web_bot(tmp_path, monkeypatch, fake_claude, read, *sqs_messages):
    bot = make_bot(tmp_path, monkeypatch, fake_claude, "CREVIEW", frozenset({"UREV"}))
    bot.set_reads(read)
    bot.pool = Inline()
    bot.mailer = FakeMailer()
    slack = FakeSlack()
    bot.app = type("A", (), {"client": slack})()
    bot.s = bot.s.__class__(**{**bot.s.__dict__, "web_retry_delay": 0})
    sqs = FakeSQS(*sqs_messages)
    return bot, slack, sqs, Intake(bot, sqs, FakeS3(), "q", "bucket", bot.web, threading.Event())


def test_parse_valid_and_normalised():
    d = parse(message(), ["US", "CA"], LIMITS)
    assert d["form"] == {"Q01": ADDON, "Q39": "Yes"} and d["meta"]["in_pilot_country"] and d["meta"]["country"] == "US"
    assert intake.requester_id(d["email"]) == "email:ae.one@oracle.com"
    assert not parse(message(country="DE"), ["US", "CA"], LIMITS)["meta"]["in_pilot_country"]


@pytest.mark.parametrize("over", [
    {"email": "ae@gmail.com"}, {"email": "a@oracle.com.evil.io"}, {"deal_type": "other"}, {"currency": "EUR"},
    {"privacy_ack": False}, {"requirement": "short"}, {"addons": ["Q99"]}, {"subsidiaries": True}, {"subsidiaries": 900},
    {"submission_id": "x y;drop"}, {"attachments": [{"key": "k", "name": "a.pdf", "size": 11 * 1024 * 1024}]},
    {"attachments": [{"key": "k", "name": "a.pdf", "size": 1}] * 4},
])
def test_parse_rejects(over):
    with pytest.raises(InvalidMessage):
        parse(message(**over), ["US", "CA"], LIMITS)


def test_mask_email():
    assert mask_email("ae.one@oracle.com") == "a***@oracle.com"


def test_invalid_message_stays_on_queue(tmp_path, monkeypatch, fake_claude):
    bot, slack, sqs, intk = web_bot(tmp_path, monkeypatch, fake_claude, READ, "not json", message(email="x@gmail.com"))
    intk.poll_once(wait=0)
    assert sqs.deleted == [] and bot.store.by_source_status("web", ("received",)) == []


def test_duplicate_submission_processed_once(tmp_path, monkeypatch, fake_claude):
    bot, slack, sqs, intk = web_bot(tmp_path, monkeypatch, fake_claude, READ, message(), message())
    intk.poll_once(wait=0)
    assert sqs.deleted == ["r0", "r1"]
    assert len(bot.store.by_source_status("web", ("in_review",))) == 1


def test_web_request_reviewed_then_emailed_without_internals(tmp_path, monkeypatch, fake_claude):
    bot, slack, sqs, intk = web_bot(tmp_path, monkeypatch, fake_claude, READ, message(attachments=[
        {"key": "k1", "name": "../../scope doc.pdf", "size": 100}, {"key": "missing", "name": "b.pdf", "size": 100}]))
    intk.poll_once(wait=0)
    req = bot.store.by_submission("sub-0001-aaaa")
    assert req["source"] == "web" and req["requester_id"] == "email:ae.one@oracle.com" and req["status"] == "in_review"
    assert req["review_at"] is not None
    card = json.dumps(slack.posts("CREVIEW")[0][3], ensure_ascii=False)
    assert "Web form: ae.one@oracle.com" in card and "Send to AE" in card
    assert not [c for c in slack.calls if c[0] == "open"]  # no Slack DM to an email requester
    uploads = [c[1]["filename"] for c in slack.calls if c[0] == "upload"]
    assert any(f.endswith("_v1.xlsx") for f in uploads) and "scope_doc.pdf" in uploads
    assert any("b.pdf" in c[2] and "could not be loaded" in c[2] for c in slack.posts("CREVIEW"))
    assert bot.mailer.sent == []  # nothing is emailed before approval

    assert bot.store.transition(req["id"], ("in_review",), "UREV", "approved", status="delivered", reviewer_id="UREV")
    bot._deliver(slack, req["id"], approved_by="UREV")
    kind, sent_req, ctx = bot.mailer.sent[-1]
    assert kind == "approved" and ctx["approved_by"] == "UREV" and ctx["results"]["rom_low"] == 15000
    assert not [c for c in slack.calls if c[0] == "open"]
    assert "Web form: a***@oracle.com" in ui.log_blocks(req, ctx["results"], "delivered")[0]["text"]["text"]


def test_outside_us_canada_is_flagged(tmp_path, monkeypatch, fake_claude):
    bot, slack, sqs, intk = web_bot(tmp_path, monkeypatch, fake_claude, READ, message(country="DE"))
    intk.poll_once(wait=0)
    req = bot.store.by_submission("sub-0001-aaaa")
    assert any("excluded localization" in r for r in json.loads(req["review_reasons_json"]))


def test_web_always_reviewed_even_when_policy_says_no(tmp_path, monkeypatch, fake_claude):
    bot, slack, sqs, intk = web_bot(tmp_path, monkeypatch, fake_claude, payload(answers=[("Q39", "Yes", 1, "Advanced Revenue Management")], n=1),
                                    message(requirement="They need Advanced Revenue Management.", addons=["Q39"]))
    bot.policy["review"]["review_all"] = False
    intk.poll_once(wait=0)
    assert bot.store.by_submission("sub-0001-aaaa")["status"] == "in_review" and bot.mailer.sent == []


def test_failure_retries_three_times_then_generic_email(tmp_path, monkeypatch, fake_claude):
    bot, slack, sqs, intk = web_bot(tmp_path, monkeypatch, fake_claude, payload(n=1),
                                    message(requirement="Something vague here please.", addons=[]))
    intk.poll_once(wait=0)
    req = bot.store.by_submission("sub-0001-aaaa")
    assert req["status"] == "failed" and req["error"]
    events = bot.store.engine.connect().execute(text("select kind from estimate_events where request_id=:r"), {"r": req["id"]}).scalars().all()
    assert events.count("retry") == 2 and events.count("failed") == 1
    kind, _, ctx = bot.mailer.sent[-1]
    assert kind == "failed" and set(ctx) == set()  # the email layer is given no error text at all
    assert "after 3 attempts" in slack.posts("CREVIEW")[-1][2] and req["error"][:20] in slack.posts("CREVIEW")[-1][2]


def test_return_emails_note_and_modal_warns(tmp_path, monkeypatch, fake_claude):
    bot, slack, sqs, intk = web_bot(tmp_path, monkeypatch, fake_claude, READ, message())
    intk.poll_once(wait=0)
    modal = ui.return_modal("R", "C", "1.1", external=True)
    assert "external Oracle AE" in json.dumps(modal) and "external" not in json.dumps(ui.return_modal("R", "C", "1.1"))
    req = bot.store.by_submission("sub-0001-aaaa")
    bot._email(slack, req, "returned", note="Which states?")
    assert bot.mailer.sent[-1][0] == "returned" and bot.mailer.sent[-1][2] == {"note": "Which states?"}


def test_email_failure_is_reported_in_thread(tmp_path, monkeypatch, fake_claude):
    bot, slack, sqs, intk = web_bot(tmp_path, monkeypatch, fake_claude, READ, message())
    intk.poll_once(wait=0)
    bot.mailer = None
    bot._email(slack, bot.store.by_submission("sub-0001-aaaa"), "approved")
    assert "not sent" in slack.posts("CREVIEW")[-1][2] and "a***@oracle.com" in slack.posts("CREVIEW")[-1][2]


def test_resume_reruns_stuck_requests(tmp_path, monkeypatch, fake_claude):
    bot, slack, sqs, intk = web_bot(tmp_path, monkeypatch, fake_claude, READ)
    rid = bot.store.create("email:ae@oracle.com", "ae@oracle.com", "RiverBed", "", REQ, False, "h", "m",
                           form={"Q01": ADDON, "Q39": "Yes"}, source="web", submission_id="sub-stuck-1", meta={"in_pilot_country": True})
    bot.store.update(rid, status="estimating")
    intk.resume()
    assert bot.store.get(rid)["status"] == "in_review"


def test_business_hours():
    cfg = {"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "start": "09:00", "end": "17:00", "timezone": "America/New_York"}
    utc = lambda *a: datetime(*a, tzinfo=timezone.utc)  # noqa: E731
    # Fri 2026-11-06 16:00 NY (21:00 UTC) -> Mon 2026-11-09 10:00 NY (15:00 UTC): 1h Friday + 1h Monday
    assert reminder.business_hours_between(utc(2026, 11, 6, 21), utc(2026, 11, 9, 15), cfg) == 2
    assert reminder.business_hours_between(utc(2026, 11, 7, 15), utc(2026, 11, 8, 15), cfg) == 0  # weekend


def test_reminder_posts_once(tmp_path, monkeypatch, fake_claude):
    bot, slack, sqs, intk = web_bot(tmp_path, monkeypatch, fake_claude, READ, message())
    intk.poll_once(wait=0)
    rid = bot.store.by_submission("sub-0001-aaaa")["id"]
    now = datetime.now(timezone.utc)
    reminder.remind_once(bot, now=now)
    assert not [c for c in slack.posts("CREVIEW") if "Reminder" in c[2]]
    later = datetime(now.year + 1, 1, 6, 20, tzinfo=timezone.utc)  # a weekday far enough ahead
    reminder.remind_once(bot, now=later)
    reminder.remind_once(bot, now=later)
    assert len([c for c in slack.posts("CREVIEW") if "Reminder" in c[2]]) == 1 and bot.store.get(rid)["reminded_at"]


def test_old_database_is_upgraded(tmp_path):
    url = f"sqlite:///{tmp_path / 'old.db'}"
    with create_engine(url).begin() as cx:
        _old_schema(cx)
    store = Store(url)
    Store(url)  # second start is a no-op
    old = store.get("OLD1")
    assert old["source"] == "slack" and old["submission_id"] is None
    rid = store.create("email:ae@oracle.com", None, "C", "", "req text", False, "h", "m", source="web", submission_id="s-1")
    assert store.by_submission("s-1")["id"] == rid


def _old_schema(cx):
    cx.execute(text("create table estimate_requests (id varchar(16) primary key, created_at datetime, updated_at datetime, "
                    "requester_id varchar(32) not null, requester_name varchar(200), customer varchar(300) not null, "
                    "opportunity varchar(500), requirement text not null, form_json text, strategic boolean not null, "
                    "status varchar(20) not null, deal_type varchar(40), interpretation_json text, results_json text, "
                    "review_reasons_json text, review_channel varchar(32), review_ts varchar(32), log_ts varchar(32), "
                    "reviewer_id varchar(32), decision_note text, workbook_path varchar(1000), version integer not null, "
                    "template_sha256 varchar(64), model varchar(100), error text)"))
    cx.execute(text("insert into estimate_requests (id, created_at, updated_at, requester_id, customer, requirement, strategic, status, version) "
                    "values ('OLD1', '2026-10-01', '2026-10-01', 'U1', 'Acme', 'req', 0, 'delivered', 1)"))
