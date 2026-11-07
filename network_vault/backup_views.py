"""Backup browsing, download, pin/delete, ZIP export and config compare."""
import io
import zipfile
from datetime import datetime, timedelta
from flask import Response, abort, flash, redirect, render_template, request, send_file, url_for
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from . import engine
from .diffing import side_by_side, stats, unified
from .models import Backup, Device, db, now, log_event
from .util import BACKUP_DIR, read_backup_file, safe_name
from .web import bp, check_device_access, current_user, form_ids, require_write, site_filter


@bp.route("/backups")
def backups():
    q = site_filter(Backup.query.join(Device), Device)
    f = {k: request.args.get(k, "") for k in ("q", "device", "changed", "trigger", "since", "until")}
    if f["q"]:
        like = f"%{f['q']}%"
        q = q.filter(or_(
            Device.name.ilike(like),
            Device.host.ilike(like),
            Device.location.ilike(like),
            Backup.last_commit.ilike(like),
            Backup.trigger.ilike(like),
            Backup.git_commit.ilike(like),
            Backup.path.ilike(like),
        ))
    if f["device"]:
        q = q.filter(Backup.device_id == int(f["device"]))
    if f["changed"] == "1":
        q = q.filter(Backup.changed.is_(True))
    if f["trigger"]:
        q = q.filter(Backup.trigger.like(f"{f['trigger']}%"))
    if f["since"]:
        try:
            q = q.filter(Backup.created_at >= datetime.fromisoformat(f["since"]))
        except (ValueError, TypeError):
            pass
    if f["until"]:
        try:
            q = q.filter(Backup.created_at < datetime.fromisoformat(f["until"]) + timedelta(days=1))
        except (ValueError, TypeError):
            pass
    page = max(1, int(request.args.get("page", 1)))
    p = (q.options(selectinload(Backup.device).selectinload(Device.site))
         .order_by(Backup.created_at.desc()).paginate(page=page, per_page=50, error_out=False))

    prev_ids = {}
    if p.items:
        older = db.aliased(Backup)
        prev_sq = (db.select(older.id)
                   .where(older.device_id == Backup.device_id, older.created_at < Backup.created_at)
                   .order_by(older.created_at.desc()).limit(1).scalar_subquery())
        prev_ids = dict(db.session.query(Backup.id, prev_sq)
                        .filter(Backup.id.in_([b.id for b in p.items])).all())

    size_q = site_filter(db.session.query(func.sum(Backup.size)).join(Device), Device)
    count_q = site_filter(db.session.query(func.count(Backup.id)).join(Device), Device)
    dev_q = site_filter(Device.query, Device)
        
    total_size = size_q.scalar() or 0
    total_backups = count_q.scalar() or 0
    return render_template("backups.html", p=p, f=f, prev_ids=prev_ids, devices=dev_q.order_by(Device.name).all(),
                           total_size=total_size, total=total_backups)


@bp.route("/backups/<int:bid>")
def backup_view(bid):
    b = db.get_or_404(Backup, bid)
    check_device_access(b.device)
    part = request.args.get("part", "conf")
    rel = {"conf": b.path, "set": b.set_path, "info": b.info_path}.get(part) or b.path
    return render_template("backup_view.html", b=b, part=part, text=read_backup_file(rel), prev=b.previous)


@bp.route("/backups/<int:bid>/download")
def backup_download(bid):
    b = db.get_or_404(Backup, bid)
    check_device_access(b.device)
    rel = {"conf": b.path, "set": b.set_path, "info": b.info_path}.get(request.args.get("part", "conf")) or b.path
    path = (BACKUP_DIR / rel).resolve()
    if not path.exists():
        abort(404, "Backup file missing on disk")
    log_event(f"Backup downloaded: {path.name}", "backup", device=b.device)
    return send_file(path, as_attachment=True, download_name=path.name, mimetype="text/plain")


@bp.route("/backups/<int:bid>/pin", methods=["POST"])
@require_write
def backup_pin(bid):
    b = db.get_or_404(Backup, bid)
    check_device_access(b.device)
    b.pinned = not b.pinned
    db.session.commit()
    flash("Backup pinned (kept forever)" if b.pinned else "Backup unpinned", "ok")
    return redirect(request.referrer or url_for("web.backups"))


@bp.route("/backups/<int:bid>/delete", methods=["POST"])
@require_write
def backup_delete(bid):
    b = db.get_or_404(Backup, bid)
    check_device_access(b.device)
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
        items = Backup.query.filter(Backup.id.in_(form_ids(request.args.get("ids", "").split(",")))).all()
    if request.args.get("device"):
        items = [b for b in items if b.device_id == int(request.args["device"])]
    me = current_user()
    items = [b for b in items if me.has_site_access(b.device.site_id)]
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
    check_device_access(a.device)
    check_device_access(b.device)
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
    check_device_access(a.device)
    check_device_access(b.device)
    if a.created_at > b.created_at:
        a, b = b, a
    fmt = request.args.get("fmt", "conf")
    pa = a.set_path if fmt == "set" and a.set_path else a.path
    pb = b.set_path if fmt == "set" and b.set_path else b.path
    text = unified(read_backup_file(pa), read_backup_file(pb), pa, pb) + "\n"
    return Response(text, mimetype="text/plain", headers={
        "Content-Disposition": f"attachment; filename={safe_name(a.device.name)}_{a.id}_vs_{b.id}.diff"})
