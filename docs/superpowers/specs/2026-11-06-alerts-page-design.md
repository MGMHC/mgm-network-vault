# Alerts page — design

**Date:** 2026-11-06 · **Size:** project · **Decided with:** Enawba

## Why
Email alerts are becoming a core part of the app. Today the only alert (switch down) is configured inside Settings → "Email Notifications & Password Reset", which mixes mail-server plumbing with what people want to be told about. Enawba wants:
- a dedicated **Alerts** page for all email triggers, separate from the SMTP settings;
- two new triggers: **backup failed** and **config changed**;
- control over what the emails look like: **MGM's own style**, and **different wording for different people**.

## What the data says (last 30 days, DB copy)
- **Config changes:** 85 changed backups (≈3/day).
  - 41 came from `syslog:commit`, a backup triggered by the switch's own commit.
  - 35 from the nightly schedule, 9 manual.
  - Diffs average ~600 changed lines (max 3,218); 11 exceeded 200 lines.
  - Juniper backups record the last commit as e.g. `2026-11-05 10:54:02 IST by mgm-admin via cli commit synchronize`.
- **Backup failures:** 1 in 30 days. The nightly job backs up all 68 switches in one run.
- **Switch down:** about 117 outages longer than one check (from the previous design).

## Goals / non-goals / anti-goals
- **Goals:**
  - One Alerts page holding every trigger: switch down/recovered, backup failed, config changed.
  - Each trigger has one or more messages; each message has its own recipients and wording.
  - One shared MGM email style.
  - Preview, send-test and reset-to-default for every message.
  - A short history of alerts sent.
- **Non-goals:**
  - Ticketing/helpdesk integration (explicitly dropped: "I want it simple").
  - Teams, Slack, SMS or webhooks.
  - Raw HTML or template-code editing.
  - Per-user subscription preferences.
  - Quiet hours and maintenance windows.
- **Anti-goals:**
  - An editor that lets someone break the email (unclosed tags, broken loops).
  - Duplicate emails to the same person for the same event.
  - Alert spam: one email per switch when a building drops, or the same failing backup emailed every night.
  - Leaking another site's switches to a site-admin or to a site-limited message.
  - Any change to password-reset emails.

## Approaches (chosen by Enawba)
1. **One card per trigger**, single recipients and wording. Rejected: can't word emails differently for different people.
2. **One card per trigger, each with one or more messages — chosen.** As simple as option 1 by default; "Add message" when a different audience needs different wording.
3. **Free-form rules list.** Rejected: busiest page, easy to create duplicate emails.

**Wording editor (my recommendation, accepted with option 2):** plain fields, not HTML or code. The editable parts are the subject, an opening paragraph and a closing line, with clickable fields such as `{switch}`. The table of switches, the diff summary and the button are inserted automatically.

## The page
Admin menu → **Alerts** (next to Settings). Group Administrators only: the menu item is hidden from everyone else and the routes return 403.

```
Alerts
┌ Email style ─────────────────────────────────────────────────────────┐
│ Header colour [#25366C]  Show MGM logo [x]  Footer text [..........] │
│ Outbound email: ON (configured in Settings)          [Preview style] │
└──────────────────────────────────────────────────────────────────────┘
┌ Switch down / recovered ─────────────────────────────── [On] ────────┐
│ Alert after [2] failed checks in a row (checks every 5 min ≈ 10 min) │
│ Send recovery email [x]                                              │
│  ▸ Message: Network team    → Group admins, Site admins     [Edit]   │
│  [+ Add message]                                                     │
└──────────────────────────────────────────────────────────────────────┘
┌ Backup failed ───────────────────────────────────────── [On] ────────┐
│  ▸ Message: Network team    → Group admins, Site admins     [Edit]   │
│  [+ Add message]                                                     │
└──────────────────────────────────────────────────────────────────────┘
┌ Config changed ──────────────────────────────────────── [On] ────────┐
│ Include manual backups [x]   Diff lines shown in email [40]          │
│  ▸ Message: Network team    → Group admins, Site admins     [Edit]   │
│  [+ Add message]                                                     │
└──────────────────────────────────────────────────────────────────────┘
┌ Recent alerts (last 20) ─────────────────────────────────────────────┐
│ 06 Nov 14:05  Switch down  IDF-2-SW1  → 3 recipients  sent           │
└──────────────────────────────────────────────────────────────────────┘
```

