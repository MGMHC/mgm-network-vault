"""Alert email wording and layout.

Admins edit a message's subject, opening paragraph and closing line on the Alerts page using
{field} placeholders. Everything else - the table of switches, diffs, buttons, the MGM header and
footer - is generated here, so an edit can never break the email.
"""
import re
from datetime import timedelta
from html import escape
from pathlib import Path

from .models import get_setting, now

LOGO_PATH = Path(__file__).parent / "static" / "mgm-logo.png"
LOGO_CID = "mgmlogo"

TRIGGERS = {
    "switch_down": "Switch down / recovered",
    "backup_failed": "Backup failed",
    "config_changed": "Config changed",
}

DEFAULTS = {
    "switch_down": {
        "subject": "[Network Vault] {count} {switches} DOWN - {first_switch}",
        "intro": "The following {switches} stopped answering ping and SSH for {checks} checks in a row.",
        "outro": "",
        "recovery_subject": "[Network Vault] {count} {switches} recovered - {first_switch}",
        "recovery_intro": "The following {switches} answered again.",
    },
    "backup_failed": {
        "subject": "[Network Vault] Backup failed on {count} {switches} - {first_switch}",
        "intro": "The {trigger} backup failed for the following {switches}. Each switch is reported once "
                 "until its backup succeeds again.",
        "outro": "",
    },
    "config_changed": {
        "subject": "[Network Vault] Configuration changed on {count} {switches} - {first_switch}",
        "intro": "The {trigger} backup found configuration changes on the following {switches}.",
        "outro": "",
    },
}

_COMMON = ["count", "switches", "first_switch", "first_ip", "site", "date", "time", "recipient_name"]
FIELDS = {
    "switch_down": _COMMON + ["checks", "down_count", "recovered_count"],
    "backup_failed": _COMMON + ["trigger"],
    "config_changed": _COMMON + ["trigger", "committed_by"],
}

KIND_TITLES = {"down": "Switch down", "up": "Switch recovered", "failed": "Backup failed",
               "changed": "Configuration changed"}

_FIELD_RE = re.compile(r"\{\{|\}\}|\{(\w+)\}")
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def fill(text, fields, html):
    """Replace {field} placeholders. Unknown fields stay as typed; {{ and }} are literal braces."""
    def sub(m):
        tok = m.group(0)
        if tok == "{{":
            return "{"
        if tok == "}}":
            return "}"
        name = m.group(1)
        if name not in fields:
            return escape(tok) if html else tok
        val = "" if fields[name] is None else str(fields[name])
        return escape(val) if html else val
    if not html:
        return _FIELD_RE.sub(sub, text or "")
    # escape the literal text between placeholders too
    out, pos = [], 0
    for m in _FIELD_RE.finditer(text or ""):
        out.append(escape((text or "")[pos:m.start()]))
        out.append(sub(m))
        pos = m.end()
    out.append(escape((text or "")[pos:]))
    return "".join(out)


def wording(msg, kind):
    trig = "switch_down" if kind in ("down", "up") else ("backup_failed" if kind == "failed" else "config_changed")
    d = DEFAULTS[trig]
    g = lambda attr: (getattr(msg, attr, "") or "").strip()  # noqa: E731
    if kind == "up":
        return {"subject": g("recovery_subject") or d["recovery_subject"],
                "intro": g("recovery_intro") or d["recovery_intro"],
                "outro": g("outro") or d["outro"]}
    return {"subject": g("subject") or d["subject"], "intro": g("intro") or d["intro"],
            "outro": g("outro") or d["outro"]}


def style_from_settings():
    color = (get_setting("alert_style_color") or "").strip()
    return {
        "color": color if _HEX_RE.match(color) else "#25366C",
        "logo": get_setting("alert_style_logo") == "1",
        "footer": get_setting("alert_style_footer") or "",
    }


def _fields(trigger, kind, items, ctx):
    t = now()
    sites = {i.get("site") or "" for i in items}
    first = items[0] if items else {}
    f = {
        "count": len(items),
        "switches": "switch" if len(items) == 1 else "switches",
        "first_switch": first.get("name", ""),
        "first_ip": first.get("host", ""),
        "site": (first.get("site") or "-") if len(sites) <= 1 else "multiple sites",
        "date": t.strftime("%d %b %Y"),
        "time": t.strftime("%H:%M"),
        "recipient_name": ctx.get("recipient_name", ""),
        "checks": ctx.get("checks", ""),
        "down_count": ctx.get("down_count", len(items) if kind == "down" else 0),
        "recovered_count": ctx.get("recovered_count", len(items) if kind == "up" else 0),
        "trigger": ctx.get("trigger_label", ""),
        "committed_by": first.get("committed_by", ""),
    }
    return {k: f[k] for k in FIELDS[trigger]}


