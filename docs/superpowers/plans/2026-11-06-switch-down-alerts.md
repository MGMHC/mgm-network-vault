# Switch-Down Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Email admins when a switch has been down for 2 consecutive reachability checks, and again when it recovers.

**Architecture:** `reach.check_devices` counts consecutive `down` results per device. It collects the newly-down and recovered switches for each check and hands them to a new `alerts.py`. That module works out the recipients, builds one email per recipient, and sends the emails in a background thread through `mailer.send_email`.

**Tech Stack:** Flask, flask-sqlalchemy (SQLite), APScheduler, smtplib (existing `mailer.py`).

**Spec:** `docs/superpowers/specs/2026-11-06-switch-down-alerts-design.md`

## Global Constraints

- Only status `down` counts. `up` and `ssh-down` reset the counter.
- Settings: `alert_down_enabled` defaults to `"1"`, `alert_down_checks` defaults to `"2"` (minimum 1). Both are in `GROUP_ADMIN_SETTINGS`.
- Recipients:
  - `group-admin` users with an email get every switch.
  - `site-admin` users with an email get only switches whose `site_id` is in `allowed_site_ids`.
  - All other users get nothing.
- One email per recipient per check, with down and recovered switches combined.
- Sending never blocks `check_devices`; it runs in a daemon thread.
- No email and no per-check log entry when SMTP is disabled or alerts are off.
- No Claude attribution in commits. CHANGELOG gets an `## [Unreleased]` → **Added** entry. Don't bump `version.py`.

## Review Focus

1. A switch that's down when the app restarts must not get a second down email, because `down_alerted` is stored in the DB → covered by the Task 1 "no repeat" case, since the state lives in columns.
2. A device deleted or disabled while it's alerted gets no recovery email. That's accepted, so no test.
3. A site-admin with no assigned sites gets nothing, not everything → Task 1 test `site_admin_without_sites_gets_nothing`.
4. A device with `site_id = None` goes to group-admins only → Task 1 test.
5. An SMTP failure for one recipient doesn't stop the others → Task 1 test with a stub that fails for one address.

---

### Task 1: Alert state, recipients and email in the reachability check

**Files:**
- Modify: `network_vault/models.py` — on `Device`, add `down_checks = db.Column(db.Integer, default=0)` and `down_alerted = db.Column(db.Boolean, default=False)`. In `DEFAULT_SETTINGS`, add `"alert_down_enabled": "1"` and `"alert_down_checks": "2"`.
- Create: `network_vault/alerts.py`
- Modify: `network_vault/reach.py` — `check_devices`
- Test: `scratchpad/alerts_test.py`. This uses a fresh copy of the DB, monkeypatches `network_vault.reach.probe` to return scripted statuses, and stubs `network_vault.alerts.send_email` plus the thread start, so emails are captured synchronously.

**Interfaces:**
- Produces:
  - `alerts.collect(device, old_status, new_status, old_since) -> tuple[str, dict] | None`. Updates `device.down_checks` and `device.down_alerted`, and returns `("down", info)`, `("up", info)` or `None`. The `info` dict holds `id`, `name`, `host`, `site_id`, `site`, `since` and `minutes`.
  - `alerts.dispatch(app, down: list[dict], recovered: list[dict]) -> None`. Returns immediately when both lists are empty, alerts are off, or SMTP is disabled. Otherwise it starts a thread running `_send_all`.
  - `alerts._send_all(app, down, recovered)`.
  - `alerts.recipients_for(users, items) -> dict[str, list]`, mapping each email address to the items that recipient may see.

- [ ] **Step 1: Write the test script.** Cases (each starts from a clean state):
  - one `down` → 0 emails;
  - two `down` checks → each group-admin email gets 1 message, with "DOWN" in the subject;
  - a third `down` → 0 new emails;
  - then `up` → 1 recovery email per group-admin, with "recovered" in the subject and the duration in the body;
  - `down, ssh-down, down` → 0 emails;
  - a site-admin gets a switch in their site but not one outside it;
  - `site_admin_without_sites_gets_nothing`;
  - a device with `site_id=None` goes only to group-admins;
  - a user with an empty email is skipped;
  - `alert_down_enabled = "0"` → 0 emails, but the counters still update;
  - 12 devices down in the same 2 checks → exactly one email per recipient, listing all 12;
  - a stub that raises for one address still delivers to the others, and a WARN is logged.
- [ ] **Step 2: Run it.** Expect FAIL (`network_vault.alerts` missing).
- [ ] **Step 3: Implement** the model columns and settings, `alerts.py`, and the `reach.check_devices` changes. In `check_devices`, capture `old` and `d.status_since` before updating, call `collect` for every device, and call `dispatch` after `db.session.commit()`, outside the app context.
- [ ] **Step 4: Run it.** Expect ALL OK. Also start the app once on the DB copy and confirm the new columns were added (`PRAGMA table_info(device)`).
- [ ] **Step 5: Commit** `feat: email admins when a switch is down for consecutive checks`.

### Task 2: Settings UI

**Files:**
- Modify: `network_vault/views.py`:
  - Add `alert_down_enabled` and `alert_down_checks` to `GROUP_ADMIN_SETTINGS`.
  - In `settings()`, save `alert_down_enabled` as a checkbox (`"1"`/`"0"`) and clamp `alert_down_checks` to an int ≥ 1, falling back to `"2"` when the value is invalid.
- Modify: `network_vault/templates/settings.html`: inside the existing SMTP `<fieldset>`, add a "Switch-down alerts" row with a checkbox and a number input (min 1), plus help text: "Emails Group Administrators (all sites) and Site Administrators (their sites) when a switch fails this many reachability checks in a row, and again when it recovers."
- Test: `scratchpad/alerts_settings_test.py`

- [ ] **Step 1: Write the check.**
  - As `t_ga`: POST settings with the checkbox off and `alert_down_checks=3` → stored values are `"0"` and `"3"`.
  - Then with the checkbox on and `alert_down_checks=abc` → `"1"` and `"2"`.
  - As `t_sa`: a POST that tries to change them → unchanged.
  - `GET /settings` as `t_ga` contains `name="alert_down_enabled"`.
- [ ] **Step 2: Run it.** Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run it, plus the Task 1 script.** Expect ALL OK.
- [ ] **Step 5: Commit** `feat: switch-down alert settings`, including the CHANGELOG entry and the spec and plan.
