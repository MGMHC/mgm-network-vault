"""APScheduler wiring: user backup schedules + housekeeping jobs."""
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .models import Device, Job, LogEntry, Schedule, db, get_int, now
from .util import log_event

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


def resolve_targets(s):
    q = Device.query.filter_by(enabled=True)
    if s.target == "group":
        q = q.filter(Device.group == s.target_value)
    elif s.target == "role":
        q = q.filter(Device.role == s.target_value)
    elif s.target == "platform":
        q = q.filter(Device.platform == s.target_value)
    elif s.target == "devices":
        ids = [int(x) for x in (s.target_value or "").split(",") if x.strip().isdigit()]
        q = q.filter(Device.id.in_(ids or [-1]))
    return [d.id for d in q.all()]


def run_schedule(schedule_id):
    from .engine import start_backup_job
    with _app.app_context():
        s = db.session.get(Schedule, schedule_id)
        if not s or not s.enabled:
            return
        ids = resolve_targets(s)
        name = s.name
        log_event(f"Schedule '{name}' started: {len(ids)} device(s)", "schedule", user="scheduler")
    job_id = start_backup_job(_app, ids, trigger=f"schedule:{name}", wait=True)
    with _app.app_context():
        s = db.session.get(Schedule, schedule_id)
        j = db.session.get(Job, job_id)
        if s:
            s.last_run = j.started
            s.last_result = f"{j.ok} ok, {j.failed} failed / {j.total}"
            db.session.commit()


def reachability_tick():
    from .reach import check_devices
    check_devices(_app)


def _git_retry():
    from . import gitsync
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
        from .engine import apply_retention
        for d in Device.query.all():
            apply_retention(d)


def sync_schedules():
    """(Re)register all user schedules and the reachability poller from the database."""
    if _app is None:  # scheduler not started (e.g. tests / flask shell)
        return
    for job in scheduler.get_jobs():
        if job.id.startswith("sched-") or job.id == "reachability":
            job.remove()
    with _app.app_context():
        for s in Schedule.query.filter_by(enabled=True).all():
            try:
                scheduler.add_job(run_schedule, make_trigger(s), args=[s.id], id=f"sched-{s.id}", name=s.name)
            except Exception as e:  # noqa: BLE001
                log_event(f"Schedule '{s.name}' has an invalid trigger: {e}", "schedule", "ERROR", user="system")
        interval = get_int("ping_interval_min")
    if interval > 0:
        scheduler.add_job(reachability_tick, IntervalTrigger(minutes=interval), id="reachability",
                          name="Reachability check", next_run_time=now() + timedelta(seconds=10))


def next_run(schedule_id):
    job = scheduler.get_job(f"sched-{schedule_id}")
    return job.next_run_time if job else None


def init_scheduler(app):
    global _app
    _app = app
    scheduler.add_job(housekeeping, CronTrigger(hour=3, minute=30), id="housekeeping", replace_existing=True)
    from . import gitsync
    gitsync.init(app)
    scheduler.add_job(_git_retry, IntervalTrigger(minutes=15), id="git-retry", replace_existing=True)
    sync_schedules()
    scheduler.start()
