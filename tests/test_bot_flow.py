import json

import pytest
from slack_bolt import App

import app.slack_app as slack_app
from app import slack_ui as ui
from app.config import Settings
from app.interpreter import Interpreter
from tests.conftest import MAX_ITEMS, ROOT, TEMPLATE, payload
from tests.test_end_to_end import FORM, READ, REQ


class FakeSlack:
    def __init__(self):
        self.calls = []
        self._ts = 0

    def _next(self):
        self._ts += 1
        return f"1700000000.{self._ts:06d}"

    def conversations_open(self, users):
        self.calls.append(("open", users))
        return {"channel": {"id": f"D-{users}"}}

    def chat_postMessage(self, channel, text, blocks=None, thread_ts=None):
        ts = self._next()
        self.calls.append(("post", channel, text, blocks, thread_ts, ts))
        return {"ts": ts}

    def chat_update(self, channel, ts, text, blocks=None):
        self.calls.append(("update", channel, ts, text, blocks))
        return {"ts": ts}

    def files_upload_v2(self, **kwargs):
        self.calls.append(("upload", kwargs))
        return {"ok": True}

    def posts(self, channel):
        return [c for c in self.calls if c[0] == "post" and c[1] == channel]


def make_bot(tmp_path, monkeypatch, fake_claude, review_to, reviewers=frozenset()):
    monkeypatch.setattr(slack_app, "App", lambda **kw: App(token_verification_enabled=False, **kw))
    settings = Settings(
        slack_bot_token="xoxb-test", slack_app_token="xapp-test", anthropic_api_key="k", anthropic_model="m",
        review_channel_id=review_to, log_channel_id="CLOG", database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        reviewer_user_ids=reviewers, data_dir=tmp_path, template_path=TEMPLATE,
        policy_path=ROOT / "config" / "policy.json", worker_threads=1,
    )
    b = slack_app.EstimatorBot(settings)
    b.set_reads = lambda *p: setattr(b.estimator, "interpreter", Interpreter(fake_claude(*p), "m", MAX_ITEMS, b.bank, b.guide))
    return b


def test_estimate_lands_with_reviewer_then_goes_to_ae(tmp_path, monkeypatch, fake_claude):
    bot = make_bot(tmp_path, monkeypatch, fake_claude, "UMANISH", frozenset({"UMANISH"}))
    bot.set_reads(READ)
    slack = FakeSlack()
    rid = bot.store.create("UAE", "ae", "RiverBed", "OPP-1", REQ, False, "h", "m", form=FORM)
    bot._run(slack, rid)
    req = bot.store.get(rid)
    assert req["status"] == "in_review" and req["review_channel"] == "D-UMANISH" and req["deal_type"] == FORM["Q01"]
    card = json.dumps(slack.posts("D-UMANISH")[0][3], ensure_ascii=False)
    for text in ("Send to AE", "Adjust pricing", "2,500 open contracts", "Go-live is planned for March",
                 "Revenue plan recalculation", "$15,000 – $22,000", "$16,896", "$64.00/hr", "7 weeks", "keyword"):
        assert text in card, text
    assert [c for c in slack.calls if c[0] == "upload"][0][1]["channel"] == "D-UMANISH"
    assert not [c for c in slack.posts("D-UAE") if "$" in c[2]]
    assert bot._can_review("UMANISH") and not bot._can_review("UAE")

    assert bot.store.transition(rid, ("in_review",), "UMANISH", "approved", status="delivered", reviewer_id="UMANISH")
    bot._refresh_card(slack, rid, decided_by="UMANISH")
    bot._deliver(slack, rid, approved_by="UMANISH")
    bot._log(slack, rid)
    final = slack.posts("D-UAE")[-1]
    assert "$15,000 – $22,000" in final[2] and "7 weeks" in final[2]
    body = json.dumps(final[3], ensure_ascii=False)
    assert "released by <@UMANISH>" in body and "Not priced in this ROM" in body
    assert "16,896" not in body and "/hr" not in body and "rate" not in body.lower()
    assert "Priced for one subsidiary." in body and "50% / 20% / 20% / 10%" in body
    assert "How many subsidiaries?" in body
    req = bot.store.get(rid)
    assert json.loads(req["interpretation_json"])["versions"]["question_bank"] == bot.bank.sha256
    assert [c for c in slack.calls if c[0] == "update" and c[1] == "CLOG"]


