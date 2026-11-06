# Alerts Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A group-admin Alerts page with three triggers (switch down/recovered, backup failed, config changed). Each trigger has one or more messages with their own recipients, site limit and field-based wording, plus a shared MGM email style, preview, test send and history.

**Architecture:**
- `alert_render.py` turns a message plus a list of switch items into subject, HTML and text, using `{field}` substitution and one shared layout.
- `alerts.py` decides who gets which switches (`plan_emails`) and sends in a background thread (`dispatch`).
- `reach.py` and `engine.py` produce items.
- `alerts_views.py` serves the page.

**Tech Stack:** Flask, flask-sqlalchemy (SQLite), Jinja for the page only, smtplib via `mailer.py`.

**Spec:** `docs/superpowers/specs/2026-11-06-alerts-page-design.md`

## Global Constraints

- Group-admin only: page, all `/alerts*` routes, preview, test. Others get 403 and the menu item is hidden.
- Wording uses `{field}` substitution only (no Jinja/format). Unknown fields are left as typed. `{{`/`}}` are literal braces. Values are HTML-escaped in HTML. Subjects are single-line.
- Triggers: `switch_down`, `backup_failed`, `config_changed`. Kinds:
  - `down`/`up` for switch_down;
  - `failed` for backup_failed;
  - `changed` for config_changed.
- Settings keys (defaults):
  - `alert_down_enabled` "1", `alert_down_checks` "2", `alert_down_recovery` "1"
  - `alert_backup_enabled` "1"
  - `alert_change_enabled` "1", `alert_change_manual` "1", `alert_change_diff_lines` "40"
  - `alert_style_color` "#25366C", `alert_style_logo` "1", `alert_style_footer` "Sent by MGM Network Vault. Manage alerts in Network Vault → Alerts."

  All are in `DEFAULT_SETTINGS` and `GROUP_ADMIN_SETTINGS`.
- No duplicate and no dropped switch per address per trigger per run (spec "Messages").
- A switch is only marked alerted if the trigger is on, SMTP is on, and some enabled message reaches it.
- Sending happens in a daemon thread; logs go to category `alert` (INFO on send, WARN on failure).
- Password-reset email unchanged. No Claude attribution in commits. CHANGELOG under `## [Unreleased]`; no version bump.

## Review Focus

1. A site-admin must never receive another site's switch through any message → Task 2 test `site_admin_scoped_through_every_message`.
2. Editing a message to an empty subject or opening must still send a sane email → Task 1 test `empty_wording_falls_back_to_default`.
3. A device name with `<script>` or a newline must not inject into the HTML or the subject → Task 1 tests.
4. A 3,000-line diff must not produce a 3,000-line email → Task 3 test `diff_truncated`.
5. Deleting the last message of a trigger must stop sending without errors, and must not mark switches alerted → Task 2 test `no_messages_no_mark`.

Checks are scratchpad scripts (no repo test suite), run against fresh copies of `scratchpad/vt` with SMTP stubbed, like the switch-down tests.

---

### Task 1: Message storage, style and rendering

**Files:**
- Modify: `network_vault/models.py`:
  - add `class AlertMessage(db.Model)`; columns per spec "Messages", with `site_ids` as a Text JSON list;
  - add `Device.backup_fail_alerted` (Boolean, default False);
  - add the new settings keys.
- Modify: `network_vault/__init__.py`: `run_once("seed_alert_messages", …)` creates one enabled "Network team" message per trigger, with group-admins and site-admins on and the default wording.
- Modify: `network_vault/mailer.py`: `send_email(to_email, subject, html_body, text_body=None, inline_images=None)`. `inline_images` is a `{cid: file_path}` dict. When given, build `multipart/related` around the alternative part. Existing callers are unchanged.
- Create: `network_vault/alert_render.py`
- Test: `scratchpad/alert_render_test.py`

