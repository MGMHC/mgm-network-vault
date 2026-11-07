"""Dashboard, device list/edit/import/export, and device actions (backup, check, detect, live tools)."""
import csv
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from flask import Response, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import case, func, or_
from sqlalchemy.orm import selectinload

from . import engine, gitsync, lifecycle, reach
from .models import MODELS, PLATFORMS, ROLES, Backup, Credential, Device, Job, LogEntry, MacSighting, Schedule, Site, db, now, log_event
from .scheduler import next_run
from .web import BOM, authorized_device_ids, bp, current_user, form_bool, form_ids, group_sort_key, require_write, selected_site_id, site_filter


@bp.route("/")
def dashboard():
    q_dev = site_filter(Device.query, Device)
    devices = q_dev.order_by(Device.role.desc(), Device.group, Device.name).all()
    dev_ids = [d.id for d in devices]

    since = now() - timedelta(hours=24)
    q_b_since = site_filter(Backup.query.filter(Backup.created_at >= since).join(Device), Device)

    counts = {
        "total": len(devices),
        "up": sum(d.status == "up" for d in devices),
        "down": sum(d.status == "down" for d in devices),
        "sshdown": sum(d.status == "ssh-down" for d in devices),
        "vc_degraded": sum(d.vc_degraded for d in devices),
        "backup_failed": sum(d.last_backup_status == "failed" for d in devices if d.enabled),
        "stale": sum(1 for d in devices if d.enabled and (not d.last_backup_at or d.last_backup_at < now() - timedelta(days=7))),
        "changed_24h": q_b_since.filter(Backup.changed.is_(True)).count(),
        "backups_24h": q_b_since.count(),
    }

    from collections import defaultdict
    floor_map = defaultdict(list)
    for d in devices:
        floor_map[d.group or "(No Group)"].append(d)
    floors = sorted(floor_map.items(), key=lambda item: group_sort_key(item[0]))

    chart_days = 14
    first_day = (now() - timedelta(days=chart_days - 1)).replace(hour=0, minute=0, second=0)
    day_col = func.date(Backup.created_at)
    per_day_q = site_filter(
        db.session.query(day_col, func.count(Backup.id),
                         func.sum(case((Backup.changed.is_(True), 1), else_=0)))
        .join(Device, Backup.device_id == Device.id)
        .filter(Backup.created_at >= first_day)
        .group_by(day_col), Device)
    per_day = {d: (n, ch or 0) for d, n, ch in per_day_q.all()}
    day_labels = []
    daily_backups = []
    daily_changes = []
    for i in range(chart_days):
        day = first_day + timedelta(days=i)
        n, ch = per_day.get(day.strftime("%Y-%m-%d"), (0, 0))
        day_labels.append(day.strftime("%d %b"))
        daily_backups.append(n)
        daily_changes.append(int(ch))

    reach_data = {
        "up": counts["up"],
        "ssh_only": counts["sshdown"],
        "down": counts["down"],
        "unknown": counts["total"] - counts["up"] - counts["sshdown"] - counts["down"],
    }

    q_recent = site_filter(Backup.query.filter(Backup.changed.is_(True)).join(Device), Device)
    recent_changes = q_recent.order_by(Backup.created_at.desc()).limit(8).all()

    site_id = selected_site_id()
    u = current_user()
    q_logs = LogEntry.query
    if site_id or (u and not u.is_group_admin):
        q_logs = q_logs.filter(or_(LogEntry.device_id.in_(dev_ids), LogEntry.device_id.is_(None)))
    logs = q_logs.order_by(LogEntry.id.desc()).limit(12).all()

    q_sched = site_filter(Schedule.query.filter_by(enabled=True), Schedule)
    schedules = [(s, next_run(s.id)) for s in q_sched.all()]
    schedules.sort(key=lambda x: (x[1] is None, x[1].timestamp() if x[1] else 0))

    jobs = Job.query.order_by(Job.id.desc()).limit(5).all()
    lifecycle_analysis = lifecycle.analyze_device_lifecycle(devices)

    return render_template("dashboard.html", devices=devices, c=counts, recent_changes=recent_changes,
                           logs=logs, schedules=schedules[:5], jobs=jobs,
                           floors=floors, day_labels=day_labels,
                           daily_backups=daily_backups, daily_changes=daily_changes,
                           reach_data=reach_data, lifecycle=lifecycle_analysis)