**Message editor** (modal or inline panel):

```
Name      [Network team                  ]   Enabled [x]
Send to   [x] Group administrators (all sites)
          [x] Site administrators (their own sites)
          Extra addresses [noc@mgmhealthcare.in, ...]
          Limit to sites  [All sites ▾]   (applies to every recipient of this message)
Subject   [{count} switch(es) DOWN - {first_switch}        ]  fields: {count} {first_switch} {site} ...
Opening   [The following switches stopped responding...     ]
Closing   [— MGM IT Network Team                            ]
          [Preview]  [Send test to me]  [Reset to default]  [Save]  [Delete]
Preview → rendered email with sample data, exactly as sent
```

## Triggers

### Switch down / recovered (existing — moved)
- Behaviour unchanged from the 2026-11-06 switch-down design: counted only on scheduled checks; email after N consecutive `down` results; recovery email; grouped per check.
- The trigger settings move from Settings to this page:
  - on/off → `alert_down_enabled`
  - threshold → `alert_down_checks`
  - new "Send recovery email" → `alert_down_recovery`, default on
- **Each switch-down message has two wordings: Down and Recovered.** If one check has both down and recovered switches for a recipient, that recipient gets two emails (one down, one recovery), each with its own wording. This is rare, and it keeps the wording free of conditionals. *(Builder's call — Claude.)*
- The default message keeps today's content and recipients but takes the new shared look. Its subject changes to a field-based one, and the scratch tests' subject and HTML checks are rewritten to match. *(Claude's call.)*

### Backup failed (new)
- **Fires when:** a switch's backup fails in a backup job (schedule, `syslog:commit`, or manual).
- **Grouping:** one email per recipient per job run, listing every failed switch with its error (first line, max 200 characters).
- **No nightly repeats:** a switch is reported once per failure streak. New `Device.backup_fail_alerted` flag: set when reported, cleared on the next successful backup. No "backup working again" email.
- The same "only mark as alerted when an email can actually go out" rule as switch-down, made stricter for all triggers *(Claude's call)*:
  - "can go out" = the trigger is on, outbound email is on, **and** at least one enabled message would reach this switch: some recipient after the site limits and site-admin scoping.
  - Otherwise the switch isn't marked, so adding a message later still reports it on the next run.
  - A failed SMTP send still leaves the flag set; it is logged as WARN (unchanged behaviour).

### Config changed (new)
- **Fires when:** a backup is stored with `changed=True`. The first backup of a switch is a baseline, not a change.
- **Manual backups:** included by default; setting `alert_change_manual`, default on.
- **Grouping:** one email per recipient per job run. A `syslog:commit` job usually covers one switch.
- **Per switch in the email:**
  - name, IP, site, when;
  - `+added / −removed` lines;
  - committed by / via, parsed from `last_commit` with `by (\S+) via (\S+)`, or "unknown";
  - the first N diff lines (setting `alert_change_diff_lines`, default 40, 0 = none), coloured, in monospace;
  - "N more lines" if truncated;
  - a "View full comparison" button linking to `/compare?a=<prev>&b=<new>` when `app_url` is set.

## Messages
- **Stored in a new table `AlertMessage`:**
  - `id`, `trigger` (`switch_down` | `backup_failed` | `config_changed`), `name`, `enabled`
  - `to_group_admins`, `to_site_admins`, `extra_emails` (comma or newline separated; validated as addresses on save)
  - `site_ids` (JSON list; empty = all sites)
  - `subject`, `intro`, `outro`
  - `recovery_subject`, `recovery_intro` (switch_down only)
  - `created_at`, `updated_at`
- **Defaults:** on first start each trigger gets one message, "Network team", sending to group-admins and site-admins, with default wording. The switch-down default reproduces today's email.
- **Who gets what:**
  - Group-admins get every switch, filtered by the message's site limit.
  - Site-admins get switches in their own sites, also filtered by the message's site limit.
  - Extra addresses get every switch allowed by the site limit.
  - Users with an empty email are skipped.
- **No duplicates within a trigger, and no switch dropped** *(Claude's call)*:
  - For each address, each switch in the run is assigned to the first enabled message (ordered by id) whose recipients and site limit cover that switch for that address.
  - The address then gets one email per message that was assigned at least one switch. Usually that's one email; it's more only when messages with different site limits both target the same address.
  - A message that would give an address zero switches doesn't count for that address.

## Wording fields
- **Syntax:** `{field}`. Unknown fields are left as typed; braces can be escaped as `{{`/`}}`.
- **Implementation:** a simple safe substitution, not Jinja or `str.format` (no attribute access).
- **Escaping:** values are HTML-escaped in the HTML part; the subject is single-line, with newlines stripped.

| Field | switch_down | backup_failed | config_changed |
|---|---|---|---|
| `{count}` — switches in this email | ✓ | ✓ | ✓ |
| `{switches}` — "switch"/"switches" | ✓ | ✓ | ✓ |
| `{first_switch}`, `{first_ip}`, `{site}` (first switch's site, or "multiple sites") | ✓ | ✓ | ✓ |
| `{date}`, `{time}` | ✓ | ✓ | ✓ |
| `{checks}` (threshold), `{down_count}`, `{recovered_count}` | ✓ | | |
| `{trigger}` (nightly schedule / switch commit / manual) | | ✓ | ✓ |
| `{committed_by}` (first switch) | | | ✓ |
| `{recipient_name}` (username, or the address for extra recipients) | ✓ | ✓ | ✓ |

## Email style
- **Settings:**
  - `alert_style_color` (default `#25366C`)
  - `alert_style_logo` (default on)
  - `alert_style_footer` (default "Sent by MGM Network Vault. Manage alerts in Network Vault → Alerts.")
- **Logo:** `static/mgm-logo.png`, embedded as an inline (CID) image so it shows without the client fetching remote images. `mailer.send_email` gains optional inline images; behaviour for existing callers is unchanged.
- **Layout:** shared by all triggers — header bar in the colour with logo and title, opening paragraph, the generated content, closing line, footer. The layout is plain table-based HTML for Outlook, with a plain-text part.

## Sending and history
- All sending happens in a background thread after the triggering work commits, as switch-down does today.
  - **Backup triggers:** dispatched once per job, at the end of `start_backup_job`'s runner, from results collected during the job.
  - **Switch down:** from `check_devices`, as now.
- **Logging:** every send is logged in the Logs table under the new category `alert`:
  - INFO: trigger, message name, recipient count and switch names;
  - WARN: failures.
- The page's "Recent alerts" lists the last 20 `alert` log entries.
- The switch-down entries currently logged under `reachability` move to `alert`.
- **Send test:** renders the message with built-in sample data for its trigger and sends it only to the logged-in user's email (error if they have none). The subject is prefixed `[TEST]`.

## Migration
- `_add_missing_columns` creates the table and `Device.backup_fail_alerted`. A `run_once` step seeds the three default messages.
- `alert_down_enabled` and `alert_down_checks` keep their current values. The Switch-down section is removed from Settings and replaced by a one-line link: "Email alerts are configured on the Alerts page."
- The new settings keys are added to `DEFAULT_SETTINGS` and `GROUP_ADMIN_SETTINGS`.

## Builder's calls
- Modal vs inline editor; exact CSS (match the Liquid Glass style of the rest of the app).
- Exact default wording.
- Module layout. Suggested: `alerts.py` grows into a package or gains `alert_templates.py`, plus `alerts_views.py` for routes, following `ipam_views.py`.
- How the preview is served (server-rendered route returning the HTML into an iframe is fine).

## Verification
- **Scripted tests** against a DB copy with SMTP stubbed:
  - each trigger fires and groups per run;
  - backup failure is reported once per streak;
  - manual-backup toggle;
  - diff truncation and the "N more lines" text;
  - committed_by parsing;
  - site limits, site-admin scoping, extra addresses;
  - no duplicate to one address across messages;
  - disabled message or trigger sends nothing;
  - field substitution with HTML escaping and unknown fields;
  - a subject with a newline is flattened;
  - preview and test routes are group-admin only;
  - the default switch-down message still passes every existing switch-down test.
- **Browser check:**
  - the Alerts page loads with no JS errors;
  - editing a message, preview, save and reset round-trip;
  - Settings no longer shows the switch-down controls.
