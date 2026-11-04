# NS Estimator – Oracle AE Web Form: Claude Code Pack

Source plan: "NS Estimator – Oracle AE Web Form Plan" (Nov 2, 2026, Lirik Pre-Sales).

## Files
| File | Use |
|---|---|
| `CLAUDE.md` | Drop into the **root of the NS Estimator repo**. Claude Code loads it automatically; holds the non-negotiable rules. |
| `MASTER_PROMPT.md` | Paste into Claude Code (VS Code) as the first message. Runs 6 gated phases. |

## How to run
1. Open the existing NS Estimator repo in VS Code, start Claude Code on a new branch: `git checkout -b feature/oracle-ae-web-form`.
2. Copy `CLAUDE.md` to the repo root (merge with any existing CLAUDE.md, don't overwrite).
3. Start Claude Code in **plan mode** and paste `MASTER_PROMPT.md`.
4. Claude stops at the end of every phase. Review the diff, then reply `continue`.

## What stays with you (Claude Code must not do these)
- AWS deployment, IAM/SES/WAF/DNS changes: you run them with the generated runbook once AWS admin grants access.
- Real AE names/emails, real API keys, real customer data.

## Open decisions carried from the plan
Pilot AE list and Oracle data terms (Sales Director), go-ahead and AWS access (CTO), reviewers and turnaround (both), AI reading attachments (recommended: after pilot). Phase 0 gives you a config file with placeholders for these.
