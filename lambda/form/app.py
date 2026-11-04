"""Oracle AE web form: serves the page, validates, takes attachments, confirms the email address and queues the request.

Routes (API Gateway REST proxy): GET / and /resubmit, POST /upload-urls and /submit, GET /confirm.
Only a confirmed submission reaches SQS. Responses never say whether an address is approved, and errors carry no detail.
"""
import base64
import hashlib
import json
import os
import re
import time
import uuid
from functools import lru_cache
from html import escape
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

import tokens

HERE = Path(__file__).parent
OPTIONS = json.loads((HERE / "options.json").read_text(encoding="utf-8"))
PAGE = (HERE / "page.html").read_text(encoding="utf-8")
LOCAL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}$")
ALLOWED_TYPES = {"pdf": (b"%PDF-",), "docx": (b"PK\x03\x04",), "xlsx": (b"PK\x03\x04",)}
MAX_FILES, MAX_BYTES, MAX_BODY = 3, 10 * 1024 * 1024, 20_000
DAY = 86400
GENERIC_OK = {"ok": True, "message": "If your address is on the approved list, we have emailed you a confirmation link. It is valid for 24 hours."}


def env(name, default=""):
    return os.environ.get(name, default)


@lru_cache(maxsize=None)
def aws(service):
    return boto3.client(service)


def mask(address):
    local, _, domain = str(address).partition("@")
    return f"{local[:1]}***@{domain}"


def reference(submission_id):
    """The reference an AE sees in every email; app/pdf.py:reference must give the same value."""
    return submission_id[:8].upper()


def respond(status, body, nonce=None, ctype="application/json"):
    csp = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    if nonce:
        origin = re.match(r"https://[^/]+", env("CAPTCHA_SCRIPT_URL"))
        extra = f" {origin.group(0)} https://*.awswaf.com" if origin else ""
        csp = (f"default-src 'none'; script-src 'nonce-{nonce}'{extra}; style-src 'nonce-{nonce}'; img-src 'self' data: https://*.awswaf.com; "
               f"connect-src 'self' https://{env('BUCKET')}.s3.{env('AWS_REGION', 'us-east-1')}.amazonaws.com{extra}; "
               "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    return {"statusCode": status, "body": body if isinstance(body, str) else json.dumps(body),
            "headers": {"Content-Type": ctype, "Content-Security-Policy": csp, "X-Content-Type-Options": "nosniff",
                        "Referrer-Policy": "no-referrer", "Cache-Control": "no-store", "Strict-Transport-Security": "max-age=31536000"}}


def message_page(title, text, status=200):
    return respond(status, f"<!doctype html><meta charset=utf-8><title>{escape(title)}</title><body style='font:16px sans-serif;max-width:40em;"
                           f"margin:3em auto'><h1>{escape(title)}</h1><p>{escape(text)}</p>", ctype="text/html; charset=utf-8")


