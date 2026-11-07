"""Alerts page: email triggers, their messages (recipients + wording) and the shared email style."""
import re

from flask import abort, flash, jsonify, redirect, render_template, request, url_for

from .alert_render import DEFAULTS, FIELDS, HEX_RE, TRIGGERS, render, sample_items, style_from_settings
from .alerts import threshold
from .mailer import get_smtp_config, send_email
from .models import AlertMessage, LogEntry, Site, db, get_int, get_setting, set_setting, log_event


EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
KINDS = {"switch_down": ["down", "up"], "backup_failed": ["failed"], "config_changed": ["changed"]}
TEXT_FIELDS = ("subject", "intro", "outro", "recovery_subject", "recovery_intro")


def _clamp_int(raw, lo, hi, default):
    try:
        return str(min(hi, max(lo, int(raw))))
    except (TypeError, ValueError):
        return str(default)


def _wording_from_form(form):
    class W:  # message-like object for render()
        pass
    w = W()
    for f in TEXT_FIELDS:
        setattr(w, f, (form.get(f) or "")[:255 if f.endswith("subject") else 4000])
    return w


def _render_ctx(user):
    return {"recipient_name": user.username if user else "", "checks": threshold(), "trigger_label": "scheduled",
            "app_url": get_smtp_config()["app_url"], "diff_lines": get_int("alert_change_diff_lines"),
            "style": style_from_settings()}
from .web import bp, current_user, require_group_admin


@bp.route("/alerts")
@require_group_admin
def alerts():
    msgs = AlertMessage.query.order_by(AlertMessage.id).all()
    sites = Site.query.order_by(Site.code).all()
    recent = (LogEntry.query.filter_by(category="alert").order_by(LogEntry.ts.desc()).limit(20).all())
    s = {k: get_setting(k) for k in ("alert_down_enabled", "alert_down_checks", "alert_down_recovery",
                                      "alert_backup_enabled", "alert_change_enabled", "alert_change_manual",
                                      "alert_change_diff_lines", "alert_style_color", "alert_style_logo",
                                      "alert_style_footer", "ping_interval_min")}
    data = {m.id: {"id": m.id, "trigger": m.trigger, "name": m.name, "enabled": m.enabled,
                   "to_group_admins": m.to_group_admins, "to_site_admins": m.to_site_admins,
                   "extra_emails": m.extra_emails or "", "site_ids": m.site_ids,
                   **{f: getattr(m, f) or "" for f in TEXT_FIELDS}} for m in msgs}
    return render_template("alerts.html", msgs=msgs, sites=sites, recent=recent, s=s, triggers=TRIGGERS,
                           fields=FIELDS, defaults=DEFAULTS, kinds=KINDS, msg_data={str(k): v for k, v in data.items()},
                           site_codes={x.id: x.code for x in sites},
                           smtp_on=get_smtp_config()["enabled"], me=current_user())

@bp.route("/alerts/style", methods=["POST"])
@require_group_admin
def alerts_style():
    color = (request.form.get("alert_style_color") or "").strip()
    if not HEX_RE.match(color):
        abort(400, "Header colour must look like #25366C.")
    set_setting("alert_style_color", color)
    set_setting("alert_style_logo", "1" if request.form.get("alert_style_logo") == "1" else "0")
    set_setting("alert_style_footer", (request.form.get("alert_style_footer") or "").strip()[:300])
    db.session.commit()
    log_event("Alert email style updated", "alert")
    flash("Email style saved", "ok")
    return redirect(url_for("web.alerts"))

@bp.route("/alerts/trigger/<trigger>", methods=["POST"])
@require_group_admin
def alerts_trigger(trigger):
    f = request.form
    on = lambda k: "1" if f.get(k) == "1" else "0"  # noqa: E731
    if trigger == "switch_down":
        set_setting("alert_down_enabled", on("enabled"))
        set_setting("alert_down_checks", _clamp_int(f.get("checks"), 1, 100, 2))
        set_setting("alert_down_recovery", on("recovery"))
    elif trigger == "backup_failed":
        set_setting("alert_backup_enabled", on("enabled"))
    elif trigger == "config_changed":
        set_setting("alert_change_enabled", on("enabled"))
        set_setting("alert_change_manual", on("manual"))
        set_setting("alert_change_diff_lines", _clamp_int(f.get("diff_lines"), 0, 500, 40))
    else:
        abort(404)
    db.session.commit()
    log_event(f"Alert trigger '{TRIGGERS[trigger]}' settings updated", "alert")
    flash(f"{TRIGGERS[trigger]} settings saved", "ok")
    return redirect(url_for("web.alerts") + f"#trigger-{trigger}")

