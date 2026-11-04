"""Write lambda/form/options.json from app/questions.py so the web form offers exactly the options the bot understands.

Run after changing the question lists:  python tools/build_form_options.py
tests/test_form_lambda.py fails if the file is out of date.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.questions import ADDON, ADDON_OPTIONS, AREA_OPTIONS, NEW  # noqa: E402


def options():
    return {"deal_types": [NEW, ADDON], "areas": [list(o) for o in AREA_OPTIONS], "addons": [list(o) for o in ADDON_OPTIONS]}


if __name__ == "__main__":
    (ROOT / "lambda" / "form" / "options.json").write_text(json.dumps(options(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