@bp.route("/devices")
def devices():
    q = site_filter(Device.query, Device)
    f = {k: request.args.get(k, "") for k in ("q", "platform", "group", "role", "status")}
    if f["q"]:
        like = f"%{f['q']}%"
        q = q.filter(or_(Device.name.ilike(like), Device.host.ilike(like), Device.location.ilike(like)))
    for k in ("platform", "group", "role", "status"):
        if f[k]:
            q = q.filter(getattr(Device, k) == f[k])
    
    g_q = site_filter(db.session.query(Device.group).distinct(), Device)
    groups = sorted((g for (g,) in g_q if g), key=group_sort_key)
    q = q.options(selectinload(Device.site), selectinload(Device.credential))
    dev_list = sorted(q.all(), key=lambda d: (group_sort_key(d.group), d.name))
    backup_counts = {}
    if dev_list:
        backup_counts = dict(db.session.query(Backup.device_id, func.count(Backup.id))
                             .filter(Backup.device_id.in_([d.id for d in dev_list]))
                             .group_by(Backup.device_id).all())
    return render_template("devices.html", devices=dev_list, f=f, groups=groups,
                           PLATFORMS=PLATFORMS, ROLES=ROLES, backup_counts=backup_counts)


@bp.route("/devices/new", methods=["GET", "POST"])
@bp.route("/devices/<int:dev_id>/edit", methods=["GET", "POST"])
@require_write
def device_edit(dev_id=None):
    d = db.session.get(Device, dev_id) if dev_id else Device()
    if dev_id and not d:
        abort(404)
    u = current_user()
    if dev_id and not u.has_site_access(d.site_id):
        abort(403, "Cannot edit devices belonging to another site.")
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        clash = Device.query.filter(Device.name == name, Device.id != (d.id or 0)).first()
        if not name or not request.form.get("host", "").strip():
            flash("Name and IP / hostname are required", "error")
        elif clash:
            flash(f"Another device is already named {name}", "error")
        else:
            old_name = d.name
            d.name = name
            d.host = request.form["host"].strip()
            d.port = int(request.form.get("port") or 22)
            d.platform = request.form.get("platform", "juniper_junos")
            if d.platform not in PLATFORMS:
                abort(400, "Unknown platform.")
            d.model = request.form.get("model", "Auto")
            d.role = request.form.get("role", "access")
            d.group = request.form.get("group", "").strip()
            d.location = request.form.get("location", "").strip()
            d.is_vc = form_bool("is_vc")
            d.legacy_ssh = form_bool("legacy_ssh")
            d.enabled = form_bool("enabled")
            d.notes = request.form.get("notes", "")
            cred_raw = request.form.get("credential_id", "")
            if cred_raw.isdigit():
                cred = db.session.get(Credential, int(cred_raw))
                # A credential from a site the user can't access must never be bound to a host
                # they control - the switch login would hand them its password.
                if not cred:
                    abort(400, "Unknown credential profile.")
                if cred.id != d.credential_id and not u.has_site_access(cred.site_id):
                    abort(403, "That credential profile belongs to another site.")
                d.credential_id = cred.id
            else:
                d.credential_id = None

            site_id_val = request.form.get("site_id")
            if site_id_val and site_id_val.isdigit():
                candidate_sid = int(site_id_val)
                if u.has_site_access(candidate_sid):
                    d.site_id = candidate_sid
                else:
                    d.site_id = selected_site_id() or (u.site_id if u else None)
            else:
                d.site_id = selected_site_id() or (u.site_id if u else None)

            if not d.site_id:
                allowed = u.allowed_site_ids if u else []
                d.site_id = allowed[0] if allowed else (Site.query.order_by(Site.id).first().id if Site.query.first() else None)

            if not dev_id:
                db.session.add(d)
            db.session.commit()
            if dev_id and old_name != d.name:
                gitsync.rename_device(old_name, d.name)
            log_event(f"Device {'updated' if dev_id else 'added'}", "device", device=d)
            msg = f"Device {d.name} saved"
            if d.enabled and d.credential_id and (d.model == "Auto" or not d.hw_model):
                engine.start_discover_job(current_app._get_current_object(), [d.id], session.get("user"))
                msg += " - detecting model in the background"
            flash(msg, "ok")
            return redirect(url_for("web.device_detail", dev_id=d.id))
    if not dev_id:
        d.enabled, d.port, d.platform, d.model, d.role = True, 22, "juniper_junos", "Auto", "access"
        d.site_id = selected_site_id() or (u.site_id if u else None)
    
    g_q = site_filter(db.session.query(Device.group).distinct(), Device)
    groups = sorted((g for (g,) in g_q if g), key=group_sort_key)
    
    if d.site_id:
        cred_q = Credential.query.filter(Credential.site_id == d.site_id)
    else:
        cred_q = site_filter(Credential.query, Credential)
    creds = cred_q.order_by(Credential.name).all()

    return render_template("device_form.html", d=d, creds=creds,
                           PLATFORMS=PLATFORMS, MODELS=MODELS, ROLES=ROLES, groups=groups,
                           sites=Site.query.order_by(Site.code).all())