**Interfaces (produces):**
- `DEFAULTS: dict[str, dict]`: per trigger, the keys `subject`, `intro`, `outro`; switch_down also has `recovery_subject` and `recovery_intro`.
- `FIELDS: dict[str, list[str]]`: field names per trigger, per spec table, for the UI.
- `fill(text: str, fields: dict, html: bool) -> str`
- `wording(msg, kind) -> dict(subject, intro, outro)`: the message's text, falling back to `DEFAULTS` when blank. `kind == "up"` uses the recovery_* keys.
- `render(trigger, kind, msg, items, ctx) -> tuple[str, str, str, dict]`: subject, html, text, inline_images.
  - `ctx` holds `recipient_name`, `checks`, `trigger_label`, `app_url`, `diff_lines` and `style`. `style` is a dict with `color`, `logo` (bool) and `footer`.
  - Item dicts:
    - switch_down: `id, name, host, site, site_id, since, minutes`
    - backup_failed: adds `error`
    - config_changed: adds `added, removed, committed_by, via, diff (list[str]), diff_total, prev_id, backup_id, when`
- `style_from_settings() -> dict`
- `sample_items(trigger, kind) -> list[dict]`: fixed sample data for preview and test.

- [ ] **Step 1: Write the check.**
  - `fill("{count} {switches} DOWN - {first_switch}", …)` → `"2 switches DOWN - SW1"`.
  - Unknown `{nope}` is kept.
  - `{{x}}` → `{x}`.
  - A field value `<b>` becomes `&lt;b&gt;` when `html=True` and stays raw when `False`.
  - `empty_wording_falls_back_to_default`: a msg with empty subject and intro renders the default subject.
  - `render` with a switch name `"A\nB<script>"`:
    - the subject has no `\n` or `\r`;
    - the HTML contains `&lt;script&gt;` and not `<script>`.
  - Style colour `#123456` appears in the HTML.
  - Logo on → inline_images has one entry and the HTML has `cid:`; logo off → no image.
  - Footer text appears.
  - A config_changed item with `diff_total=120` and 40 lines shows "80 more lines" and a `/compare?a=…&b=…` link when `app_url` is set, and no link when it isn't.
  - `send_email` with inline_images builds a message whose MIME tree contains `multipart/related` and an `image/png` part. To test this, stub `smtplib.SMTP` to capture `send_message`.
  - After `create_app()` on a fresh DB copy:
    - three AlertMessage rows exist, one per trigger;
    - `PRAGMA table_info(device)` has `backup_fail_alerted`;
    - a second `create_app()` doesn't add more rows.
- [ ] **Step 2: Run it.** Expect FAIL (no `alert_render`).
- [ ] **Step 3: Implement** the model, seed, mailer change and `alert_render.py`. The layout is table-based and inline-styled for Outlook.
- [ ] **Step 4: Run it.** Expect ALL OK.
- [ ] **Step 5: Commit** `feat: alert messages, shared email style and field-based wording`.

### Task 2: Recipients, dispatch and switch-down on messages

**Files:**
- Modify: `network_vault/alerts.py`
- Modify: `network_vault/reach.py` (call sites only)
- Test: rewrite `scratchpad/alerts_test.py`, keeping every existing case and updating the subject and HTML assertions to the new default wording.

**Interfaces:**
- Consumes: `alert_render.render`, `AlertMessage`.
- Produces:
  - `trigger_enabled(trigger) -> bool`: the trigger's setting is on **and** SMTP is enabled.
  - `audience(msg, users) -> list[tuple[str, str, set|None]]`: (address lowercased, recipient_name, allowed_site_ids or None for all), after the message's site limit. Site-admins get the intersection of their sites with the limit. Empty emails are skipped.
  - `plan_emails(trigger, items, messages, users) -> list[dict(address, name, msg, items)]`:
    - each switch is assigned to the first enabled message (by id) that covers it for that address;
    - one entry per (address, message) with ≥1 item;
    - order is stable.
  - `would_reach(trigger, site_id, messages, users) -> bool`
  - `dispatch(app, trigger, by_kind: dict[str, list[dict]], ctx: dict | None = None) -> None`:
    - no-op if every list is empty or `trigger_enabled` is false;
    - otherwise spawns `_send_all`, which renders one email per (plan entry × kind) and logs to `alert`.
  - `collect(...)`: unchanged signature, except `sending: bool` now means "would_reach for this switch". `check_devices` computes it per device from preloaded messages and users.
  - `alert_down_recovery == "0"` → no up emails, but counters still reset.