def _fmt_time(t):
    return t.strftime("%d %b %Y %H:%M") if t else "unknown"


def fmt_minutes(m):
    if m is None:
        return "unknown"
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h} h {m} min" if m else f"{h} h"
    d, h = divmod(h, 24)
    return f"{d} d {h} h" if h else f"{d} d"


def _err(e):
    e = (e or "").strip().splitlines()[0] if (e or "").strip() else "unknown error"
    return e[:200]


_TD = "padding:8px 10px;border-top:1px solid #e2e8f0;vertical-align:top"


def _button(url, label, color):
    return (f"<p style='margin:18px 0 0'><a href='{escape(url)}' style='display:inline-block;background:{color};"
            f"color:#fff;padding:9px 16px;border-radius:6px;text-decoration:none;font-weight:600'>{escape(label)}</a></p>")


def _table(items, last_head, last_col):
    head = ("<tr style='background:#f4f6fa;text-align:left'><th style='padding:8px 10px'>Switch</th>"
            "<th style='padding:8px 10px'>IP</th><th style='padding:8px 10px'>Site</th>"
            f"<th style='padding:8px 10px'>{escape(last_head)}</th></tr>")
    rows = "".join(
        f"<tr><td style='{_TD}'><b>{escape(i['name'])}</b></td>"
        f"<td style='{_TD};font-family:Consolas,monospace'>{escape(i['host'])}</td>"
        f"<td style='{_TD}'>{escape(i.get('site') or '-')}</td>"
        f"<td style='{_TD}'>{escape(last_col(i))}</td></tr>" for i in items)
    return ("<table role='presentation' cellpadding='0' cellspacing='0' width='100%' "
            "style='border-collapse:collapse;font-size:13px;border:1px solid #e2e8f0'>" + head + rows + "</table>")


def _content(kind, items, ctx, color):
    app_url = (ctx.get("app_url") or "").rstrip("/")
    if kind == "down":
        html = _table(items, "Down since", lambda i: _fmt_time(i.get("since")))
        text = [f"  - {i['name']}  {i['host']}  {i.get('site') or '-'}  down since {_fmt_time(i.get('since'))}" for i in items]
    elif kind == "up":
        html = _table(items, "Was down for", lambda i: fmt_minutes(i.get("minutes")))
        text = [f"  - {i['name']}  {i['host']}  {i.get('site') or '-'}  was down for {fmt_minutes(i.get('minutes'))}" for i in items]
    elif kind == "failed":
        html = _table(items, "Error", lambda i: _err(i.get("error")))
        text = [f"  - {i['name']}  {i['host']}  {i.get('site') or '-'}  {_err(i.get('error'))}" for i in items]
    else:  # changed
        blocks, text = [], []
        n = ctx.get("diff_lines", 40)
        for i in items:
            who = i.get("committed_by") or "unknown"
            via = f" via {i['via']}" if i.get("via") else ""
            meta = f"+{i.get('added', 0)} / −{i.get('removed', 0)} lines · committed by {who}{via} · {_fmt_time(i.get('when'))}"
            diff = (i.get("diff") or [])[:n] if n else []
            more = max(0, (i.get("diff_total") or 0) - len(diff)) if n else 0
            lines = "".join(
                f"<div style='color:{'#1e8449' if l.startswith('+') else '#c0392b' if l.startswith('-') else '#334155'}'>"
                f"{escape(l) or '&nbsp;'}</div>" for l in diff)
            block = (f"<div style='margin:16px 0 0;border:1px solid #e2e8f0;border-radius:6px'>"
                     f"<div style='padding:10px 12px;background:#f4f6fa'><b>{escape(i['name'])}</b> "
                     f"<span style='font-family:Consolas,monospace;color:#475569'>{escape(i['host'])}</span> "
                     f"<span style='color:#475569'>· {escape(i.get('site') or '-')}</span><br>"
                     f"<span style='font-size:12px;color:#475569'>{escape(meta)}</span></div>")
            if diff:
                block += (f"<div style='padding:8px 12px;font-family:Consolas,monospace;font-size:12px;"
                          f"white-space:pre-wrap;word-break:break-all'>{lines}</div>")
            if more:
                block += f"<div style='padding:0 12px 8px;font-size:12px;color:#64748b'>{more} more lines</div>"
            if app_url and i.get("prev_id") and i.get("backup_id"):
                block += ("<div style='padding:0 12px 12px'>"
                          + _button(f"{app_url}/compare?a={i['prev_id']}&b={i['backup_id']}", "View full comparison", color)
                          + "</div>")
            blocks.append(block + "</div>")
            text.append(f"  - {i['name']}  {i['host']}  {i.get('site') or '-'}  {meta}")
            text += [f"      {l}" for l in diff]
            if more:
                text.append(f"      ({more} more lines)")
            if app_url and i.get("prev_id") and i.get("backup_id"):
                text.append(f"      Full comparison: {app_url}/compare?a={i['prev_id']}&b={i['backup_id']}")
        return "".join(blocks), text
    if app_url:
        page = "/devices" if kind in ("down", "up") else "/backups"
        label = "Open Devices" if kind in ("down", "up") else "Open Backups"
        html += _button(app_url + page, label, color)
        text.append(f"\n{label}: {app_url}{page}")
    return html, text