@bp.route("/devices/<int:dev_id>/delete", methods=["POST"])
@require_write
def device_delete(dev_id):
    d = db.get_or_404(Device, dev_id)
    u = current_user()
    if not u.has_site_access(d.site_id):
        abort(403, "Cannot delete devices belonging to another site.")
    for b in d.backups.all():
        engine.delete_backup(b, commit=False)
    log_event(f"Device '{d.name}' ({d.host}) deleted with its backups", "device", "WARN", commit=False)
    name = d.name
    MacSighting.query.filter_by(device_id=d.id).delete(synchronize_session=False)
    db.session.delete(d)
    db.session.commit()
    gitsync.remove_device(name)
    log_event(f"Device '{name}' deleted", "device")
    flash(f"Device '{name}' deleted", "ok")
    return redirect(url_for("web.devices"))


@bp.route("/devices/<int:dev_id>")
def device_detail(dev_id):
    d = db.get_or_404(Device, dev_id)
    u = current_user()
    if not u.has_site_access(d.site_id):
        abort(403, "Cannot view devices belonging to another site.")
    backups = d.backups.limit(200).all()
    return render_template("device_detail.html", d=d, backups=backups, PLATFORMS=PLATFORMS)


CSV_FIELDS = ["name", "host", "port", "platform", "role", "group", "location", "is_vc",
              "legacy_ssh", "enabled", "credential", "notes"]


