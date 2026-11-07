"""Backup schedule pages."""
import json
import threading
from flask import abort, flash, redirect, render_template, request, url_for

from .models import PLATFORMS, ROLES, Device, Schedule, Site, db, log_event
from .scheduler import make_trigger, next_run, resolve_targets, run_schedule, sync_schedules
from .web import bp, current_user, form_bool, group_sort_key, require_write, selected_site_id, site_filter


@bp.route("/schedules")
def schedules():
    q_sched = site_filter(Schedule.query, Schedule)
    items = [(s, next_run(s.id), len(resolve_targets(s))) for s in q_sched.order_by(Schedule.name)]
    excluded = {i for s, _, _ in items for i in s.excluded_ids}
    excluded_names = dict(db.session.query(Device.id, Device.name).filter(Device.id.in_(excluded))) if excluded else {}

    g_q = site_filter(db.session.query(Device.group).distinct(), Device)
    dev_q = site_filter(Device.query, Device)
    groups = sorted((g for (g,) in g_q if g), key=group_sort_key)
    edit = db.session.get(Schedule, int(request.args.get("edit", 0) or 0))
    return render_template("schedules.html", items=items, groups=groups, edit=edit, excluded_names=excluded_names,
                           devices=dev_q.order_by(Device.name).all(), ROLES=ROLES, PLATFORMS=PLATFORMS)


_SCHEDULE_TARGETS = ("all", "group", "role", "platform", "devices")


def _picked_ids(field: str) -> str:
    """Comma-separated, de-duplicated device ids from a multi-select form field."""
    return ",".join(dict.fromkeys(x for x in request.form.getlist(field) if x.isdigit()))


def _picked_values(target: str) -> list[str]:
    """Groups / roles / platforms picked in the form; unknown roles and platforms are dropped."""
    vals = [v.strip() for v in request.form.getlist(f"target_{target}") if v.strip()]
    known = {"role": set(ROLES), "platform": set(PLATFORMS)}.get(target)
    return list(dict.fromkeys(v for v in vals if known is None or v in known))


@bp.route("/schedules/save", methods=["POST"])
@require_write
def schedule_save():
    sid = request.form.get("id")
    s = db.session.get(Schedule, int(sid)) if sid else Schedule()
    u = current_user()
    if sid and not u.has_site_access(s.site_id):
        abort(403, "Cannot edit schedules for another site.")
    s.name = request.form.get("name", "").strip() or "Backup"
    s.frequency = request.form.get("frequency", "daily")
    s.time = request.form.get("time", "02:00") or "02:00"
    s.day_of_week = ",".join(request.form.getlist("day_of_week")) or "sun"
    s.day_of_month = int(request.form.get("day_of_month") or 1)
    s.cron = request.form.get("cron", "").strip()
    s.target = request.form.get("target", "all")
    if s.target not in _SCHEDULE_TARGETS:
        s.target = "all"
    if s.target == "devices":
        s.target_value = _picked_ids("target_devices")
        s.exclude_devices = ""          # picking devices one by one leaves nothing to exclude
    else:
        s.exclude_devices = _picked_ids("exclude_devices")
        s.target_value = ""
        if s.target != "all":
            vals = _picked_values(s.target)
            if not vals:
                db.session.rollback()
                flash(f"Pick at least one {s.target} for this schedule", "error")
                return redirect(url_for("web.schedules", edit=sid) if sid else url_for("web.schedules"))
            s.target_value = json.dumps(vals)
    s.enabled = form_bool("enabled")
    if not sid:
        s.site_id = selected_site_id() or (list(u.allowed_site_ids)[0] if (u and u.allowed_site_ids) else None)
        if not s.site_id:
            first_s = Site.query.order_by(Site.id).first()
            s.site_id = first_s.id if first_s else None
    try:
        make_trigger(s)
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        flash(f"Invalid schedule: {e}", "error")
        return redirect(url_for("web.schedules"))
    if Schedule.query.filter(Schedule.name == s.name, Schedule.site_id == s.site_id, Schedule.id != (s.id or 0)).first():
        db.session.rollback()
        flash("A schedule with that name already exists for this site", "error")
        return redirect(url_for("web.schedules"))
    if not sid:
        db.session.add(s)
    db.session.commit()
    sync_schedules()
    what = ", ".join(s.target_values) if s.target in ("group", "role", "platform") else s.target_value
    excl = f", excluding {len(s.excluded_ids)} device(s)" if s.excluded_ids else ""
    log_event(f"Schedule '{s.name}' saved ({s.frequency}, target={s.target} {what}{excl})", "schedule")
    flash("Schedule saved", "ok")
    return redirect(url_for("web.schedules"))


@bp.route("/schedules/<int:sid>/<action>", methods=["POST"])
@require_write
def schedule_action(sid, action):
    s = db.get_or_404(Schedule, sid)
    u = current_user()
    if not u.has_site_access(s.site_id):
        abort(403, "Cannot modify schedules belonging to another site.")
    if action == "delete":
        log_event(f"Schedule '{s.name}' deleted", "schedule")
        db.session.delete(s)
        db.session.commit()
    elif action == "toggle":
        s.enabled = not s.enabled
        db.session.commit()
        log_event(f"Schedule '{s.name}' {'enabled' if s.enabled else 'disabled'}", "schedule")
    elif action == "run":
        threading.Thread(target=run_schedule, args=[s.id], daemon=True).start()
        flash(f"Schedule '{s.name}' started - see Logs for results", "ok")
    sync_schedules()
    return redirect(url_for("web.schedules"))
