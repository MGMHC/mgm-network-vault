"""HTTP routes."""
import csv
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from flask import (Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from sqlalchemy import func, or_

from . import config, engine, gitsync, portable, reach
from .diffing import side_by_side, stats, unified
from .models import (DEFAULT_SETTINGS, MODELS, PLATFORMS, ROLES, USER_ROLES, Backup, Credential, Device, Job,
                     LogEntry, Schedule, User, db, get_setting, now, set_setting)
from .scheduler import make_trigger, next_run, resolve_targets, run_schedule, sync_schedules
from .util import BACKUP_DIR, BASE_DIR, encrypt, log_event, read_backup_file, safe_name
from .version import __version__

bp = Blueprint("web", __name__)
BOM = "\ufeff"  # lets Excel open our UTF-8 CSV files correctly


def _current_user():
    """Return the current User ORM object (or None)."""
    uname = session.get("user")
    return User.query.filter_by(username=uname).first() if uname else None


def require_write(f):
    """Decorator: 403 if user is read-only."""
    from functools import wraps
    @wraps(f)
    def wrapper(*a, **kw):
        u = _current_user()
        if u and not u.can_write:
            abort(403, "Your account is read-only. Contact an admin.")
        return f(*a, **kw)
    return wrapper


def require_admin(f):
    """Decorator: 403 unless user is admin."""
    from functools import wraps
    @wraps(f)
    def wrapper(*a, **kw):
        u = _current_user()
        if u and not u.is_admin:
            abort(403, "Admin access required.")
        return f(*a, **kw)
    return wrapper


@bp.app_template_filter("dt")
def fmt_dt(v):
    return v.strftime("%Y-%m-%d %H:%M:%S") if v else "—"


@bp.app_template_filter("ago")
def fmt_ago(v):
    if not v:
        return "never"
    if v.tzinfo:
        v = v.astimezone().replace(tzinfo=None)
    s = (now() - v).total_seconds()
    future = s < 0
    s = abs(s)
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            t = f"{int(s // n)}{unit}"
            return f"in {t}" if future else f"{t} ago"
    return "just now" if not future else "in <1m"


@bp.app_template_filter("gh_commit")
def gh_commit(sha):
    return gitsync.commit_url(sha) if gitsync.is_enabled() else None


@bp.app_context_processor
def _gh_helpers():
    return {"gh_device_url": lambda name: gitsync.device_url(name) if gitsync.is_enabled() else None}


@bp.app_template_filter("size")
def fmt_size(n):
    n = n or 0
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _bool(name):
    return request.form.get(name) in ("1", "on", "true", "yes")


def _ids(values):
    return [int(v) for v in values if str(v).strip().isdigit()]


# --------------------------------------------------------------------------- auth

@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = User.query.filter_by(username=request.form.get("username", "").strip()).first()
        if u and u.check_password(request.form.get("password", "")):
            session.clear()
            session.permanent = True
            session["user"] = u.username
            session["must_change"] = bool(u.must_change)
            log_event("User logged in", "auth", user=u.username, details=request.remote_addr)
            nxt = request.args.get("next") or ""
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("web.dashboard"))
        log_event(f"Failed login for '{request.form.get('username', '')}'", "auth", "WARN",
                  user="-", details=request.remote_addr)
        flash("Invalid username or password", "error")
    return render_template("login.html")


@bp.route("/logout")
def logout():
    log_event("User logged out", "auth")
    session.clear()
    return redirect(url_for("web.login"))


