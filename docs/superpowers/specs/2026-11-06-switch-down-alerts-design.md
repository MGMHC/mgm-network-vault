# Switch-down email alerts — design

**Date:** 2026-11-06 · **Size:** small feature · **Approved by:** Enawba ("create it as you proposed")

## Why
Admins want to hear about a switch outage by email instead of noticing it on the dashboard. The reachability poller already runs every 5 minutes (`ping_interval_min`) and SMTP is configured, but nothing is sent.

## What the data says
Last 30 days: 635 "down" transitions across 30 switches; 518 (82%) were back by the next check, 77 lasted 6–15 min, 38 lasted 15–60 min, 2 over an hour. An email per transition would be ~20 a day, mostly blips.

## Goals / non-goals / anti-goals
- **Goal:** an email when a switch has been down for **2 consecutive checks** (~10 min at the default interval), and a recovery email when it comes back, with how long it was down.
- **Non-goal:** SMS/Teams/webhooks; per-user subscription preferences; flap suppression beyond the 2-check rule.
- **Anti-goal:** an email storm (one email per switch when a whole building drops); a slow reachability check or a hung UI because SMTP is slow; alerts leaking another site's switches to a site-admin.

## Approaches
1. **Hook into `reach.check_devices` with a per-device consecutive-down counter (chosen).** The status transition is already detected there; two new columns on `Device` survive restarts.
2. Separate scheduler job scanning `Device.status` / `status_since` — rejected: duplicates the poller's timing and can't count consecutive checks.
3. Derive alerts from the `log_entry` table — rejected: indirect, and log retention purges history.

## Design
- **What counts as down:** status `down` only (no ping and no SSH). `ssh-down` means the switch answers ping, so it's not an outage for alert purposes and resets the counter. Only `enabled` devices are polled, as today.
- **State on `Device`:** `down_checks` (int, default 0) — consecutive `down` results; `down_alerted` (bool, default false) — a down email went out for the current outage. Added automatically by `_add_missing_columns`.
- **Each check, per device:**
  - result `down` → `down_checks += 1`; when it reaches the threshold and `down_alerted` is false → add to this check's *down* list, set `down_alerted = true`.
  - any other result → if `down_alerted` → add to this check's *recovered* list with the outage duration (from `status_since` captured before the status flips); then `down_checks = 0`, `down_alerted = false`.
- **Threshold:** setting `alert_down_checks`, default `2`, minimum 1.
- **On/off:** setting `alert_down_enabled`, default `1`. Checkbox and threshold on the Settings page in the email section, Group Administrators only (added to `GROUP_ADMIN_SETTINGS`). When off, the counters still update but no email is sent. When SMTP is disabled, nothing is sent and nothing is logged per check.
- **Recipients:** every user with an email address whose role is `group-admin` (all switches) or `site-admin` (only switches whose `site_id` is in `allowed_site_ids`). Read-write / read-only users get nothing.
- **Grouping:** one email per recipient per check, listing that recipient's newly-down and recovered switches together. Subject e.g. `[Network Vault] 3 switches DOWN` / `[Network Vault] 1 switch recovered` / `[Network Vault] 2 switches DOWN, 1 recovered`.
- **Body:** HTML table (name, IP, site, down since / down for) in the same style as the password-reset email, plus a plain-text part, and a link to `app_url` + `/devices` when `app_url` is set.
- **Sending:** after the check's DB commit, in a background thread, so a slow SMTP server never delays the poller or a user's manual "Check" click. Each send result is logged in the `reachability` category (INFO on success with recipient count, WARN with the error on failure).

## Builder's calls
- Exact HTML/CSS of the email; module layout (a new `alerts.py` next to `mailer.py`).

## Verification
- Unit-level script against a copy of the DB with `send_email` stubbed: 1 down → no mail; 2 downs → one mail per recipient; 3rd down → no repeat; up → recovery mail with duration; ssh-down resets; site-admin sees only their site; a user without email is skipped; disabled setting → no mail; 12 switches down in one check → one email per recipient.
- Settings page saves both settings; a site-admin can't change them.