@bp.route("/devices/export.csv")
def devices_export():
    q = site_filter(Device.query, Device)
    dev_list = sorted(q.all(), key=lambda d: (group_sort_key(d.group), d.name))
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(CSV_FIELDS)
    for d in dev_list:
        w.writerow([d.name, d.host, d.port, d.platform, d.role, d.group, d.location, int(d.is_vc),
                    int(d.legacy_ssh), int(d.enabled), d.credential.name if d.credential else "", d.notes])
    return Response(BOM + out.getvalue(), content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=devices.csv"})


@bp.route("/devices/template.csv")
def devices_template():
    rows = [CSV_FIELDS,
            ["CORE-EX3300-VC", "10.10.0.1", 22, "juniper_junos", "core", "Core", "Server room", 1, 1, 1, "juniper", ""],
            ["L1-EX2300-VC", "10.10.1.10", 22, "juniper_junos", "access", "Level-1", "IDF 1", 1, 0, 1, "juniper", ""],
            ["OPD-EX2200", "10.10.2.10", 22, "juniper_junos", "access", "OPD", "IDF 2", 0, 1, 1, "juniper", ""],
            ["ADM-C1300", "10.10.3.10", 22, "cisco_c1300", "access", "Admin", "IDF 3", 0, 0, 1, "cisco", ""]]
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return Response(BOM + out.getvalue(), content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=devices_template.csv"})


@bp.route("/devices/import", methods=["POST"])
@require_write
def devices_import():
    f = request.files.get("file")
    if f and request.content_length and request.content_length > 5 * 1024 * 1024:
        flash("CSV file is too large (max 5 MB)", "error")
        return redirect(url_for("web.devices"))
    if not f:
        flash("Choose a CSV file", "error")
        return redirect(url_for("web.devices"))
    me = current_user()
    site_id = selected_site_id()
    if not site_id:
        allowed = me.allowed_site_ids
        if allowed:
            site_id = allowed[0]
        else:
            first_s = Site.query.order_by(Site.id).first()
            site_id = first_s.id if first_s else None
    if not me.has_site_access(site_id):
        abort(403, "Cannot import devices into another site.")
    cred_q = Credential.query
    if site_id:
        cred_q = cred_q.filter(Credential.site_id == site_id)
    creds = {c.name.lower(): c.id for c in cred_q.all()}
    added = updated = 0
    errors = []
    imported = []
    reader = csv.DictReader(io.StringIO(f.read().decode("utf-8-sig", errors="replace")))
    truthy = lambda v: str(v).strip().lower() in ("1", "yes", "true", "y")  # noqa: E731
    for i, row in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if not row.get("name") or not row.get("host"):
            errors.append(f"line {i}: name and host required")
            continue
        if row.get("platform") and row["platform"] not in PLATFORMS:
            errors.append(f"line {i}: unknown platform '{row['platform']}'")
            continue
        d = Device.query.filter_by(name=row["name"]).first()
        if d is not None and not me.has_site_access(d.site_id):
            errors.append(f"line {i}: device '{row['name']}' belongs to another site (skipped)")
            continue
        new = d is None
        d = d or Device(name=row["name"])
        d.site_id = d.site_id or site_id
        d.host = row["host"]
        d.port = int(row.get("port") or 22)
        d.platform = row.get("platform") or d.platform or "juniper_junos"
        if row.get("model"):
            d.model = row["model"]
        elif not d.model:
            d.model = "Auto"
        d.role = row.get("role") or d.role or "access"
        d.group = row.get("group", d.group or "")
        d.location = row.get("location", d.location or "")
        d.is_vc = truthy(row.get("is_vc", ""))
        d.legacy_ssh = truthy(row.get("legacy_ssh", ""))
        d.enabled = truthy(row.get("enabled", "1") or "1")
        d.notes = row.get("notes", d.notes or "")
        if row.get("credential"):
            if row["credential"].lower() in creds:
                d.credential_id = creds[row["credential"].lower()]
            else:
                errors.append(f"line {i}: credential profile '{row['credential']}' not found (device saved without it)")
        if new:
            db.session.add(d)
            added += 1
        else:
            updated += 1
        imported.append(d)
    db.session.commit()
    detect = [d.id for d in imported if d.enabled and d.credential_id and (d.model == "Auto" or not d.hw_model)]
    engine.start_discover_job(current_app._get_current_object(), detect, session.get("user"))
    log_event(f"CSV import: {added} added, {updated} updated, {len(errors)} issue(s)", "device",
              "WARN" if errors else "INFO", details="\n".join(errors) or None)
    flash(f"Imported: {added} added, {updated} updated" +
          (f". Detecting model of {len(detect)} switch(es) in the background - refresh in a minute." if detect else "") +
          (f" Issues: {'; '.join(errors[:5])}" if errors else ""),
          "error" if errors else "ok")
    return redirect(url_for("web.devices"))


@bp.route("/api/backup", methods=["POST"])
def api_backup():
    data = request.get_json(silent=True) or {}
    if data.get("all"):
        q = site_filter(Device.query.filter_by(enabled=True), Device)
        ids = [d.id for d in q.all()]
    else:
        ids = form_ids(data.get("ids", []))
    ids = authorized_device_ids(ids)
    if not ids:
        return jsonify(error="No devices selected"), 400
    log_event(f"Manual backup requested for {len(ids)} device(s)", "backup")
    job_id = engine.start_backup_job(current_app._get_current_object(), ids, trigger="manual", user=session.get("user"))
    return jsonify(job=job_id)


@bp.route("/api/jobs/<int:job_id>")
def api_job(job_id):
    j = db.get_or_404(Job, job_id)
    return jsonify(id=j.id, status=j.status, total=j.total, done=j.done, ok=j.ok, failed=j.failed,
                   unchanged=j.unchanged)


@bp.route("/api/check", methods=["POST"])
def api_check():
    data = request.get_json(silent=True) or {}
    if data.get("all"):
        q = site_filter(Device.query.filter_by(enabled=True), Device)
        ids = [d.id for d in q.all()]
    else:
        ids = form_ids(data.get("ids", []))
    ids = authorized_device_ids(ids)
    res = reach.check_devices(current_app._get_current_object(), ids, scheduled=False)
    return jsonify({str(k): {"status": s, "ping_ms": r} for k, (s, r) in res.items()})


@bp.route("/api/detect", methods=["POST"])
def api_detect():
    data = request.get_json(silent=True) or {}
    ids = authorized_device_ids(form_ids(data.get("ids", [])))
    if not ids:
        return jsonify(error="No devices selected"), 400
    user = session.get("user")
    app = current_app._get_current_object()

    def one(dev_id):
        with app.app_context():
            return engine.discover_device(dev_id, user)

    # waits for the results so the page can refresh with them; one short SSH session per switch
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(zip(map(str, ids), pool.map(one, ids)))
    return jsonify(results)


@bp.route("/api/devices/<int:dev_id>/tool", methods=["POST"])
def api_tool(dev_id):
    d = db.get_or_404(Device, dev_id)
    u = current_user()
    if not u.has_site_access(d.site_id):
        abort(403, "Cannot run tools on devices belonging to another site.")
    data = request.get_json(silent=True) or {}
    kind = data.get("kind")
    try:
        title, text = engine.live_command(d, kind, data.get("a"), data.get("b"))
        log_event(f"Live tool '{kind}' run", "device", device=d)
        return jsonify(title=title, text=text)
    except Exception as e:  # noqa: BLE001
        log_event(f"Live tool '{kind}' failed: {e}", "device", "ERROR", device=d)
        return jsonify(error=f"{type(e).__name__}: {e}"), 502