def test_direct_flow_when_review_all_is_off(tmp_path, monkeypatch, fake_claude):
    bot = make_bot(tmp_path, monkeypatch, fake_claude, "CREVIEW")
    bot.policy["review"]["review_all"] = False
    small = payload(answers=[("Q39", "Yes", 1, "Advanced Revenue Management")], n=1)
    bot.set_reads(small)
    slack = FakeSlack()
    rid = bot.store.create("UAE", "ae", "Acme", "", "They need Advanced Revenue Management.", False, "h", "m",
                           form={"Q01": "Add-on to existing account", "Q39": "Yes"})
    bot._run(slack, rid)
    assert bot.store.get(rid)["status"] == "delivered"
    assert not slack.posts("CREVIEW")
    assert "Acme" in slack.posts("D-UAE")[-1][2]


def test_failure_is_reported(tmp_path, monkeypatch, fake_claude):
    bot = make_bot(tmp_path, monkeypatch, fake_claude, "CREVIEW")
    bot.set_reads(payload(n=1))
    slack = FakeSlack()
    rid = bot.store.create("UAE", "ae", "Acme", "", "Something vague here please.", False, "h", "m",
                           form={"Q01": "Add-on to existing account"})
    bot._run(slack, rid)
    assert bot.store.get(rid)["status"] == "failed"
    assert slack.posts("CREVIEW") and "couldn't produce" in slack.posts("D-UAE")[-1][2]


def test_adjust_pricing(tmp_path, monkeypatch, fake_claude):
    bot = make_bot(tmp_path, monkeypatch, fake_claude, "UMANISH", frozenset({"UMANISH"}))
    bot.set_reads(READ)
    slack = FakeSlack()
    rid = bot.store.create("UAE", "ae", "RiverBed", "", REQ, False, "h", "m", form=FORM)
    bot._run(slack, rid)
    modal = ui.pricing_modal(rid, json.loads(bot.store.get(rid)["results_json"]))
    assert [b["element"]["initial_value"] for b in modal["blocks"]] == ["120", "50", "20"]
    pricing, errors = ui.parse_pricing({"onsite_rate": {"value": {"value": "150"}}, "offshore_rate": {"value": {"value": "45"}},
                                        "onsite_share": {"value": {"value": "30"}}})
    assert not errors and pricing == {"onsite_rate": 150, "offshore_rate": 45, "onsite_share": 0.3}
    bot._reprice(slack, rid, "UMANISH", pricing)
    req = bot.store.get(rid)
    res = json.loads(req["results_json"])
    assert req["version"] == 2 and res["blended_rate"] == 76.5 and res["price"] == 264 * 76.5
    assert any("Repriced v2" in c[2] for c in slack.posts("D-UMANISH"))
    assert [c for c in slack.calls if c[0] == "upload"][-1][1]["filename"] == f"{rid}_v2.xlsx"
    _, errors = ui.parse_pricing({"onsite_rate": {"value": {"value": "0"}}, "offshore_rate": {"value": {"value": "45"}},
                                  "onsite_share": {"value": {"value": "130"}}})
    assert set(errors) == {"onsite_rate", "onsite_share"}