def _save_message(m, form):
    extras = [e.strip() for e in re.split(r"[,;\s]+", form.get("extra_emails") or "") if e.strip()]
    bad = [e for e in extras if not EMAIL_RE.match(e)]
    if bad:
        return f"Not a valid email address: {', '.join(bad[:3])}"
    name = (form.get("name") or "").strip()[:64]
    if not name:
        return "Give the message a name."
    valid_sites = {x.id for x in Site.query.all()}
    m.name = name
    m.enabled = form.get("enabled") == "1"
    m.to_group_admins = form.get("to_group_admins") == "1"
    m.to_site_admins = form.get("to_site_admins") == "1"
    m.extra_emails = ", ".join(extras)
    m.site_ids = [int(x) for x in form.getlist("site_ids") if x.isdigit() and int(x) in valid_sites]
    w = _wording_from_form(form)
    for fld in TEXT_FIELDS:
        if fld.startswith("recovery") and m.trigger != "switch_down":
            continue
        setattr(m, fld, getattr(w, fld))
    return None

@bp.route("/alerts/message", methods=["POST"])
@require_group_admin
def alerts_message_new():
    trigger = request.form.get("trigger")
    if trigger not in TRIGGERS:
        abort(400, "Unknown trigger")
    d = DEFAULTS[trigger]
    m = AlertMessage(trigger=trigger, name="New message", to_group_admins=False, to_site_admins=False,
                     subject=d["subject"], intro=d["intro"], outro=d["outro"],
                     recovery_subject=d.get("recovery_subject", ""), recovery_intro=d.get("recovery_intro", ""))
    err = _save_message(m, request.form) if request.form.get("name") else None
    if err:
        flash(err, "error")
        return redirect(url_for("web.alerts") + f"#trigger-{trigger}")
    db.session.add(m)
    db.session.commit()
    log_event(f"Alert message '{m.name}' added to {TRIGGERS[trigger]}", "alert")
    flash(f"Message '{m.name}' added - choose who receives it", "ok")
    return redirect(url_for("web.alerts") + f"#edit-{m.id}")

@bp.route("/alerts/message/<int:mid>", methods=["POST"])
@require_group_admin
def alerts_message_save(mid):
    m = db.get_or_404(AlertMessage, mid)
    err = _save_message(m, request.form)
    if err:
        db.session.rollback()
        flash(err, "error")
    else:
        db.session.commit()
        log_event(f"Alert message '{m.name}' ({TRIGGERS[m.trigger]}) saved", "alert")
        flash(f"Message '{m.name}' saved", "ok")
    return redirect(url_for("web.alerts") + f"#trigger-{m.trigger}")

@bp.route("/alerts/message/<int:mid>/delete", methods=["POST"])
@require_group_admin
def alerts_message_delete(mid):
    m = db.get_or_404(AlertMessage, mid)
    trigger, name = m.trigger, m.name
    db.session.delete(m)
    db.session.commit()
    log_event(f"Alert message '{name}' ({TRIGGERS[trigger]}) deleted", "alert", "WARN")
    flash(f"Message '{name}' deleted", "ok")
    return redirect(url_for("web.alerts") + f"#trigger-{trigger}")

@bp.route("/alerts/message/<int:mid>/reset", methods=["POST"])
@require_group_admin
def alerts_message_reset(mid):
    m = db.get_or_404(AlertMessage, mid)
    d = DEFAULTS[m.trigger]
    for f in TEXT_FIELDS:
        setattr(m, f, d.get(f, ""))
    db.session.commit()
    flash(f"Wording of '{m.name}' reset to the default", "ok")
    return redirect(url_for("web.alerts") + f"#trigger-{m.trigger}")

def _preview_args():
    trigger = request.form.get("trigger")
    kind = request.form.get("kind")
    if trigger not in TRIGGERS or kind not in KINDS[trigger]:
        abort(400, "Unknown trigger or kind")
    return trigger, kind, _wording_from_form(request.form)

@bp.route("/alerts/preview", methods=["POST"])
@require_group_admin
def alerts_preview():
    trigger, kind, w = _preview_args()
    subject, html, _text, images = render(trigger, kind, w, sample_items(trigger, kind),
                                          _render_ctx(current_user()))
    if images:  # the browser can't resolve cid: - show the real logo file instead
        html = html.replace("cid:mgmlogo", url_for("static", filename="mgm-logo.png"))
    return jsonify(subject=subject, html=html)

@bp.route("/alerts/test", methods=["POST"])
@require_group_admin
def alerts_test():
    trigger, kind, w = _preview_args()
    me = current_user()
    if not (me.email or "").strip():
        return jsonify(ok=False, error="Your account has no email address - add one under Users."), 400
    if not get_smtp_config()["enabled"]:
        return jsonify(ok=False, error="Outbound email is turned off in Settings."), 400
    subject, html, text, images = render(trigger, kind, w, sample_items(trigger, kind), _render_ctx(me))
    ok, err = send_email(me.email, "[TEST] " + subject, html, text, inline_images=images)
    log_event(f"Test alert ({TRIGGERS[trigger]}, {kind}) sent to {me.email}" if ok
              else f"Test alert to {me.email} failed: {err}", "alert", "INFO" if ok else "WARN")
    return jsonify(ok=ok, error=err, to=me.email)