def render(trigger, kind, msg, items, ctx):
    """Return (subject, html, text, inline_images) for one email."""
    style = ctx.get("style") or {"color": "#25366C", "logo": False, "footer": ""}
    color = style["color"] if _HEX_RE.match(style.get("color") or "") else "#25366C"
    fields = _fields(trigger, kind, items, ctx)
    w = wording(msg, kind)
    subject = " ".join(fill(w["subject"], fields, False).split())[:250] or KIND_TITLES[kind]
    intro_h = fill(w["intro"], fields, True).replace("\n", "<br>")
    outro_h = fill(w["outro"], fields, True).replace("\n", "<br>")
    body_h, body_t = _content(kind, items, ctx, color)
    images = {LOGO_CID: str(LOGO_PATH)} if style.get("logo") and LOGO_PATH.exists() else {}
    logo = (f"<img src='cid:{LOGO_CID}' alt='MGM Healthcare' height='34' "
            "style='display:block;height:34px;margin:0 0 10px;background:#fff;border-radius:4px;padding:3px 6px'>") if images else ""
    footer = escape(style.get("footer") or "")
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(subject)}</title></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#f4f6fa;margin:0;padding:24px 12px;color:#1b2547">
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="max-width:680px;margin:0 auto;background:#fff;border-radius:10px;border:1px solid #e2e8f0">
    <tr><td style="padding:18px 24px;background:{color};color:#fff;border-radius:10px 10px 0 0">{logo}<div style="font-size:17px;font-weight:600">{escape(KIND_TITLES[kind])}</div></td></tr>
    <tr><td style="padding:18px 24px 24px;font-size:14px;line-height:1.5">
      {f'<p style="margin:0 0 14px">{intro_h}</p>' if intro_h else ''}
      {body_h}
      {f'<p style="margin:18px 0 0">{outro_h}</p>' if outro_h else ''}
      {f'<p style="margin:22px 0 0;color:#64748b;font-size:12px">{footer}</p>' if footer else ''}
    </td></tr>
  </table>
</body></html>"""
    text_parts = [fill(w["intro"], fields, False)] if w["intro"] else []
    text_parts += ["", *body_t]
    if w["outro"]:
        text_parts += ["", fill(w["outro"], fields, False)]
    if style.get("footer"):
        text_parts += ["", "--", style["footer"]]
    return subject, html, "\n".join(text_parts).strip() + "\n", images


def sample_items(trigger, kind):
    """Fixed sample data for previews and test emails."""
    t = now()
    base = [
        {"id": 0, "name": "IDF-2-ACC-SW1", "host": "10.10.2.5", "site": "MGMHC", "site_id": None},
        {"id": 0, "name": "MALAR-B-4TH-FLOOR", "host": "10.20.4.2", "site": "MGM-Malar", "site_id": None},
    ]
    if kind == "down":
        return [dict(b, since=t - timedelta(minutes=10), minutes=10) for b in base]
    if kind == "up":
        return [dict(b, since=t - timedelta(minutes=47), minutes=47) for b in base]
    if kind == "failed":
        return [dict(b, error="NetmikoTimeoutException: TCP connection to device failed.") for b in base]
    return [dict(base[0], added=3, removed=1, committed_by="mgm-admin", via="cli", when=t, prev_id=None, backup_id=None,
                 diff=["-   description \"old uplink\";", "+   description \"Uplink to CORE-1\";",
                       "+   vlan members [ 120 130 ];", "+   mtu 9216;"], diff_total=4)]