@bp.route("/account", methods=["GET", "POST"])
def account():
    me = User.query.filter_by(username=session["user"]).first_or_404()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "password":
            if not me.check_password(request.form.get("current", "")):
                flash("Current password is wrong", "error")
            elif len(request.form.get("new", "")) < 8:
                flash("New password must be at least 8 characters", "error")
            elif request.form.get("new") != request.form.get("confirm"):
                flash("Passwords do not match", "error")
            else:
                me.set_password(request.form["new"])
                me.must_change = False
                session["must_change"] = False
                db.session.commit()
                log_event("Password changed", "auth")
                flash("Password changed", "ok")
                return redirect(url_for("web.dashboard"))
        elif action == "adduser":
            if not me.is_admin:
                abort(403, "Admin access required.")
            name = request.form.get("username", "").strip()
            pw = request.form.get("password", "")
            role = request.form.get("role", "read-write")
            if role not in USER_ROLES:
                role = "read-write"
            if not name or len(pw) < 8:
                flash("Username required and password must be at least 8 characters", "error")
            elif User.query.filter_by(username=name).first():
                flash("User already exists", "error")
            else:
                u = User(username=name, must_change=True, role=role)
                u.set_password(pw)
                db.session.add(u)
                db.session.commit()
                log_event(f"User '{name}' created with role '{role}'", "auth")
                flash(f"User {name} created (must change password at first login)", "ok")
        elif action == "setrole":
            if not me.is_admin:
                abort(403, "Admin access required.")
            u = db.session.get(User, int(request.form.get("id", 0)))
            new_role = request.form.get("role", "read-write")
            if u and u.username != me.username and new_role in USER_ROLES:
                u.role = new_role
                db.session.commit()
                log_event(f"User '{u.username}' role changed to '{new_role}'", "auth")
                flash(f"Role updated for {u.username}", "ok")
        elif action == "deluser":
            if not me.is_admin:
                abort(403, "Admin access required.")
            u = db.session.get(User, int(request.form.get("id", 0)))
            if u and u.username != me.username:
                db.session.delete(u)
                db.session.commit()
                log_event(f"User '{u.username}' deleted", "auth")
                flash("User deleted", "ok")
        return redirect(url_for("web.account"))
    return render_template("account.html", users=User.query.order_by(User.username).all(), me=me,
                           USER_ROLES=USER_ROLES)


# --------------------------------------------------------------------------- dashboard

@bp.route("/")
def dashboard():
    devices = Device.query.order_by(Device.role.desc(), Device.group, Device.name).all()
    since = now() - timedelta(hours=24)
    counts = {
        "total": len(devices),
        "up": sum(d.status == "up" for d in devices),
        "down": sum(d.status == "down" for d in devices),
        "sshdown": sum(d.status == "ssh-down" for d in devices),
        "vc_degraded": sum(d.vc_degraded for d in devices),
        "backup_failed": sum(d.last_backup_status == "failed" for d in devices if d.enabled),
        "stale": sum(1 for d in devices if d.enabled and (not d.last_backup_at or d.last_backup_at < now() - timedelta(days=7))),
        "changed_24h": Backup.query.filter(Backup.created_at >= since, Backup.changed.is_(True)).count(),
        "backups_24h": Backup.query.filter(Backup.created_at >= since).count(),
    }

    # --- Floor-wise grouping (by device.group) ---
    from collections import defaultdict
    floor_map = defaultdict(list)
    for d in devices:
        floor_map[d.group or "(No Group)"].append(d)
    floors = sorted(floor_map.items(), key=lambda x: x[0])

    # --- Chart data: backups + changes per day over last 14 days ---
    chart_days = 14
    day_labels = []
    daily_backups = []
    daily_changes = []
    for i in range(chart_days - 1, -1, -1):
        day_start = (now() - timedelta(days=i)).replace(hour=0, minute=0, second=0)
        day_end   = (now() - timedelta(days=i)).replace(hour=23, minute=59, second=59)
        day_labels.append(day_start.strftime("%d %b"))
        daily_backups.append(Backup.query.filter(Backup.created_at >= day_start, Backup.created_at <= day_end).count())
        daily_changes.append(Backup.query.filter(Backup.created_at >= day_start, Backup.created_at <= day_end, Backup.changed.is_(True)).count())

    # --- Reachability chart data ---
    reach_data = {
        "up": counts["up"],
        "ssh_only": counts["sshdown"],
        "down": counts["down"],
        "unknown": counts["total"] - counts["up"] - counts["sshdown"] - counts["down"],
    }

    recent_changes = (Backup.query.filter(Backup.changed.is_(True)).order_by(Backup.created_at.desc()).limit(8).all())
    logs = LogEntry.query.order_by(LogEntry.id.desc()).limit(12).all()
    schedules = [(s, next_run(s.id)) for s in Schedule.query.filter_by(enabled=True).all()]
    schedules.sort(key=lambda x: (x[1] is None, x[1].timestamp() if x[1] else 0))
    jobs = Job.query.order_by(Job.id.desc()).limit(5).all()
    return render_template("dashboard.html", devices=devices, c=counts, recent_changes=recent_changes,
                           logs=logs, schedules=schedules[:5], jobs=jobs,
                           floors=floors, day_labels=day_labels,
                           daily_backups=daily_backups, daily_changes=daily_changes,
                           reach_data=reach_data)