def test_version_guard(tmp_path, monkeypatch, fake_claude):
    bot = make_bot(tmp_path, monkeypatch, fake_claude, "CREVIEW")
    rid = bot.store.create("UAE", "ae", "Acme", "", "requirement text", False, "h", "m")
    bot.store.update(rid, status="in_review", version=1)
    assert bot.store.transition(rid, ("in_review",), "U", "reviewer_upload", expect_version=1, version=2)
    assert not bot.store.transition(rid, ("in_review",), "U", "reviewer_upload", expect_version=1, version=2)


def _state(**over):
    values = {
        "customer": {"value": {"value": "Acme"}},
        "opportunity": {"value": {"value": None}},
        "deal_type": {"value": {"selected_option": {"value": "New implementation"}}},
        "subsidiaries": {"value": {"value": "3"}},
        "areas": {"value": {"selected_options": [{"value": "Q07"}, {"value": "Q08"}]}},
        "addons": {"value": {"selected_options": [{"value": "Q38"}]}},
        "requirement": {"value": {"value": "Distributor needing P2P and O2C with fixed assets."}},
        "strategic": {"value": {"selected_options": []}},
    }
    values.update(over)
    return values


def test_form_parsing():
    data = ui.parse_request(_state())
    assert data["form"] == {"Q01": "New implementation", "Q02": 3, "Q07": "Yes", "Q08": "Yes", "Q38": "Yes"}
    assert data["opportunity"] == "" and data["strategic"] is False
    data = ui.parse_request(_state(subsidiaries={"value": {"value": None}}, areas={"value": {"selected_options": None}}))
    assert data["form"] == {"Q01": "New implementation", "Q38": "Yes"}


def test_modal_shape():
    modal = ui.request_modal("x" * 4000)
    ids = [b["block_id"] for b in modal["blocks"]]
    assert ids == ["customer", "opportunity", "deal_type", "subsidiaries", "areas", "addons", "requirement", "strategic"]
    assert len(modal["blocks"][4]["element"]["options"]) == 13
    assert len(modal["blocks"][5]["element"]["options"]) == 12
    assert modal["blocks"][6]["element"]["initial_value"] == "x" * 3000
    assert all(len(o["text"]["text"]) <= 75 for b in modal["blocks"][4:6] for o in b["element"]["options"])


@pytest.mark.parametrize("status", ["in_review", "delivered"])
def test_card_limits(status):
    answers = {f"Q{i:02d}": {"value": "Yes", "source": "Requirement", "evidence": "e" * 300} for i in range(1, 57)}
    questions = [{"type": "Bank", "id": f"X-{i}", "question": "q" * 200, "trigger": {"how": "matched", "detail": "d" * 300, "sentence": 1},
                  "assumes": "a"} for i in range(30)]
    interp = {"answers": answers, "approvals": [], "custom_items": [{"name": f"c{i}", "type": "Script", "size": "Small"} for i in range(60)],
              "migration_objects": [], "not_covered": [{"n": i, "text": "t" * 200} for i in range(40)], "conflicts": ["c" * 300] * 20,
              "questions": questions, "assumptions": ["a" * 100] * 20, "form_flags": [], "issues": [], "scope_summary": "s",
              "line_hours": {f"L{i}": {"hours": i, "name": "n", "section": "s"} for i in range(80)}}
    results = {"price": 1, "price_cad": 1.4, "rom_low": 1, "rom_high": 2, "blended_rate": 64, "onsite_rate": 120, "offshore_rate": 50,
               "onsite_share": 0.2, "total_hours": 1, "dev_days": 1, "weeks_requirements": 2, "weeks_build": 1, "weeks_uat": 1,
               "weeks_golive": 2, "weeks_total": 6, "milestones": [[0.5, 1]]}
    req = {"id": "R", "customer": "C", "requester_id": "U", "version": 1}
    blocks = ui.review_blocks(req, interp, results, ["r"], status)
    assert len(blocks) <= 50
    assert all(len(b.get("text", {}).get("text", "")) <= 3000 for b in blocks)
    if status == "in_review":
        assert blocks[-1]["type"] == "actions"