- [ ] **Step 1: Rewrite and extend the check.** Keep all 27 existing cases, and add:
  - `site_admin_scoped_through_every_message`: a second message limited to all sites still doesn't give the Malar site-admin an HCH switch.
  - `extra_addresses_get_site_limited_switches`.
  - `same_address_two_messages_no_duplicate_no_drop`: msg1 = noc@ limited to Malar, msg2 = noc@ for all sites. With one Malar and one HCH switch down, noc@ gets 2 emails (msg1 with Malar, msg2 with HCH), and each switch appears once.
  - `disabled_message_sends_nothing`.
  - `no_messages_no_mark`: delete all switch_down messages → 2 downs → no mail, `down_alerted` stays False; add a message → next scheduled down → mail.
  - `mixed_check_two_emails`: one switch down and one recovered in the same check → that recipient gets 2 emails, one DOWN and one recovered.
  - `recovery_off`: with `alert_down_recovery=0`, the up email isn't sent and the counters still reset.
  - All alert sends are logged under category `alert`.
- [ ] **Step 2: Run it.** Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run it, plus `alerts_thread_test.py`** (update it to the new `dispatch` signature). Expect ALL OK.
- [ ] **Step 5: Commit** `feat: message-based recipients for switch-down alerts`.

### Task 3: Backup failed and config changed triggers

**Files:**
- Modify: `network_vault/engine.py`:
  - `backup_device(device_id, trigger="manual", user=None, events=None)` appends to `events` (a list):
    - `{"kind": "failed", item…}` on failure, if the switch's `backup_fail_alerted` is False and `would_reach` is true; it then sets `backup_fail_alerted=True`;
    - on success, sets `backup_fail_alerted=False`;
    - `{"kind": "changed", item…}` when a changed backup is stored (`prev` exists). Manual backups count only if `alert_change_manual == "1"`.
  - `start_backup_job` passes one shared list (guarded by a lock) and, after the runner finishes, calls:
    - `alerts.dispatch(app, "backup_failed", {"failed": …}, ctx)`
    - `alerts.dispatch(app, "config_changed", {"changed": …}, ctx)`

    `ctx` includes `trigger_label`: "nightly schedule" / "switch commit" / "manual", from the trigger prefix `schedule` / `syslog` / `manual`.
- Modify: `network_vault/alerts.py`: `parse_commit(last_commit) -> tuple[str, str]` returns (committed_by, via) from `by (\S+) via (\S+)`, or `("unknown", "")`.
- Test: `scratchpad/alerts_backup_test.py`. Monkeypatch `engine.collect` to return scripted configs, or raise for failures. Use a fresh DB copy.

- [ ] **Step 1: Write the check.**
  - A job with 2 failing switches → one email per recipient listing both, with errors truncated to 200 characters.
  - The next job with the same switches still failing → no email.
  - A successful backup, then a failure → email again.
  - A changed config → email with `+a / −r`, `committed_by` from a last_commit of `"2026-11-05 10:54:02 IST by mgm-admin via cli commit synchronize"` (→ mgm-admin, cli), the first N diff lines, and a compare link with the correct `a`/`b` ids.
  - `diff_truncated`: 3,000 changed lines with `alert_change_diff_lines=40` → ≤ 40 diff lines and "more lines" text.
  - `diff_lines=0` → no diff block.
  - A first backup (baseline) → no change email.
  - A manual change with `alert_change_manual=0` → no email; with `1` → email.
  - Trigger off → nothing, and `backup_fail_alerted` isn't set.
  - The ctx `trigger_label` for a `schedule:Nightly backup` job is "nightly schedule".
