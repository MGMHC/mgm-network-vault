"""APScheduler wiring: user backup schedules + housekeeping jobs."""
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import config, engine, gitsync, machistory, reach, syslog_listener
from .models import Device, Job, LogEntry, Schedule, db, get_bool, get_int, now, log_event


scheduler = BackgroundScheduler(job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 900})
_app = None


def make_trigger(s):
    hh, mm = (s.time or "02:00").split(":")
    if s.frequency == "hourly":
        return CronTrigger(minute=int(mm))
    if s.frequency == "daily":
        return CronTrigger(hour=int(hh), minute=int(mm))
    if s.frequency == "weekly":
        return CronTrigger(day_of_week=s.day_of_week or "sun", hour=int(hh), minute=int(mm))
    if s.frequency == "monthly":
        return CronTrigger(day=s.day_of_month or 1, hour=int(hh), minute=int(mm))
    return CronTrigger.from_crontab(s.cron)


_TARGET_COLUMNS = {"group": Device.group, "role": Device.role, "platform": Device.platform}


def resolve_targets(s):
    """Ids of the enabled devices a schedule backs up, minus its excluded devices."""
    q = Device.query.filter_by(enabled=True)
    if s.target in _TARGET_COLUMNS:
        q = q.filter(_TARGET_COLUMNS[s.target].in_(s.target_values))   # nothing picked -> nothing
    elif s.target == "devices":
        q = q.filter(Device.id.in_(s.device_ids))
    excluded = s.excluded_ids
    if excluded and s.target != "devices":
        q = q.filter(Device.id.notin_(excluded))
    return [d.id for d in q.all()]


def run_schedule(schedule_id):
    with _app.app_context():
        s = db.session.get(Schedule, schedule_id)
        if not s or not s.enabled:
            return
        ids = resolve_targets(s)
        name = s.name
        log_event(f"Schedule '{name}' started: {len(ids)} device(s)", "schedule", user="scheduler")
    job_id = engine.start_backup_job(_app, ids, trigger=f"schedule:{name}", wait=True)
    with _app.app_context():
        s = db.session.get(Schedule, schedule_id)
        j = db.session.get(Job, job_id)
        if s:
            s.last_run = j.started
            s.last_result = f"{j.ok} ok, {j.failed} failed / {j.total}"
            db.session.commit()


def reachability_tick():
    reach.check_devices(_app)


def mac_history_tick():
    machistory.sweep_if_enabled(_app)


def _git_retry():
    with _app.app_context():
        gitsync.retry_push()


def housekeeping():
    with _app.app_context():
        days = get_int("log_retention_days")
        if days > 0:
            n = LogEntry.query.filter(LogEntry.ts < now() - timedelta(days=days)).delete()
            Job.query.filter(Job.started < now() - timedelta(days=days)).delete()
            db.session.commit()
            if n:
                log_event(f"Housekeeping: purged {n} log entries older than {days} days", user="system")
        for d in Device.query.all():
            engine.apply_retention(d)
        machistory.purge_old()
        n = syslog_listener.purge_old_files(config.get("syslog_log_dir"), days)
        if n:
            log_event(f"Housekeeping: deleted {n} raw syslog file(s) older than {days} days", user="system")


def sync_schedules():
    """(Re)register all user schedules and the reachability poller from the database."""
    if _app is None:  # scheduler not started (e.g. tests / flask shell)
        return
    for job in scheduler.get_jobs():
        if job.id.startswith("sched-") or job.id in ("reachability", "mac-history"):
            job.remove()
    with _app.app_context():
        for s in Schedule.query.filter_by(enabled=True).all():
            try:
                scheduler.add_job(run_schedule, make_trigger(s), args=[s.id], id=f"sched-{s.id}", name=s.name)
            except Exception as e:  # noqa: BLE001
                log_event(f"Schedule '{s.name}' has an invalid trigger: {e}", "schedule", "ERROR", user="system")
        interval = get_int("ping_interval_min")
        history_hours = get_int("mac_history_interval_hours") if get_bool("mac_history_enabled") else 0
    if history_hours > 0:  # off by default: nothing is registered, so nothing sweeps
        scheduler.add_job(mac_history_tick, IntervalTrigger(hours=history_hours), id="mac-history",
                          name="MAC/IP history sweep")
    if interval > 0:
        scheduler.add_job(reachability_tick, IntervalTrigger(minutes=interval), id="reachability",
                          name="Reachability check", next_run_time=now() + timedelta(seconds=10))


def next_run(schedule_id):
    job = scheduler.get_job(f"sched-{schedule_id}")
    return job.next_run_time if job else None


def close_interrupted_jobs(app):
    """Jobs still 'running' at start-up were cut off by a restart; the browser would poll them forever."""
    with app.app_context():
        n = Job.query.filter_by(status="running").update({"status": "failed", "finished": now()})
        db.session.commit()
        if n:
            log_event(f"{n} backup job(s) interrupted by a restart marked as failed", "backup", "WARN", user="system")


def init_scheduler(app):
    global _app
    _app = app
    close_interrupted_jobs(app)
    scheduler.add_job(housekeeping, CronTrigger(hour=3, minute=30), id="housekeeping", replace_existing=True)
    gitsync.init(app)
    scheduler.add_job(_git_retry, IntervalTrigger(minutes=15), id="git-retry", replace_existing=True)
    sync_schedules()
    scheduler.start()
