"""Email alerts: who hears about which switch, and sending.

Triggers (configured on the Alerts page):
- switch_down     - reach.check_devices: a switch failed `alert_down_checks` scheduled checks in a row
                    (no ping and no SSH), and again when it recovers.
- backup_failed   - engine.start_backup_job: a switch's backup failed (once per failure streak).
- config_changed  - engine.start_backup_job: a backup found a configuration change.

Each trigger has one or more AlertMessages (recipients + site limit + wording). For every address,
each switch goes out once per run, through the first enabled message (by id) that covers it, so
nobody gets duplicates and no switch is dropped. Sending runs in a background thread.
"""
import re
import threading

from .alert_render import TRIGGERS, render, style_from_settings
from .mailer import get_smtp_config, send_email
from .models import AlertMessage, Device, User, db, get_bool, get_int, now, log_event


TRIGGER_SETTING = {
    "switch_down": "alert_down_enabled",
    "backup_failed": "alert_backup_enabled",
    "config_changed": "alert_change_enabled",
}


def threshold():
    return max(1, get_int("alert_down_checks"))


def trigger_enabled(trigger):
    """True when this trigger is on and outbound email is on."""
    return get_bool(TRIGGER_SETTING[trigger]) and get_smtp_config()["enabled"]


def audience(msg, users):
    """[(address, recipient_name, allowed_site_ids or None for all sites)] for one message."""
    limit = set(msg.site_ids) or None
    out = {}

    def add(addr, name, allowed):
        addr = (addr or "").strip().lower()
        if not addr:
            return
        if addr in out:
            prev = out[addr][1]
            allowed = None if prev is None or allowed is None else prev | allowed
            out[addr] = (out[addr][0], allowed)
        else:
            out[addr] = (name, allowed)

    for u in users:
        if not (u.email or "").strip():
            continue
        if msg.to_group_admins and u.is_group_admin:
            add(u.email, u.username, limit)
        elif msg.to_site_admins and u.role == "site-admin":
            own = set(u.allowed_site_ids or [])
            add(u.email, u.username, own & limit if limit is not None else own)
    for e in msg.extra_list:
        add(e, e, limit)
    return [(a, n, s) for a, (n, s) in out.items()]


def _covers(allowed, site_id):
    return allowed is None or (site_id is not None and site_id in allowed)


def targets(trigger):
    """Enabled messages for a trigger with their audiences, ready for would_reach / plan_emails."""
    msgs = AlertMessage.query.filter_by(trigger=trigger, enabled=True).order_by(AlertMessage.id).all()
    users = User.query.all()
    return [(m, audience(m, users)) for m in msgs]


def would_reach(site_id, tgts):
    return any(_covers(allowed, site_id) for _m, aud in tgts for _a, _n, allowed in aud)


def plan_emails(items, tgts):
    """[{address, name, msg, items}] - each switch once per address, via the first message covering it."""
    seen, out = set(), {}
    for msg, aud in tgts:
        for addr, name, allowed in aud:
            for it in items:
                key = (addr, it.get("kind"), it["id"])
                if key in seen or not _covers(allowed, it.get("site_id")):
                    continue
                seen.add(key)
                out.setdefault((addr, msg.id), {"address": addr, "name": name, "msg": msg, "items": []})["items"].append(it)
    return list(out.values())


def parse_commit(last_commit):
    """('mgm-admin', 'cli') from '2026-11-05 10:54:02 IST by mgm-admin via cli commit synchronize' (Junos);
    ('mgmadmin', '') from '15:05:28 IST Sat Nov 7 2026 by mgmadmin' (Cisco IOS has no 'via')."""
    m = re.search(r"\bby (\S+)(?: via (\S+))?", last_commit or "")
    return (m.group(1), m.group(2) or "") if m else ("unknown", "")


def _item(device, **extra):
    """One switch in an alert email (the shape alert_render reads)."""
    return {"id": device.id, "name": device.name, "host": device.host, "site_id": device.site_id,
            "site": device.site.code if device.site else "", **extra}