- [ ] **Step 2: Run it.** Expect FAIL.
- [ ] **Step 3: Implement.** The diff lines come from the same `difflib.unified_diff(n=0)` already computed in `backup_device`: keep the `+`/`-` lines and drop the `+++`/`---` headers.
- [ ] **Step 4: Run it, plus the Task 1 and Task 2 checks.** Expect ALL OK.
- [ ] **Step 5: Commit** `feat: backup failed and config changed alerts`.

### Task 4: The Alerts page

**Files:**
- Create: `network_vault/alerts_views.py` with `register_alert_views(bp, _current_user, require_group_admin)`, registered at the bottom of `views.py` like IPAM. Routes:
  - `GET /alerts`: the page.
  - `POST /alerts/style`: colour (must match `^#[0-9a-fA-F]{6}$`, else 400), logo checkbox, footer (max 300 characters).
  - `POST /alerts/trigger/<trigger>`: that trigger's on/off and its settings (checks clamped ≥1; diff lines clamped 0–500).
  - `POST /alerts/message` (create, with defaults) and `POST /alerts/message/<id>` (save):
    - fields per spec;
    - extra_emails validated with a simple `^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$`; invalid → flash an error and don't save;
    - site_ids limited to existing sites.
  - `POST /alerts/message/<id>/delete`, `POST /alerts/message/<id>/reset` (wording back to DEFAULTS).
  - `GET /alerts/message/<id>/preview?kind=…`: the HTML from `render` with `sample_items`, for an iframe.
  - `POST /alerts/message/<id>/test`: sends to the current user's email with `[TEST] ` prefixed to the subject. JSON result; errors if the user has no email or SMTP is off.

  All routes are decorated `@require_group_admin`; POSTs are CSRF-checked by the existing guard.
- Create: `network_vault/templates/alerts.html`:
  - the layout from the spec sketch;
  - the message editor as a modal, with clickable field chips that insert at the cursor;
  - the preview in an iframe;
  - Recent alerts showing the last 20 `LogEntry` rows with category `alert`.
- Modify: `network_vault/templates/base.html`:
  - "Alerts" link in the Admin dropdown, the subnav and the mobile nav, shown only when the user is a group-admin;
  - `web.alerts` added to the `is_admin` endpoint list.
- Modify: `network_vault/templates/settings.html`: remove the "Switch-down Alerts" block. Add one line, shown to group-admins only: "Email alerts are configured on the Alerts page", with a link.
- Modify: `network_vault/views.py`:
  - `settings()` no longer handles `alert_*` keys; the Alerts page owns them;
  - `logs()` categories list gains `"alert"`.
- Test: `scratchpad/alerts_page_test.py`, plus a browser check `scratchpad/alerts_page_browser.mjs` run with the existing CDP harness.

- [ ] **Step 1: Write the checks.**
  - As `t_ga`: `/alerts` returns 200 and shows 3 trigger cards and the seeded messages.
  - As `t_sa` and `t_rw`, every alerts route returns 403, and `/` doesn't contain `href="/alerts"`.
  - Style: an invalid colour → 400; a valid one is saved.
  - Trigger save clamps.
  - Message create → save (with an invalid extra email → not saved) → preview has the subject text → reset restores the default → delete.
  - Test send with a stubbed mailer → `[TEST]` subject to the GA's own email.
  - The Settings page has no `name="alert_down_enabled"` and does contain the link to `/alerts`.
  - `/logs` category filter includes `alert`.
  - Browser:
    - `/alerts` loads with no JS errors;
    - opening the editor, clicking a field chip inserts `{first_switch}`;
    - the preview iframe loads;
    - the modal closes.
- [ ] **Step 2: Run it.** Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run it, plus all earlier checks** (alert_render, alerts, alerts_thread, alerts_backup, nav_ui, nav_cache, modal_check). Expect ALL OK.
- [ ] **Step 5: Commit** `feat: Alerts page for email triggers, recipients and wording`, together with the CHANGELOG entry, the spec and the plan.
