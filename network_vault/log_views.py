"""Event log pages."""
import csv
import io
from datetime import datetime, timedelta
from flask import Response, flash, redirect, render_template, request, url_for
from sqlalchemy import or_

from .models import Device, LogEntry, db, now, log_event
from .web import BOM, bp, current_user, require_group_admin, selected_site_id, site_filter


def _log_query():
    q = LogEntry.query
    u = current_user()
    sid = selected_site_id()
    if sid:
        dev_ids = [d.id for d in Device.query.filter(Device.site_id == sid).all()]
        q = q.filter(or_(LogEntry.device_id.in_(dev_ids), LogEntry.device_id.is_(None)))
    elif u and not u.is_group_admin:
        allowed = u.allowed_site_ids or []
        dev_ids = [d.id for d in Device.query.filter(Device.site_id.in_(allowed)).all()]
        q = q.filter(or_(LogEntry.device_id.in_(dev_ids), LogEntry.device_id.is_(None)))

    f = {k: request.args.get(k, "") for k in ("level", "category", "device", "q", "since", "until")}
    if f["level"]:
        q = q.filter(LogEntry.level == f["level"])
    if f["category"]:
        q = q.filter(LogEntry.category == f["category"])
    if f["device"]:
        q = q.filter(LogEntry.device_id == int(f["device"]))
    if f["q"]:
        like = f"%{f['q']}%"
        q = q.filter(or_(LogEntry.message.ilike(like), LogEntry.details.ilike(like), LogEntry.device_name.ilike(like)))
    if f["since"]:
        q = q.filter(LogEntry.ts >= datetime.fromisoformat(f["since"]))
    if f["until"]:
        q = q.filter(LogEntry.ts < datetime.fromisoformat(f["until"]) + timedelta(days=1))
    return q.order_by(LogEntry.id.desc()), f


@bp.route("/logs")
def logs():
    q, f = _log_query()
    page = max(1, int(request.args.get("page", 1)))
    p = q.paginate(page=page, per_page=100, error_out=False)
    dev_q = site_filter(Device.query, Device)
    return render_template("logs.html", p=p, f=f, devices=dev_q.order_by(Device.name).all(),
                           categories=["backup", "reachability", "alert", "schedule", "device", "git", "auth", "system"])


@bp.route("/logs/export.csv")
def logs_export():
    q, _ = _log_query()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["time", "level", "category", "device", "user", "message", "details"])
    for l in q.limit(100000):
        w.writerow([l.ts, l.level, l.category, l.device_name or "", l.user or "", l.message, l.details or ""])
    return Response(BOM + out.getvalue(), content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=mgm-network-vault_logs_{now():%Y%m%d}.csv"})


@bp.route("/logs/purge", methods=["POST"])
@require_group_admin
def logs_purge():
    days = int(request.form.get("days") or 0)
    n = LogEntry.query.filter(LogEntry.ts < now() - timedelta(days=days)).delete()
    db.session.commit()
    log_event(f"Purged {n} log entries older than {days} day(s)", "system", "WARN")
    flash(f"Purged {n} log entries", "ok")
    return redirect(url_for("web.logs"))