@lru_cache(maxsize=8)
def _secret(name, bucket):  # bucket = time slot, so values refresh every 5 minutes
    return aws("ssm").get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def secret(name):
    return _secret(name, int(time.time() // 300))


def approved(address):
    listed = re.split(r"[\s,;]+", secret(env("APPROVED_PARAM")).lower())
    return address.lower() in listed


def clean_text(value, limit, multiline=False):
    pattern = r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]" if multiline else r"[\x00-\x1f\x7f]"
    return re.sub(pattern, " ", str(value)).strip()[:limit]


def validate(body):
    """(fields, bad). `bad` lists the names of invalid fields only; nothing else is reported back."""
    bad, f = [], {}

    def text(key, lo, hi, multiline=False):
        v = body.get(key)
        v = clean_text(v, hi + 1, multiline) if isinstance(v, str) else ""
        f[key] = v
        if not lo <= len(v) <= hi:
            bad.append(key)

    text("customer", 1, 200)
    text("opportunity", 0, 400)
    text("requirement", 20, 3000, multiline=True)
    f["email"] = clean_text(body.get("email", ""), 254).lower()
    local, _, domain = f["email"].rpartition("@")
    if not LOCAL.match(local) or domain not in re.split(r"[\s,;]+", env("EMAIL_DOMAINS", "oracle.com").lower()):
        bad.append("email")
    f["deal_type"] = body.get("deal_type")
    if f["deal_type"] not in OPTIONS["deal_types"]:
        bad.append("deal_type")
    f["country"] = str(body.get("country", "")).upper()
    if not re.fullmatch(r"[A-Z]{2}", f["country"]):
        bad.append("country")
    f["currency"] = body.get("currency")
    if f["currency"] not in ("USD", "CAD"):
        bad.append("currency")
    f["privacy_ack"] = body.get("privacy_ack") is True
    if not f["privacy_ack"]:
        bad.append("privacy_ack")
    subs = body.get("subsidiaries")
    if subs in (None, ""):
        f["subsidiaries"] = None
    elif isinstance(subs, int) and not isinstance(subs, bool) and 1 <= subs <= 500:
        f["subsidiaries"] = subs
    else:
        f["subsidiaries"] = None
        bad.append("subsidiaries")
    for key in ("areas", "addons"):
        chosen, valid = body.get(key) or [], {o[0] for o in OPTIONS[key]}
        f[key] = chosen if isinstance(chosen, list) and all(c in valid for c in chosen) else []
        if f[key] != chosen:
            bad.append(key)
    sid = body.get("submission_id") or uuid.uuid4().hex
    f["submission_id"] = sid
    if not re.fullmatch(r"[0-9a-f]{32}", str(sid)):
        bad.append("submission_id")
    f["attachments"] = body.get("attachments") or []
    if not isinstance(f["attachments"], list) or len(f["attachments"]) > MAX_FILES:
        f["attachments"] = []
        bad.append("attachments")
    return f, bad


def count(key, limit, ttl):
    """Atomic counter; True while the count is within the limit."""
    n = aws("dynamodb").update_item(
        TableName=env("TABLE"), Key={"pk": {"S": key}}, UpdateExpression="ADD n :one SET #t = :ttl",
        ExpressionAttributeNames={"#t": "ttl"}, ExpressionAttributeValues={":one": {"N": "1"}, ":ttl": {"N": str(int(time.time()) + ttl)}},
        ReturnValues="UPDATED_NEW")["Attributes"]["n"]["N"]
    return int(n) <= limit


def send_mail(to, subject, text):
    aws("ses").send_email(Source=env("SENDER"), Destination={"ToAddresses": [to]}, ConfigurationSetName=env("CONFIG_SET"),
                          Message={"Subject": {"Data": clean_text(subject, 150)}, "Body": {"Text": {"Data": text}}})


def base_url(event):
    ctx = event["requestContext"]
    return env("FORM_BASE_URL") or f"https://{ctx['domainName']}/{ctx['stage']}"


def page(event, prefill=None, notice=""):
    nonce = base64.b64encode(os.urandom(12)).decode()
    config = json.dumps({"options": OPTIONS, "prefill": prefill or {}, "notice": notice,
                         "captcha": {"script": env("CAPTCHA_SCRIPT_URL"), "key": env("CAPTCHA_API_KEY")}}).replace("<", "\\u003c")
    tag = f'<script nonce="{nonce}" src="{escape(env("CAPTCHA_SCRIPT_URL"))}" defer></script>' if env("CAPTCHA_SCRIPT_URL") else ""
    html = PAGE.replace("{{NONCE}}", nonce).replace("{{CAPTCHA_TAG}}", tag).replace("{{CONFIG}}", config)
    return respond(200, html, nonce, "text/html; charset=utf-8")


def upload_urls(event, body):
    if not count(f"IP#{ip(event)}#{time.strftime('%Y%m%d%H')}", int(env("IP_HOURLY_LIMIT", "20")), 2 * 3600):
        return respond(429, {"error": "Too many requests. Try again later."})
    files = body.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        return respond(400, {"fields": ["attachments"]})
    sid, uploads = uuid.uuid4().hex, []
    for i, item in enumerate(files):
        name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(str(item.get("name", ""))).name)[:80]
        size = item.get("size")
        if name.rsplit(".", 1)[-1].lower() not in ALLOWED_TYPES or not isinstance(size, int) or not 0 < size <= MAX_BYTES:
            return respond(400, {"fields": ["attachments"]})
        key = f"pending/{sid}/{i}-{name}"
        post = aws("s3").generate_presigned_post(env("BUCKET"), key, Conditions=[["content-length-range", 1, MAX_BYTES]], ExpiresIn=600)
        uploads.append({"key": key, "name": name, "size": size, **post})
    return respond(200, {"submission_id": sid, "uploads": uploads})