def _info(device, since):
    minutes = int((now() - since).total_seconds() // 60) if since else None
    return _item(device, since=since, minutes=minutes)


class BackupRunAlerts:
    """Backup-failed and config-changed alerts of one backup job: collected per switch, sent when the job ends.
    Create it inside an app context, before the job's workers start."""

    def __init__(self, manual):
        self._lock = threading.Lock()
        self._items = []
        self._failed = targets("backup_failed") if trigger_enabled("backup_failed") else None
        self._changed = (targets("config_changed") if trigger_enabled("config_changed")
                         and (not manual or get_bool("alert_change_manual")) else None)

    def _add(self, tgts, device, **extra):
        if tgts is not None and would_reach(device.site_id, tgts):
            with self._lock:
                self._items.append(_item(device, **extra))

    def backup_failed(self, device, error):
        # reported once per failure streak; backup_fail_alerted is set by send(), when the email goes out
        if not device.backup_fail_alerted:
            self._add(self._failed, device, kind="failed", error=error)

    def config_changed(self, device, last_commit, **change):
        if self._changed is not None:
            who, via = parse_commit(last_commit)
            self._add(self._changed, device, kind="changed", committed_by=who, via=via, when=now(), **change)

    def send(self, app, label):
        failed = [e for e in self._items if e["kind"] == "failed"]
        if failed:
            # Mark switches as reported (once per failure streak) only now that the email goes out, and only
            # those someone will still hear about - so a restart mid-job or a settings change loses nothing.
            with app.app_context():
                if trigger_enabled("backup_failed"):
                    tg = targets("backup_failed")
                    failed = [e for e in failed if would_reach(e["site_id"], tg)]
                    for e in failed:
                        d = db.session.get(Device, e["id"])
                        if d:
                            d.backup_fail_alerted = True
                    db.session.commit()
                else:
                    failed = []
        for trig, kinds in (("backup_failed", {"failed": failed}),
                            ("config_changed", {"changed": [e for e in self._items if e["kind"] == "changed"]})):
            try:
                dispatch(app, trig, kinds, {"trigger_label": label})
            except Exception as e:  # noqa: BLE001 - an alert problem must never affect the backup run
                with app.app_context():
                    log_event(f"Could not send {trig} alert: {e}", "alert", "WARN", user="monitor")


def collect(device, old_status, new_status, old_since, limit=None, scheduled=True, sending=True):
    """Update the switch's down counters for one check result.

    Returns ("down", info) or ("up", info) when an email is due, otherwise None.
    - Only scheduled checks count toward the threshold: someone pressing "Check" on a red switch
      must not turn a short blip into an email. Any check can end an outage (recovery email).
    - `sending` is False when no email could reach anyone about this switch (trigger or email
      off, or no enabled message covers its site). It is then not marked as alerted, so nobody
      later gets a "recovered" email for an outage they never heard about, and if it is still
      down when that changes the next scheduled check reports it."""
    limit = limit or threshold()
    if new_status == "down":
        if not scheduled:
            return None
        device.down_checks = (device.down_checks or 0) + 1
        if device.down_checks >= limit and not device.down_alerted and sending:
            device.down_alerted = True
            return "down", _info(device, device.status_since)
        return None
    was_alerted = bool(device.down_alerted)
    device.down_checks = 0
    device.down_alerted = False
    if was_alerted:
        # old_since is when the switch went down (status_since before this check changed it)
        return "up", _info(device, old_since if old_status == "down" else None)
    return None


def _spawn(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True, name="alert-mail").start()


def dispatch(app, trigger, by_kind, ctx=None):
    """Send this run's alerts for one trigger in the background. by_kind: {"down": [...], "up": [...]} etc."""
    by_kind = {k: list(v) for k, v in (by_kind or {}).items() if v}
    if not by_kind:
        return
    with app.app_context():
        if trigger == "switch_down" and not get_bool("alert_down_recovery"):
            by_kind.pop("up", None)
        if not by_kind or not trigger_enabled(trigger):
            return
    _spawn(_send_all, app, trigger, by_kind, dict(ctx or {}))


def _send_all(app, trigger, by_kind, ctx):
    with app.app_context():
        tgts = targets(trigger)
        base = dict(ctx, style=style_from_settings(), app_url=get_smtp_config()["app_url"], checks=threshold(),
                    diff_lines=max(0, get_int("alert_change_diff_lines")))
        if trigger == "switch_down":
            base.setdefault("down_count", len(by_kind.get("down", [])))
            base.setdefault("recovered_count", len(by_kind.get("up", [])))
        for kind, items in by_kind.items():
            items = [dict(i, kind=kind) for i in items]
            per_msg = {}
            for e in plan_emails(items, tgts):
                try:
                    subject, html, text, images = render(trigger, kind, e["msg"], e["items"],
                                                         dict(base, recipient_name=e["name"]))
                    ok, err = send_email(e["address"], subject, html, text, inline_images=images)
                except Exception as ex:  # noqa: BLE001 - one bad recipient or email must not stop the rest
                    ok, err = False, str(ex)
                rec = per_msg.setdefault(e["msg"].id, {"name": e["msg"].name, "sent": 0, "names": set()})
                if ok:
                    rec["sent"] += 1
                    rec["names"].update(i["name"] for i in e["items"])
                else:
                    log_event(f"{TRIGGERS[trigger]} alert ({kind}) to {e['address']} failed: {err}", "alert", "WARN",
                              commit=False, user="monitor")
            for rec in per_msg.values():
                if rec["sent"]:
                    names = ", ".join(sorted(rec["names"]))[:300]
                    log_event(f"{TRIGGERS[trigger]} alert ({kind}) '{rec['name']}' emailed to {rec['sent']} "
                              f"recipient(s): {names}", "alert", "INFO", commit=False, user="monitor")
        db.session.commit()
