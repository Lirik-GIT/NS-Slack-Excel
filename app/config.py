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


def load_settings():
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
    )


def load_policy(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_guide(path):
    text = Path(path).read_text(encoding="utf-8")
    if len(text.strip()) < 200:
        raise ValueError(f"{path} is empty or too short to be the estimator guide")
    return text