# --------------------------------------------------------------------------- devices

@bp.route("/devices")
def devices():
    q = Device.query
    f = {k: request.args.get(k, "") for k in ("q", "platform", "group", "role", "status")}
    if f["q"]:
        like = f"%{f['q']}%"
        q = q.filter(or_(Device.name.ilike(like), Device.host.ilike(like), Device.location.ilike(like)))
    for k in ("platform", "group", "role", "status"):
        if f[k]:
            q = q.filter(getattr(Device, k) == f[k])
    groups = [g for (g,) in db.session.query(Device.group).distinct() if g]
    return render_template("devices.html", devices=q.order_by(Device.name).all(), f=f, groups=sorted(groups),
                           PLATFORMS=PLATFORMS, ROLES=ROLES)


@bp.route("/devices/new", methods=["GET", "POST"])
@bp.route("/devices/<int:dev_id>/edit", methods=["GET", "POST"])
@require_write
def device_edit(dev_id=None):
    d = db.session.get(Device, dev_id) if dev_id else Device()
    if dev_id and not d:
        abort(404)
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
            d.model = request.form.get("model", "Auto")
            d.role = request.form.get("role", "access")
            d.group = request.form.get("group", "").strip()
            d.location = request.form.get("location", "").strip()
            d.is_vc = _bool("is_vc")
            d.legacy_ssh = _bool("legacy_ssh")
            d.enabled = _bool("enabled")
            d.notes = request.form.get("notes", "")
            d.credential_id = int(request.form["credential_id"]) if request.form.get("credential_id") else None
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
    groups = sorted(g for (g,) in db.session.query(Device.group).distinct() if g)
    return render_template("device_form.html", d=d, creds=Credential.query.order_by(Credential.name).all(),
                           PLATFORMS=PLATFORMS, MODELS=MODELS, ROLES=ROLES, groups=groups)


@bp.route("/devices/<int:dev_id>/delete", methods=["POST"])
@require_write
def device_delete(dev_id):
    d = db.get_or_404(Device, dev_id)
    for b in d.backups.all():
        engine.delete_backup(b, commit=False)
    log_event(f"Device '{d.name}' ({d.host}) deleted with its backups", "device", "WARN", commit=False)
    name = d.name
    db.session.delete(d)
    db.session.commit()
    gitsync.remove_device(name)
    flash("Device deleted", "ok")
    return redirect(url_for("web.devices"))


@bp.route("/devices/<int:dev_id>")
def device_detail(dev_id):
    d = db.get_or_404(Device, dev_id)
    backups = d.backups.limit(200).all()
    return render_template("device_detail.html", d=d, backups=backups, PLATFORMS=PLATFORMS)


# "model" is not part of the CSV: it is detected from the switch after import (an old CSV that
# still has a model column is accepted).
CSV_FIELDS = ["name", "host", "port", "platform", "role", "group", "location", "is_vc",
              "legacy_ssh", "enabled", "credential", "notes"]


