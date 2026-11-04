import json
import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _ids(value):
    return {v.strip() for v in (value or "").split(",") if v.strip()}


@dataclass(frozen=True)
class Settings:
    slack_bot_token: str
    slack_app_token: str
    anthropic_api_key: str
    anthropic_model: str
    review_channel_id: str
    log_channel_id: str
    reviewer_user_ids: frozenset = field(default_factory=frozenset)
    allowed_requester_user_ids: frozenset = field(default_factory=frozenset)
    database_url: str = ""
    data_dir: Path = BASE_DIR / "data"
    template_path: Path = BASE_DIR / "templates" / "NetSuite_Project_Estimator_Master.xlsx"
    policy_path: Path = BASE_DIR / "config" / "policy.json"
    guide_path: Path = BASE_DIR / "config" / "estimator_guide.md"
    bank_path: Path = BASE_DIR / "config" / "question_bank.xlsx"
    soffice_path: str = "soffice"
    recalc_timeout: int = 120
    worker_threads: int = 4
    web_queue_url: str = ""  # empty = web intake off
    web_attachments_bucket: str = ""
    web_events_queue_url: str = ""  # SES bounce/complaint events
    web_hmac_key: str = field(default="", repr=False)  # signs the resubmit link; from SSM
    web_form_path: Path = BASE_DIR / "config" / "web_form.yaml"
    web_retry_delay: float = 10.0  # seconds between the 3 estimate attempts for web requests


WEB_FORM_DEFAULTS = {
    "countries": ["US", "CA"],
    "sla": {"reviewer_reminder_business_hours": 4,
            "business_hours": {"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "start": "09:00", "end": "17:00",
                               "timezone": "America/New_York"}},
    "limits": {"attachments": {"max_files": 3, "max_mb_each": 10}},
}


def load_web_form(path):
    """config/web_form.yaml over the defaults above; a missing file means defaults."""
    import yaml
    data = {}
    if Path(path).exists():
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

    def merge(base, over):
        return {k: merge(base[k], over[k]) if isinstance(base.get(k), dict) and isinstance(over.get(k), dict) else over.get(k, base.get(k))
                for k in {**base, **over}}
    return merge(WEB_FORM_DEFAULTS, data)


def load_ssm_into_env(prefix):
    """Copy SecureString parameters under `prefix` into os.environ (names already set win). Used when SSM_PREFIX is set."""
    import boto3
    ssm = boto3.client("ssm")
    for page in ssm.get_paginator("get_parameters_by_path").paginate(Path=prefix, WithDecryption=True):
        for p in page["Parameters"]:
            os.environ.setdefault(p["Name"].rsplit("/", 1)[-1], p["Value"])


def load_settings():
    if os.environ.get("SSM_PREFIX"):
        load_ssm_into_env(os.environ["SSM_PREFIX"])
    data_dir = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
    config_dir = Path(os.environ.get("CONFIG_DIR", BASE_DIR / "config"))
    return Settings(
        slack_bot_token=os.environ["SLACK_BOT_TOKEN"],
        slack_app_token=os.environ["SLACK_APP_TOKEN"],
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        review_channel_id=os.environ["REVIEW_CHANNEL_ID"],
        log_channel_id=os.environ["LOG_CHANNEL_ID"],
        reviewer_user_ids=frozenset(_ids(os.environ.get("REVIEWER_USER_IDS"))),
        allowed_requester_user_ids=frozenset(_ids(os.environ.get("ALLOWED_REQUESTER_USER_IDS"))),
        database_url=os.environ.get("DATABASE_URL", f"sqlite:///{data_dir / 'estimator.db'}"),
        data_dir=data_dir,
        template_path=Path(os.environ.get("TEMPLATE_PATH", BASE_DIR / "templates" / "NetSuite_Project_Estimator_Master.xlsx")),
        policy_path=config_dir / "policy.json",
        guide_path=config_dir / "estimator_guide.md",
        bank_path=config_dir / "question_bank.xlsx",
        soffice_path=os.environ.get("SOFFICE_PATH", "soffice"),
        recalc_timeout=int(os.environ.get("RECALC_TIMEOUT", "120")),
        worker_threads=int(os.environ.get("WORKER_THREADS", "4")),
        web_queue_url=os.environ.get("WEB_QUEUE_URL", ""),
        web_attachments_bucket=os.environ.get("WEB_ATTACHMENTS_BUCKET", ""),
        web_events_queue_url=os.environ.get("WEB_EVENTS_QUEUE_URL", ""),
        web_hmac_key=os.environ.get("WEB_HMAC_KEY", ""),
        web_form_path=config_dir / "web_form.yaml",
        web_retry_delay=float(os.environ.get("WEB_RETRY_DELAY", "10")),
    )


def load_policy(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_guide(path):
    text = Path(path).read_text(encoding="utf-8")
    if len(text.strip()) < 200:
        raise ValueError(f"{path} is empty or too short to be the estimator guide")
    return text
