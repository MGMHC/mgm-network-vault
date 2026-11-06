"""Switch-down email alerts.

The reachability poller (reach.check_devices) calls collect() for every switch it checks and
dispatch() once per check. A switch is reported after `alert_down_checks` consecutive "down"
results (no ping and no SSH) and again when it comes back. Group Administrators hear about every
switch, Site Administrators about switches in their own sites. Each recipient gets one email per
check, so a whole building going dark is one message, not one per switch.
"""
import threading
from html import escape

from .mailer import get_smtp_config, send_email
from .models import User, db, get_setting, now
from .util import log_event


def threshold():
    try:
        return max(1, int(get_setting("alert_down_checks") or 2))
    except (TypeError, ValueError):
        return 2


def _info(device, since):
    minutes = int((now() - since).total_seconds() // 60) if since else None
    return {
        "id": device.id,
        "name": device.name,
        "host": device.host,
        "site_id": device.site_id,
        "site": device.site.code if device.site else "",
        "since": since,
        "minutes": minutes,
    }


def collect(device, old_status, new_status, old_since, limit=None):
    """Update the switch's alert counters for one check result.

    Returns ("down", info) or ("up", info) when an email is due, otherwise None. Counters update
    even when alerts are switched off; dispatch() decides whether anything is sent."""
    limit = limit or threshold()
    if new_status == "down":
        device.down_checks = (device.down_checks or 0) + 1
        if device.down_checks >= limit and not device.down_alerted:
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


def recipients_for(users, items):
    """Map each email address to the alert items that user may see."""
    out = {}
    for u in users:
        email = (u.email or "").strip()
        if not email:
            continue
        if u.is_group_admin:
            mine = list(items)
        elif u.role == "site-admin":
            allowed = set(u.allowed_site_ids or [])
            mine = [i for i in items if i["site_id"] is not None and i["site_id"] in allowed]
        else:
            continue
        if mine:
            have = out.setdefault(email.lower(), [])
            seen = {(i["kind"], i["id"]) for i in have}
            have.extend(i for i in mine if (i["kind"], i["id"]) not in seen)
    return out


def _spawn(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True, name="switch-alert-mail").start()


def dispatch(app, down, recovered):
    """Send this check's alerts in the background so a slow mail server never delays the poller."""
    if not down and not recovered:
        return
    with app.app_context():
        if get_setting("alert_down_enabled") != "1" or not get_smtp_config()["enabled"]:
            return
    _spawn(_send_all, app, list(down), list(recovered))


def _fmt_minutes(m):
    if m is None:
        return "unknown"
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h} h {m} min" if m else f"{h} h"
    d, h = divmod(h, 24)
    return f"{d} d {h} h" if h else f"{d} d"


def _fmt_time(t):
    return t.strftime("%d %b %Y %H:%M") if t else "unknown"


def _plural(n):
    return f"{n} switch" if n == 1 else f"{n} switches"


def _subject(down, up):
    parts = []
    if down:
        parts.append(f"{_plural(len(down))} DOWN" + (f" - {down[0]['name']}" if len(down) == 1 and not up else ""))
    if up:
        parts.append(f"{_plural(len(up))} recovered" + (f" - {up[0]['name']}" if len(up) == 1 and not down else ""))
    return "[Network Vault] " + ", ".join(parts)


