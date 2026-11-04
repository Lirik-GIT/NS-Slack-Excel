"""Web-form intake: poll the SQS queue the confirmed submissions land in and feed them to the existing pipeline.

The EC2 host never accepts inbound connections; it only long-polls SQS and downloads attachments from S3.
A message is deleted only once its request row exists, so a crash or an invalid message is redelivered, and after
the queue's maxReceiveCount (3) it moves to the dead-letter queue for manual handling.
"""
import json
import logging
import re
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from .questions import ADDON, ADDON_OPTIONS, AREA_OPTIONS, NEW

log = logging.getLogger("ns_estimator.intake")

EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@oracle\.com$", re.IGNORECASE)
CURRENCIES = ("USD", "CAD")
SOURCE = "web"
VISIBILITY_TIMEOUT = 300  # seconds; covers attachment downloads before the row is written


def mask_email(address):
    """a***@oracle.com: addresses never appear in clear in logs."""
    local, _, domain = str(address).partition("@")
    return f"{local[:1]}***@{domain}"


def requester_id(address):
    return f"email:{address.lower()}"


def address_of(req):
    return req["requester_id"].split(":", 1)[1] if req["requester_id"].startswith("email:") else ""


class InvalidMessage(ValueError):
    pass


def _text(body, key, lo, hi, required=True):
    value = body.get(key)
    if value in (None, "") and not required:
        return ""
    if not isinstance(value, str) or not lo <= len(value.strip()) <= hi:
        raise InvalidMessage(f"{key} must be text of {lo}-{hi} characters")
    return value.strip()


def parse(body, countries, limits):
    """Validate a queue message (written by the form Lambda) and turn it into request fields. Raises InvalidMessage."""
    if not isinstance(body, dict):
        raise InvalidMessage("message is not an object")
    sid = _text(body, "submission_id", 8, 64)
    if not re.fullmatch(r"[A-Za-z0-9-]+", sid):
        raise InvalidMessage("submission_id has unexpected characters")
    email = _text(body, "email", 6, 254)
    if not EMAIL.match(email):
        raise InvalidMessage("email is not an @oracle.com address")
    deal = body.get("deal_type")
    if deal not in (NEW, ADDON):
        raise InvalidMessage("deal_type is not one of the two deal types")
    country = _text(body, "country", 2, 2).upper()
    currency = body.get("currency")
    if currency not in CURRENCIES:
        raise InvalidMessage("currency must be USD or CAD")
    if body.get("privacy_ack") is not True:
        raise InvalidMessage("privacy acknowledgement missing")
    form = {"Q01": deal}
    subs = body.get("subsidiaries")
    if subs not in (None, ""):
        if isinstance(subs, bool) or not isinstance(subs, int) or not 1 <= subs <= 500:
            raise InvalidMessage("subsidiaries must be a whole number from 1 to 500")
        form["Q02"] = subs
    for key, options in (("areas", AREA_OPTIONS), ("addons", ADDON_OPTIONS)):
        chosen = body.get(key) or []
        valid = {qid for qid, _ in options}
        if not isinstance(chosen, list) or any(c not in valid for c in chosen):
            raise InvalidMessage(f"{key} has an unknown option")
        form.update({c: "Yes" for c in chosen})
    attachments = body.get("attachments") or []
    if not isinstance(attachments, list) or len(attachments) > limits["max_files"]:
        raise InvalidMessage("too many attachments")
    for a in attachments:
        if not isinstance(a, dict) or not isinstance(a.get("key"), str) or not isinstance(a.get("name"), str) \
                or not isinstance(a.get("size"), int) or not 0 < a["size"] <= limits["max_mb_each"] * 1024 * 1024:
            raise InvalidMessage("attachment entry is invalid")
    return {
        "submission_id": sid,
        "email": email,
        "customer": _text(body, "customer", 1, 200),
        "opportunity": _text(body, "opportunity", 0, 400, required=False),
        "requirement": _text(body, "requirement", 20, 3000),
        "form": form,
        "meta": {"email": email.lower(), "country": country, "currency": currency,
                 "in_pilot_country": country in countries, "attachments": attachments},
    }


def safe_name(name):
    return re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).name)[:100] or "file"


class Intake:
    def __init__(self, bot, sqs, s3, queue_url, bucket, web_cfg, stop):
        self.bot, self.sqs, self.s3 = bot, sqs, s3
        self.queue_url, self.bucket = queue_url, bucket
        self.countries = web_cfg["countries"]
        self.limits = web_cfg["limits"]["attachments"]
        self.stop = stop

    def run(self):
        """Long-poll until `stop` is set. SQS errors are logged and retried; they never end the loop."""
        log.info("Web intake polling started")
        while not self.stop.is_set():
            try:
                self.poll_once()
            except Exception:
                log.exception("Web intake poll failed")
                self.stop.wait(5)
        log.info("Web intake stopped")

    def poll_once(self, wait=20):
        resp = self.sqs.receive_message(QueueUrl=self.queue_url, MaxNumberOfMessages=5, WaitTimeSeconds=wait,
                                        VisibilityTimeout=VISIBILITY_TIMEOUT)
        for msg in resp.get("Messages", []):
            if self.stop.is_set():
                break  # unprocessed messages become visible again after the visibility timeout
            self.handle(msg)

    def _done(self, msg):
        self.sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=msg["ReceiptHandle"])

    def handle(self, msg):
        try:
            data = parse(json.loads(msg["Body"]), self.countries, self.limits)
        except (InvalidMessage, ValueError) as exc:
            log.error("Invalid web message %s left on the queue for the DLQ: %s", msg.get("MessageId"), exc)
            return
        sid, who = data["submission_id"], mask_email(data["email"])
        store = self.bot.store
        if store.by_submission(sid):
            log.info("Duplicate submission %s from %s ignored", sid, who)
            self._done(msg)
            return
        try:
            rid = store.create(requester_id(data["email"]), data["email"], data["customer"], data["opportunity"],
                               data["requirement"], False, self.bot.catalog.template_sha256, self.bot.s.anthropic_model,
                               form=data["form"], source=SOURCE, submission_id=sid, meta=data["meta"])
        except IntegrityError:  # two copies of the message raced
            log.info("Duplicate submission %s from %s ignored", sid, who)
            self._done(msg)
            return
        self._done(msg)
        log.info("Web request %s created from submission %s (%s)", rid, sid, who)
        self._fetch_attachments(rid, data["meta"])
        self.bot.pool.submit(self.bot._run, self.bot.app.client, rid)

    def _fetch_attachments(self, rid, meta):
        """Attachments are for the reviewer only; a failed download is reported in the review thread, not fatal."""
        folder = self.bot.s.data_dir / "files" / rid / "attachments"
        saved = []
        for a in meta["attachments"]:
            path = folder / safe_name(a["name"])
            try:
                folder.mkdir(parents=True, exist_ok=True)
                self.s3.download_file(self.bucket, a["key"], str(path))
                saved.append({"name": path.name, "path": str(path)})
            except Exception:
                log.exception("Attachment download failed for request %s", rid)
                saved.append({"name": safe_name(a["name"]), "path": None})
        self.bot.store.update(rid, meta_json=json.dumps({**meta, "attachments": saved}))

    def resume(self):
        """After a restart, run web requests that were created but never reached review or failure."""
        for req in self.bot.store.by_source_status(SOURCE, ("received", "estimating")):
            log.info("Resuming web request %s", req["id"])
            self.bot.pool.submit(self.bot._run, self.bot.app.client, req["id"])


def make_clients():
    import boto3
    return boto3.client("sqs"), boto3.client("s3")