def check_attachments(f):
    """Each file must sit under this submission's pending prefix, be within the size limit and start with the right magic bytes."""
    s3, out = aws("s3"), []
    for a in f["attachments"]:
        key = a.get("key", "") if isinstance(a, dict) else ""
        name = key.rsplit("/", 1)[-1].split("-", 1)[-1]
        ext = name.rsplit(".", 1)[-1].lower()
        if not key.startswith(f"pending/{f['submission_id']}/") or ext not in ALLOWED_TYPES:
            return None
        try:
            head = s3.head_object(Bucket=env("BUCKET"), Key=key)
            if not 0 < head["ContentLength"] <= MAX_BYTES:
                raise ValueError("size")
            first = s3.get_object(Bucket=env("BUCKET"), Key=key, Range="bytes=0-7")["Body"].read()
            if not any(first.startswith(m) for m in ALLOWED_TYPES[ext]):
                raise ValueError("type")
        except (ClientError, ValueError):
            s3.delete_object(Bucket=env("BUCKET"), Key=key)
            return None
        out.append({"key": key, "name": name, "size": head["ContentLength"]})
    return out


def submit(event, body):
    if len(event.get("body") or "") > MAX_BODY or not count(f"IP#{ip(event)}#{time.strftime('%Y%m%d%H')}", int(env("IP_HOURLY_LIMIT", "20")), 2 * 3600):
        return respond(429, {"error": "Too many requests. Try again later."})
    f, bad = validate(body)
    if bad:
        return respond(400, {"fields": bad})
    if not approved(f["email"]) or not count(f"AE#{f['email']}#{time.strftime('%Y%m%d')}", int(env("DAILY_LIMIT", "10")), 2 * DAY):
        print(json.dumps({"event": "not_sent", "email": mask(f["email"])}))  # same answer as success: no way to probe the list
        return respond(200, GENERIC_OK)
    attachments = check_attachments(f)
    if attachments is None:
        return respond(400, {"fields": ["attachments"]})
    f["attachments"] = attachments
    try:
        aws("dynamodb").put_item(TableName=env("TABLE"), ConditionExpression="attribute_not_exists(pk)", Item={
            "pk": {"S": f"SUB#{f['submission_id']}"}, "status": {"S": "pending"}, "data": {"S": json.dumps(f)},
            "ttl": {"N": str(int(time.time()) + DAY)}})
    except ClientError:
        return respond(200, GENERIC_OK)  # duplicate submission id
    token = tokens.sign(secret(env("HMAC_PARAM")), {"k": "confirm", "sid": f["submission_id"]}, DAY)
    send_mail(f["email"], "Confirm your NetSuite ROM request",
              f"Hello,\n\nPlease confirm your request for {clean_text(f['customer'], 100)} within 24 hours:\n"
              f"{base_url(event)}/confirm?t={token}\n\nIf you did not make this request, ignore this email.\n\nLirik Pre-Sales")
    print(json.dumps({"event": "confirmation_sent", "email": mask(f["email"]), "sid": f["submission_id"]}))
    return respond(200, GENERIC_OK)