def _render(down, up, checks, app_url):
    def rows(items, last_col):
        return "".join(
            "<tr>"
            f"<td style='padding:8px 10px;border-top:1px solid #e2e8f0'><b>{escape(i['name'])}</b></td>"
            f"<td style='padding:8px 10px;border-top:1px solid #e2e8f0;font-family:Consolas,monospace'>{escape(i['host'])}</td>"
            f"<td style='padding:8px 10px;border-top:1px solid #e2e8f0'>{escape(i['site'] or '-')}</td>"
            f"<td style='padding:8px 10px;border-top:1px solid #e2e8f0'>{escape(last_col(i))}</td>"
            "</tr>" for i in items)

    def table(title, colour, items, last_head, last_col):
        return (f"<h3 style='margin:22px 0 8px;color:{colour};font-size:15px'>{escape(title)}</h3>"
                "<table role='presentation' cellpadding='0' cellspacing='0' width='100%' "
                "style='border-collapse:collapse;font-size:13px;border:1px solid #e2e8f0;border-radius:6px'>"
                "<tr style='background:#f4f6fa;text-align:left'>"
                "<th style='padding:8px 10px'>Switch</th><th style='padding:8px 10px'>IP</th>"
                f"<th style='padding:8px 10px'>Site</th><th style='padding:8px 10px'>{escape(last_head)}</th></tr>"
                f"{rows(items, last_col)}</table>")

    since = lambda i: _fmt_time(i["since"])  # noqa: E731
    lasted = lambda i: _fmt_minutes(i["minutes"])  # noqa: E731
    checks_txt = f"{checks} reachability check{'s' if checks != 1 else ''} in a row"
    parts, text = [], []
    if down:
        parts.append(table(f"Down - no ping and no SSH for {checks_txt}", "#c0392b", down, "Down since", since))
        text.append(f"DOWN (no ping and no SSH for {checks_txt}):")
        text += [f"  - {i['name']}  {i['host']}  {i['site'] or '-'}  down since {since(i)}" for i in down]
    if up:
        parts.append(table("Recovered", "#1e8449", up, "Was down for", lasted))
        text.append("RECOVERED:" if not down else "\nRECOVERED:")
        text += [f"  - {i['name']}  {i['host']}  {i['site'] or '-'}  was down for {lasted(i)}" for i in up]
    link_html = link_txt = ""
    if app_url:
        link_html = (f"<p style='margin:22px 0 0'><a href='{escape(app_url)}/devices' style='display:inline-block;"
                     "background:#25366C;color:#fff;padding:9px 16px;border-radius:6px;text-decoration:none;"
                     "font-weight:600'>Open Devices</a></p>")
        link_txt = f"\nOpen Devices: {app_url}/devices"
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Network Vault switch alert</title></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#f4f6fa;margin:0;padding:24px 12px;color:#1b2547">
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="max-width:640px;margin:0 auto;background:#fff;border-radius:10px;border:1px solid #e2e8f0">
    <tr><td style="padding:20px 24px;background:#25366C;color:#fff;border-radius:10px 10px 0 0;font-size:17px;font-weight:600">MGM Network Vault - switch alert</td></tr>
    <tr><td style="padding:4px 24px 24px">
      {''.join(parts)}
      {link_html}
      <p style="margin:22px 0 0;color:#64748b;font-size:12px">Sent by the reachability monitor. Turn these alerts off or change how many failed checks trigger them in Settings.</p>
    </td></tr>
  </table>
</body></html>"""
    return html, "\n".join(text) + "\n" + link_txt + "\n"


def _send_all(app, down, recovered):
    with app.app_context():
        items = [dict(i, kind="down") for i in down] + [dict(i, kind="up") for i in recovered]
        recips = recipients_for(User.query.all(), items)
        if not recips:
            return
        cfg = get_smtp_config()
        checks = threshold()
        sent, failed = [], []
        for email, mine in recips.items():
            d = [i for i in mine if i["kind"] == "down"]
            u = [i for i in mine if i["kind"] == "up"]
            html, text = _render(d, u, checks, cfg["app_url"])
            try:
                ok, err = send_email(email, _subject(d, u), html, text)
            except Exception as e:  # noqa: BLE001 - one bad recipient must not stop the rest
                ok, err = False, str(e)
            (sent if ok else failed).append((email, err))
        names = ", ".join(i["name"] for i in down + recovered)[:300]
        what = ", ".join(p for p in (f"{len(down)} down" if down else "", f"{len(recovered)} recovered" if recovered else "") if p)
        if sent:
            log_event(f"Switch alert emailed to {len(sent)} recipient(s) ({what}): {names}", "reachability",
                      "INFO", commit=False, user="monitor")
        for email, err in failed:
            log_event(f"Switch alert email to {email} failed: {err}", "reachability", "WARN",
                      commit=False, user="monitor")
        db.session.commit()
