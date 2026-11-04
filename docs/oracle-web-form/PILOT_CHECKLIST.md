# Pilot checklist – Oracle AE web form

Scope: US and Canada customers, USD or CAD, English only. Two AEs. A Lirik reviewer approves every estimate in Slack.

## A. Mail-delivery test with one AE (before launch)
Oracle's mail filters are the biggest unknown, so test delivery before any real request.
- [ ] SES production access granted; DKIM shows Successful; SPF and DMARC records published.
- [ ] Ask **one** AE (not the pilot customer's real deal) to submit a test request with a dummy customer name.
- [ ] The confirmation email arrives in the inbox (not spam or quarantine) within 5 minutes. Note how long it took.
- [ ] The link opens the page, and the "received" email follows.
- [ ] Approve in Slack. The PDF email arrives with the PDF attached and opens correctly (phone and desktop).
- [ ] Ask the AE to check the headers: DKIM pass, SPF pass, DMARC pass.
- [ ] If anything lands in spam: ask Oracle IT to allow-list `komaldeep@lirik.io`, try a custom MAIL FROM domain, then repeat.
- [ ] The PDF layout was only checked as extracted text during development. Open it and look at it: title, ROM range, sections, wrapping, the disclaimer. Fix layout before launch.

## B. Checks only a person can do
- [ ] CAPTCHA puzzle appears on submit in Chrome, Edge, Safari.
- [ ] Attachment upload works from a real browser (3 files, 10 MB each; a wrong type is refused).
- [ ] The PDF and every email contain no rates, hours, line items, reviewer notes, reasons for review or error text. Read one of each.
- [ ] A request from an address not on the approved list gets the normal success page and no email.
- [ ] The reviewer reminder posts after 4 business hours of waiting (set the clock or use an old test request).
- [ ] An AI failure (for example a wrong Anthropic key on a test host) gives the AE only the generic "reviewing manually" email.
- [ ] A bounce (send to a bounce-simulator address) posts in the Slack thread.
- [ ] Alarms fire to `ns-estimator-alerts` (stop the container for 30 minutes, then start it).

## C. Trial week with two AEs
Each day the reviewer notes in one shared sheet: requests received, hours from submit to approval, estimates returned, resubmits, failures, anything an AE said.
- [ ] Day 0: both AEs briefed. Tell them: reply within 1 business day, US and Canada only, no personal data or contract terms in the requirement text.
- [ ] Every request approved or returned by a reviewer before any email (no exceptions).
- [ ] End of week: AE interview, 10 minutes each.

## D. Go / no-go for a wider rollout
Go only if **all** are true at the end of the week:
| Criterion | Target |
|---|---|
| Email delivery | 100% of emails in the inbox; bounce rate under 2%; no complaints |
| Turnaround | at least 90% approved or returned within 1 business day |
| Leaks | zero internal figures or text found in any PDF or email (review every PDF sent) |
| Reliability | no request lost; DLQ empty; no alarm left unexplained |
| Estimate quality | reviewer changed the range on fewer than 1 in 3 estimates, or the changes are understood |
| AE feedback | both AEs would use it again |
| Cost | AI spend per estimate within the cap in COSTS.md |
| Abuse | no junk submissions; WAF and limits held |

No-go if any leak is found, a request was lost, or an email went out without approval: stop the form (RUNBOOK section 10), fix, repeat the trial.

## E. Before widening
- [ ] Add AEs by updating the SSM approved list (no deploy).
- [ ] Move DMARC from `p=none` to `p=quarantine`.
- [ ] Decide on other countries or languages (not in the pilot).
- [ ] Review monthly costs against COSTS.md.
