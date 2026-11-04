# NS Estimator – issues for the developer (23 Oct 2026)

These came from testing the version dropped on 23 Oct 2026. It was compared with the 22 Oct deployment package, run on the local Socket Mode bot, and checked with the live acceptance suite in `acceptance/`.

**Status:** the new version is running locally with fixes 1 and 2 applied. All 4 acceptance groups pass (`acceptance/results.json`), and so do the 18 tests for the new code. The open items are 3, 5 and 6.

## 1. Claude call breaks on `claude-sonnet-5-5`: re-applied, keep it

[app/interpreter.py:236-243](app/interpreter.py#L236-L243) brought back `temperature=0` and a forced `tool_choice`. With anthropic 2.1.0 and Sonnet 5.5:

- `temperature` raises `TypeError: Messages.create() got an unexpected keyword argument 'temperature'`
- `tool_choice={"type": "tool", ...}` returns a 400, because forced tool choice isn't supported on this model

Every estimate failed with this. The fix is now in the file. Please keep it in your source copy so the next drop doesn't revert it:

```python
resp = self._client.messages.create(
    model=self._model,
    max_tokens=12000,
    system=self._system,
    tools=[TOOL],
    # current models reject temperature and forced tool_choice; the system prompt asks for submit_scope
    tool_choice={"type": "auto"},
    messages=[...],
)
```

The test assertion is updated too: [tests/test_reading.py:63](tests/test_reading.py#L63) now expects `{"type": "auto"}` and no `temperature`.

## 2. The manifest lost the Messages tab: re-applied, keep it

[slack_manifest.yml:6](slack_manifest.yml#L6). Reviewers upload the revised `.xlsx` in a DM with the bot, and Slack blocks that unless the Messages tab is on:

```yaml
features:
  app_home:
    home_tab_enabled: false
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false
```

## 3. Stale staffing code: open

`staffing` was removed from [config/policy.json](config/policy.json), and nothing in `app/` imports [app/staffing.py](app/staffing.py) any more. That file and [tests/test_staffing.py](tests/test_staffing.py) are leftovers from the old version, and all 5 staffing tests fail with `KeyError: 'staffing'`.

**Action:** delete both files if staffing is retired, or restore the policy block if it isn't.

## 4. A reviewer's upload didn't re-price: fixed in this version (for reference)

In the old version, uploading v2 kept the v1 Step 3 grid. Northwind v2 needed 14.58 person-weeks but was priced at 14.03. In the new version, v2 re-prices correctly: Northwind went from $40,480 at v1 to $41,888 at v2. No action needed.

## 5. Thin requirements aren't flagged: open, suggested change

The TSK request (`42E2A0F0DA`) had the requirement "Net new implementation - Japan". It produced a confident ROM built from form ticks and defaults, and the only review reason was "Every estimate is reviewed". Suggested addition to [app/review.py](app/review.py), in `review_reasons()`:

```python
from .resolve import REQUIREMENT

stated = sum(1 for a in scope.answers.values() if a.get("source") == REQUIREMENT)
if len(scope.sentences) < 3 or stated == 0:
    reasons.append("Requirement is too thin: the price rests on form ticks and default answers")
```

Add a test with a one-sentence requirement and form-only answers that expects this reason.

## 6. `DATA_DIR` isn't created outside Docker: open, minor

If the directory doesn't exist, SQLite fails at startup with `sqlite3.OperationalError: unable to open database file`. The Dockerfile creates `/srv/data`, so only local and non-Docker runs hit this. Suggested fix: at the top of `EstimatorBot.__init__` in [app/slack_app.py](app/slack_app.py), before `Store(...)` at line 37:

```python
settings.data_dir.mkdir(parents=True, exist_ok=True)
```

## How to verify

```bash
pip install -r requirements-dev.txt
pytest -q tests/test_reading.py tests/test_question_bank.py   # fast, no LibreOffice
pytest -q                                                    # full suite, needs soffice on PATH
python acceptance/run_acceptance.py                          # live: posts UAT cards to the test channels, uses Claude credit
```

Notes:

- **Windows:** keep the virtualenv on a short path. Some `slack_sdk` files go over the 260-character `MAX_PATH` limit and fail to import.
- **Speed:** LibreOffice recalculation takes about 30 seconds per run on Windows, against about 1 second on Linux, so the full suite is slow there.