def confirm(event, query):
    claims = tokens.verify(secret(env("HMAC_PARAM")), query.get("t", ""))
    if not claims or claims.get("k") != "confirm":
        return message_page("Link not valid", "This confirmation link is invalid or has expired. Please submit the form again.", 400)
    sid, ddb = claims["sid"], aws("dynamodb")
    item = ddb.get_item(TableName=env("TABLE"), Key={"pk": {"S": f"SUB#{sid}"}}).get("Item")
    if not item:
        return message_page("Link not valid", "This confirmation link is invalid or has expired. Please submit the form again.", 400)
    if item["status"]["S"] == "confirmed":
        return message_page("Already confirmed", f"Your request {reference(sid)} is already with our team.")
    f, s3 = json.loads(item["data"]["S"]), aws("s3")
    for a in f["attachments"]:
        dest = a["key"].replace("pending/", "attachments/", 1)
        s3.copy_object(Bucket=env("BUCKET"), Key=dest, CopySource={"Bucket": env("BUCKET"), "Key": a["key"]})
        s3.delete_object(Bucket=env("BUCKET"), Key=a["key"])
        a["key"] = dest
    # the bot de-duplicates on submission_id, so a message sent twice (two clicks at once) is harmless
    aws("sqs").send_message(QueueUrl=env("QUEUE_URL"), MessageBody=json.dumps({k: v for k, v in f.items()}))
    ddb.update_item(TableName=env("TABLE"), Key={"pk": {"S": f"SUB#{sid}"}}, UpdateExpression="SET #s = :c, #t = :ttl, #d = :d",
                    ExpressionAttributeNames={"#s": "status", "#t": "ttl", "#d": "data"},
                    ExpressionAttributeValues={":c": {"S": "confirmed"}, ":ttl": {"N": str(int(time.time()) + 30 * DAY)}, ":d": {"S": json.dumps(f)}})
    send_mail(f["email"], f"We received your NetSuite ROM request {reference(sid)}",
              f"Hello,\n\nThank you. We received your request for {clean_text(f['customer'], 100)}.\nReference: {reference(sid)}\n\n"
              "A Lirik presales reviewer will review it and reply by email, usually within 1 business day.\n\nLirik Pre-Sales")
    print(json.dumps({"event": "confirmed", "email": mask(f["email"]), "sid": sid}))
    return message_page("Request confirmed", f"Thank you. Your reference is {reference(sid)}. You will receive the estimate by email.")


def resubmit(event, query):
    claims = tokens.verify(secret(env("HMAC_PARAM")), query.get("t", ""))
    if not claims or claims.get("k") != "resubmit":
        return message_page("Link not valid", "This link is invalid or has expired. Please start a new request.", 400)
    item = aws("dynamodb").get_item(TableName=env("TABLE"), Key={"pk": {"S": f"SUB#{claims['sid']}"}}).get("Item")
    if not item:
        return page(event, notice="Your earlier answers are no longer stored. Please fill in the form again.")
    old = json.loads(item["data"]["S"])
    keep = ("customer", "opportunity", "deal_type", "subsidiaries", "areas", "addons", "requirement", "country", "currency", "email")
    return page(event, {k: old.get(k) for k in keep}, "Your earlier answers are filled in. Add the detail the reviewer asked for and submit again.")


def ip(event):
    """Hashed, so no raw address is stored in the counter table."""
    return hashlib.sha256(event["requestContext"]["identity"]["sourceIp"].encode()).hexdigest()[:32]


def handler(event, context=None):
    try:
        method, path = event["httpMethod"], event["path"].rstrip("/") or "/"
        query = event.get("queryStringParameters") or {}
        if method == "GET" and path == "/":
            return page(event)
        if method == "GET" and path == "/confirm":
            return confirm(event, query)
        if method == "GET" and path == "/resubmit":
            return resubmit(event, query)
        if method == "POST" and path in ("/submit", "/upload-urls"):
            try:
                body = json.loads(event.get("body") or "{}")
            except ValueError:
                body = None
            if not isinstance(body, dict):
                return respond(400, {"fields": []})
            return (submit if path == "/submit" else upload_urls)(event, body)
        return respond(404, {"error": "Not found"})
    except Exception:
        print(json.dumps({"event": "error"}), flush=True)
        import traceback
        traceback.print_exc()  # goes to CloudWatch only; the caller gets a generic answer
        return respond(500, {"error": "Something went wrong. Please try again later."})