@bp.route("/devices/export.csv")
def devices_export():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(CSV_FIELDS)
    for d in Device.query.order_by(Device.name):
        w.writerow([d.name, d.host, d.port, d.platform, d.role, d.group, d.location, int(d.is_vc),
                    int(d.legacy_ssh), int(d.enabled), d.credential.name if d.credential else "", d.notes])
    return Response(BOM + out.getvalue(), content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=devices.csv"})


@bp.route("/devices/template.csv")
def devices_template():
    rows = [CSV_FIELDS,
            ["CORE-EX3300-VC", "10.10.0.1", 22, "juniper_junos", "core", "Core", "Server room", 1, 1, 1, "juniper", ""],
            ["F1-EX2300-VC", "10.10.1.10", 22, "juniper_junos", "access", "Floor-1", "IDF 1", 1, 0, 1, "juniper", ""],
            ["OPD-EX2200", "10.10.2.10", 22, "juniper_junos", "access", "OPD", "IDF 2", 0, 1, 1, "juniper", ""],
            ["ADM-C1300", "10.10.3.10", 22, "cisco_c1300", "access", "Admin", "IDF 3", 0, 0, 1, "cisco", ""]]
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return Response(BOM + out.getvalue(), content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=devices_template.csv"})


@bp.route("/devices/import", methods=["POST"])
def devices_import():
    f = request.files.get("file")
    if f and request.content_length and request.content_length > 5 * 1024 * 1024:
        flash("CSV file is too large (max 5 MB)", "error")
        return redirect(url_for("web.devices"))
    if not f:
        flash("Choose a CSV file", "error")
        return redirect(url_for("web.devices"))
    creds = {c.name.lower(): c.id for c in Credential.query.all()}
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
        new = d is None
        d = d or Device(name=row["name"])
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


# --------------------------------------------------------------------------- actions (JSON)

@bp.route("/api/backup", methods=["POST"])
def api_backup():
    data = request.get_json(silent=True) or {}
    if data.get("all"):
        ids = [d.id for d in Device.query.filter_by(enabled=True)]
    else:
        ids = _ids(data.get("ids", []))
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
    ids = None if data.get("all") else _ids(data.get("ids", []))
    res = reach.check_devices(current_app._get_current_object(), ids)
    return jsonify({str(k): {"status": s, "ping_ms": r} for k, (s, r) in res.items()})


@bp.route("/api/detect", methods=["POST"])
def api_detect():
    data = request.get_json(silent=True) or {}
    ids = _ids(data.get("ids", []))
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
    data = request.get_json(silent=True) or {}
    kind = data.get("kind")
    try:
        title, text = engine.live_command(d, kind, data.get("a"), data.get("b"))
        log_event(f"Live tool '{kind}' run", "device", device=d)
        return jsonify(title=title, text=text)
    except Exception as e:  # noqa: BLE001
        log_event(f"Live tool '{kind}' failed: {e}", "device", "ERROR", device=d)
        return jsonify(error=f"{type(e).__name__}: {e}"), 502


# --------------------------------------------------------------------------- backups

@bp.route("/backups")
def backups():
    q = Backup.query.join(Device)
    f = {k: request.args.get(k, "") for k in ("device", "changed", "trigger", "since", "until")}
    if f["device"]:
        q = q.filter(Backup.device_id == int(f["device"]))
    if f["changed"] == "1":
        q = q.filter(Backup.changed.is_(True))
    if f["trigger"]:
        q = q.filter(Backup.trigger.like(f"{f['trigger']}%"))
    if f["since"]:
        q = q.filter(Backup.created_at >= datetime.fromisoformat(f["since"]))
    if f["until"]:
        q = q.filter(Backup.created_at < datetime.fromisoformat(f["until"]) + timedelta(days=1))
    page = max(1, int(request.args.get("page", 1)))
    p = q.order_by(Backup.created_at.desc()).paginate(page=page, per_page=50, error_out=False)
    total_size = db.session.query(func.sum(Backup.size)).scalar() or 0
    return render_template("backups.html", p=p, f=f, devices=Device.query.order_by(Device.name).all(),
                           total_size=total_size, total=Backup.query.count())


@bp.route("/backups/<int:bid>")
def backup_view(bid):
    b = db.get_or_404(Backup, bid)
    part = request.args.get("part", "conf")
    rel = {"conf": b.path, "set": b.set_path, "info": b.info_path}.get(part) or b.path
    return render_template("backup_view.html", b=b, part=part, text=read_backup_file(rel), prev=b.previous)


@bp.route("/backups/<int:bid>/download")
def backup_download(bid):
    b = db.get_or_404(Backup, bid)
    rel = {"conf": b.path, "set": b.set_path, "info": b.info_path}.get(request.args.get("part", "conf")) or b.path
    path = (BACKUP_DIR / rel).resolve()
    if not path.exists():
        abort(404, "Backup file missing on disk")
    log_event(f"Backup downloaded: {path.name}", "backup", device=b.device)
    return send_file(path, as_attachment=True, download_name=path.name, mimetype="text/plain")


@bp.route("/backups/<int:bid>/pin", methods=["POST"])
def backup_pin(bid):
    b = db.get_or_404(Backup, bid)
    b.pinned = not b.pinned
    db.session.commit()
    flash("Backup pinned (kept forever)" if b.pinned else "Backup unpinned", "ok")
    return redirect(request.referrer or url_for("web.backups"))


@bp.route("/backups/<int:bid>/delete", methods=["POST"])
def backup_delete(bid):
    b = db.get_or_404(Backup, bid)
    dev = b.device
    log_event(f"Backup deleted: {b.path}", "backup", "WARN", dev, commit=False)
    engine.delete_backup(b)
    flash("Backup deleted", "ok")
    return redirect(request.referrer or url_for("web.backups"))


@bp.route("/backups/zip")
def backups_zip():
    """?ids=1,2,3 for specific backups, or ?latest=1[&device=..] for newest backup of each device."""
    if request.args.get("latest"):
        sub = db.session.query(Backup.device_id, func.max(Backup.created_at).label("m")).group_by(Backup.device_id).subquery()
        items = Backup.query.join(sub, (Backup.device_id == sub.c.device_id) & (Backup.created_at == sub.c.m)).all()
    else:
        items = Backup.query.filter(Backup.id.in_(_ids(request.args.get("ids", "").split(",")))).all()
    if request.args.get("device"):
        items = [b for b in items if b.device_id == int(request.args["device"])]
    if not items:
        flash("Nothing to download", "error")
        return redirect(request.referrer or url_for("web.backups"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for b in items:
            for rel in (b.path, b.set_path, b.info_path):
                if rel and (BACKUP_DIR / rel).exists():
                    z.write(BACKUP_DIR / rel, rel)
    buf.seek(0)
    name = f"switch-backups_{now().strftime('%Y%m%d-%H%M%S')}.zip"
    log_event(f"ZIP download of {len(items)} backup(s)", "backup")
    return send_file(buf, as_attachment=True, download_name=name, mimetype="application/zip")


@bp.route("/compare")
def compare():
    a = db.session.get(Backup, int(request.args.get("a", 0) or 0))
    b = db.session.get(Backup, int(request.args.get("b", 0) or 0))
    if not a or not b:
        flash("Pick two backups to compare", "error")
        return redirect(request.referrer or url_for("web.backups"))
    if a.created_at > b.created_at:
        a, b = b, a
    fmt = request.args.get("fmt") or ("set" if a.set_path and b.set_path else "conf")
    if fmt == "set" and not (a.set_path and b.set_path):
        fmt = "conf"
    mode = request.args.get("mode", "side")
    ctx = request.args.get("ctx", "5")
    context = None if ctx == "all" else int(ctx)
    pa = a.set_path if fmt == "set" else a.path
    pb = b.set_path if fmt == "set" else b.path
    ta, tb = read_backup_file(pa), read_backup_file(pb)
    added, removed = stats(ta, tb)
    rows = side_by_side(ta, tb, context) if mode == "side" else None
    udiff = unified(ta, tb, pa, pb, context if context is not None else 100000) if mode != "side" else None
    others = Backup.query.filter(Backup.device_id.in_({a.device_id, b.device_id})) \
        .order_by(Backup.created_at.desc()).limit(100).all()
    return render_template("compare.html", a=a, b=b, fmt=fmt, mode=mode, ctx=ctx, rows=rows, udiff=udiff,
                           added=added, removed=removed, others=others)


@bp.route("/compare.diff")
def compare_download():
    a = db.get_or_404(Backup, int(request.args["a"]))
    b = db.get_or_404(Backup, int(request.args["b"]))
    if a.created_at > b.created_at:
        a, b = b, a
    fmt = request.args.get("fmt", "conf")
    pa = a.set_path if fmt == "set" and a.set_path else a.path
    pb = b.set_path if fmt == "set" and b.set_path else b.path
    text = unified(read_backup_file(pa), read_backup_file(pb), pa, pb) + "\n"
    return Response(text, mimetype="text/plain", headers={
        "Content-Disposition": f"attachment; filename={safe_name(a.device.name)}_{a.id}_vs_{b.id}.diff"})


# --------------------------------------------------------------------------- schedules

@bp.route("/schedules")
def schedules():
    items = [(s, next_run(s.id), len(resolve_targets(s))) for s in Schedule.query.order_by(Schedule.name)]
    groups = sorted(g for (g,) in db.session.query(Device.group).distinct() if g)
    edit = db.session.get(Schedule, int(request.args.get("edit", 0) or 0))
    return render_template("schedules.html", items=items, groups=groups, edit=edit,
                           devices=Device.query.order_by(Device.name).all(), ROLES=ROLES, PLATFORMS=PLATFORMS)


@bp.route("/schedules/save", methods=["POST"])
def schedule_save():
    sid = request.form.get("id")
    s = db.session.get(Schedule, int(sid)) if sid else Schedule()
    s.name = request.form.get("name", "").strip() or "Backup"
    s.frequency = request.form.get("frequency", "daily")
    s.time = request.form.get("time", "02:00") or "02:00"
    s.day_of_week = ",".join(request.form.getlist("day_of_week")) or "sun"
    s.day_of_month = int(request.form.get("day_of_month") or 1)
    s.cron = request.form.get("cron", "").strip()
    s.target = request.form.get("target", "all")
    if s.target == "devices":
        s.target_value = ",".join(request.form.getlist("target_devices"))
    else:
        s.target_value = request.form.get(f"target_{s.target}", "")
    s.enabled = _bool("enabled")
    try:
        make_trigger(s)
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        flash(f"Invalid schedule: {e}", "error")
        return redirect(url_for("web.schedules"))
    if Schedule.query.filter(Schedule.name == s.name, Schedule.id != (s.id or 0)).first():
        db.session.rollback()
        flash("A schedule with that name already exists", "error")
        return redirect(url_for("web.schedules"))
    if not sid:
        db.session.add(s)
    db.session.commit()
    sync_schedules()
    log_event(f"Schedule '{s.name}' saved ({s.frequency}, target={s.target} {s.target_value})", "schedule")
    flash("Schedule saved", "ok")
    return redirect(url_for("web.schedules"))


@bp.route("/schedules/<int:sid>/<action>", methods=["POST"])
def schedule_action(sid, action):
    s = db.get_or_404(Schedule, sid)
    if action == "delete":
        log_event(f"Schedule '{s.name}' deleted", "schedule")
        db.session.delete(s)
        db.session.commit()
    elif action == "toggle":
        s.enabled = not s.enabled
        db.session.commit()
        log_event(f"Schedule '{s.name}' {'enabled' if s.enabled else 'disabled'}", "schedule")
    elif action == "run":
        import threading
        threading.Thread(target=run_schedule, args=[s.id], daemon=True).start()
        flash(f"Schedule '{s.name}' started - see Logs for results", "ok")
    sync_schedules()
    return redirect(url_for("web.schedules"))


# --------------------------------------------------------------------------- logs

def _log_query():
    q = LogEntry.query
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
    return render_template("logs.html", p=p, f=f, devices=Device.query.order_by(Device.name).all(),
                           categories=["backup", "reachability", "schedule", "device", "git", "auth", "system"])


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
def logs_purge():
    days = int(request.form.get("days") or 0)
    n = LogEntry.query.filter(LogEntry.ts < now() - timedelta(days=days)).delete()
    db.session.commit()
    log_event(f"Purged {n} log entries older than {days} day(s)", "system", "WARN")
    flash(f"Purged {n} log entries", "ok")
    return redirect(url_for("web.logs"))


# --------------------------------------------------------------------------- GitHub mirror

@bp.route("/github", methods=["GET", "POST"])
def github():
    if request.method == "POST":
        action = request.form.get("action")
        app = current_app._get_current_object()
        try:
            if action == "save":
                was_reset = gitsync.save_settings(request.form)
                log_event(f"GitHub settings saved (enabled={gitsync.setting('git_enabled')}, "
                          f"repo={gitsync.setting('git_url')}, branch={gitsync.setting('git_branch')})", "git")
                if gitsync.is_enabled() and gitsync.setting("git_token_enc"):
                    gitsync.full_sync(app, session.get("user"))
                    flash("Settings saved - syncing with GitHub in the background" +
                          (" (repository changed, local copy rebuilt)" if was_reset else ""), "ok")
                else:
                    flash("Settings saved" + ("" if gitsync.is_enabled() else " - GitHub sync is off"), "ok")
            elif action == "test":
                flash(gitsync.test_connection(), "ok")
            elif action == "sync":
                if gitsync.full_sync(app, session.get("user")):
                    flash("Sync started", "ok")
                else:
                    flash("A sync is already running", "error")
            elif action == "push":
                gitsync.push(raise_errors=True)
                flash("Pushed to GitHub", "ok")
            elif action == "reset":
                gitsync.reset_local()
                log_event("Local copy of the GitHub repository discarded - re-downloading", "git", "WARN")
                gitsync.full_sync(app, session.get("user"))
                flash("Local copy discarded - re-downloading from GitHub", "ok")
        except (gitsync.GitError, ValueError) as e:
            flash(str(e), "error")
        return redirect(url_for("web.github"))
    try:
        commits, tags, pending = gitsync.recent_commits(), gitsync.recent_tags(), gitsync.ahead()
        local_error = None
    except gitsync.GitError as e:
        commits, tags, pending, local_error = [], [], 0, str(e)
    s = {k: gitsync.setting(k) for k in gitsync.GIT_DEFAULTS if k != "git_token_enc"}
    return render_template("github.html", s=s, has_token=bool(gitsync.setting("git_token_enc")),
                           commits=commits, tags=tags, pending=pending, local_error=local_error,
                           progress=gitsync.progress, web_url=gitsync.web_url())


@bp.route("/api/github/progress")
def api_github_progress():
    return jsonify(gitsync.progress)


# --------------------------------------------------------------------------- export & migrate

@bp.route("/migrate")
def migrate():
    import platform
    import sys
    usage = sum(p.stat().st_size for p in BACKUP_DIR.rglob("*") if p.is_file())
    info = {
        "Config file": f"{config.CONFIG_FILE}" + ("" if config.CONFIG_FILE.exists() else " (not present - defaults)"),
        "Data folder": str(BASE_DIR),
        "Listening on": f"{config.get('host')}:{config.get('port')}",
        "Behind proxy / secure cookies": f"{config.get('behind_proxy')} / {config.get('secure_cookies')}",
        "Server": f"{platform.node()} - {platform.system()} {platform.release()}",
        "Version": f"MGM Network Vault {__version__}",
        "Python": sys.version.split()[0],
    }
    counts = {"Devices": Device.query.count(), "Backups": Backup.query.count(), "Backup data": fmt_size(usage),
              "Schedules": Schedule.query.count(), "Credential profiles": Credential.query.count(),
              "Users": User.query.count()}
    previous = sorted((p.name for p in BASE_DIR.glob("pre-restore-*") if p.is_dir()), reverse=True)
    return render_template("migrate.html", info=info, counts=counts, pending=portable.pending_restore(BASE_DIR),
                           previous=previous, exported_config=(BASE_DIR / "config.toml.from-export").exists())


@bp.route("/migrate/export", methods=["POST"])
def migrate_export():
    pw = request.form.get("passphrase", "")
    if pw != request.form.get("confirm", ""):
        flash("Passphrases do not match", "error")
        return redirect(url_for("web.migrate"))
    tmp = BASE_DIR / "tmp"
    tmp.mkdir(exist_ok=True)
    out = tmp / f"network-vault-export_{now():%Y%m%d-%H%M%S}.nvault"
    try:
        m = portable.export_archive(BASE_DIR, out, pw, config.CONFIG_FILE)
    except portable.ExportError as e:
        flash(str(e), "error")
        return redirect(url_for("web.migrate"))
    log_event(f"Full export downloaded: {m['counts']['device']} devices, {m['counts']['backup']} backups, "
              f"{out.stat().st_size // 1024} KB", "system", "WARN")

    def stream():  # delete the temporary file once it has been sent
        try:
            with open(out, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    yield chunk
        finally:
            out.unlink(missing_ok=True)

    return Response(stream(), mimetype="application/octet-stream", headers={
        "Content-Disposition": f"attachment; filename={out.name}", "Content-Length": str(out.stat().st_size)})


@bp.route("/migrate/import", methods=["POST"])
def migrate_import():
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Choose an export file (.nvault)", "error")
        return redirect(url_for("web.migrate"))
    tmp = BASE_DIR / "tmp"
    tmp.mkdir(exist_ok=True)
    upload = tmp / f"upload-{now():%Y%m%d-%H%M%S}.nvault"
    f.save(upload)
    try:
        m = portable.stage_restore(BASE_DIR, upload, request.form.get("passphrase", ""))
        log_event(f"Restore staged from export of {m['source_host']} ({m['created']}): "
                  f"{m['counts']['device']} devices, {m['counts']['backup']} backups - applied on restart",
                  "system", "WARN")
        flash("Export verified. Restart to switch over to the restored data.", "ok")
    except portable.ExportError as e:
        flash(str(e), "error")
    finally:
        upload.unlink(missing_ok=True)
    return redirect(url_for("web.migrate"))


@bp.route("/migrate/cancel", methods=["POST"])
def migrate_cancel():
    portable.cancel_restore(BASE_DIR)
    log_event("Staged restore cancelled", "system")
    flash("Staged restore cancelled", "ok")
    return redirect(url_for("web.migrate"))


@bp.route("/migrate/restart", methods=["POST"])
def migrate_restart():
    import os
    import threading
    import time
    log_event("Restart requested from the web UI", "system", "WARN")

    def bye():
        time.sleep(1.5)  # let the response reach the browser
        os._exit(3)      # start.bat / the service task restart the app on exit code 3

    threading.Thread(target=bye, daemon=True).start()
    return render_template("restarting.html")


@bp.route("/migrate/package")
def migrate_package():
    log_event("Application package downloaded", "system")
    return Response(portable.app_package(config.APP_ROOT), mimetype="application/zip", headers={
        "Content-Disposition": f"attachment; filename=network-vault-app_v{__version__}.zip"})


@bp.route("/migrate/config-from-export")
def migrate_exported_config():
    p = BASE_DIR / "config.toml.from-export"
    if not p.exists():
        abort(404)
    return send_file(p, as_attachment=True, download_name="config.toml.from-export", mimetype="text/plain")


# --------------------------------------------------------------------------- credentials & settings

@bp.route("/credentials", methods=["GET", "POST"])
@require_write
def credentials():
    if request.method == "POST":
        cid = request.form.get("id")
        c = db.session.get(Credential, int(cid)) if cid else Credential()
        c.name = request.form.get("name", "").strip()
        c.username = request.form.get("username", "").strip()
        if request.form.get("password"):
            c.password_enc = encrypt(request.form["password"])
        if request.form.get("secret") or _bool("clear_secret"):
            c.secret_enc = encrypt(request.form.get("secret", "")) if not _bool("clear_secret") else None
        if not c.name or not c.username or not c.password_enc:
            db.session.rollback()
            flash("Name, username and password are required", "error")
        else:
            if not cid:
                db.session.add(c)
            db.session.commit()
            log_event(f"Credential profile '{c.name}' saved", "system")
            flash("Credential profile saved", "ok")
        return redirect(url_for("web.credentials"))
    return render_template("credentials.html", creds=Credential.query.order_by(Credential.name).all(),
                           edit=db.session.get(Credential, int(request.args.get("edit", 0) or 0)))


@bp.route("/credentials/<int:cid>/delete", methods=["POST"])
def credential_delete(cid):
    c = db.get_or_404(Credential, cid)
    if c.devices:
        flash(f"Profile is used by {len(c.devices)} device(s) - reassign them first", "error")
    else:
        db.session.delete(c)
        db.session.commit()
        log_event(f"Credential profile '{c.name}' deleted", "system", "WARN")
        flash("Deleted", "ok")
    return redirect(url_for("web.credentials"))


@bp.route("/settings", methods=["GET", "POST"])
@require_admin
def settings():
    if request.method == "POST":
        for k in DEFAULT_SETTINGS:
            if k == "skip_unchanged":
                set_setting(k, "1" if _bool(k) else "0")
            elif request.form.get(k, "").strip().isdigit():
                set_setting(k, request.form[k].strip())
        db.session.commit()
        sync_schedules()
        log_event("Settings updated", "system")
        flash("Settings saved", "ok")
        return redirect(url_for("web.settings"))
    usage = sum(p.stat().st_size for p in BACKUP_DIR.rglob("*") if p.is_file())
    return render_template("settings.html", s={k: get_setting(k) for k in DEFAULT_SETTINGS},
                           backup_dir=BACKUP_DIR, usage=usage)
