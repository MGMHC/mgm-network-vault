"""Backup job counters: parallel workers must not lose each other's +1 (read-modify-write race)."""
import threading

from network_vault import engine
from network_vault.models import Job, db, get_setting, set_setting

WORKERS = 8


def test_parallel_workers_count_every_result(app, monkeypatch):
    gate = threading.Barrier(WORKERS)
    results = ["ok", "failed", "unchanged", "ok"] * (WORKERS // 4)

    def fake_backup(dev_id, trigger, user, run_alerts=None):
        gate.wait(timeout=10)  # every worker updates the job at the same moment
        return results[dev_id]

    monkeypatch.setattr(engine, "backup_device", fake_backup)
    with app.app_context():
        saved = get_setting("max_workers")
        set_setting("max_workers", str(WORKERS))
    try:
        job_id = engine.start_backup_job(app, list(range(WORKERS)), wait=True)
        with app.app_context():
            j = db.session.get(Job, job_id)
            assert (j.done, j.ok, j.failed, j.unchanged) == (WORKERS, 6, 2, 2)
            assert j.status == "partial"
    finally:
        with app.app_context():
            set_setting("max_workers", saved)


def test_jobs_cut_off_by_a_restart_are_closed(app):
    """The browser polls a job until it stops 'running': one cut off by a restart must not run forever."""
    from network_vault import scheduler
    with app.app_context():
        job = Job(kind="backup", trigger="manual", total=3, status="running")
        db.session.add(job)
        db.session.commit()
        job_id = job.id
    scheduler.close_interrupted_jobs(app)
    with app.app_context():
        j = db.session.get(Job, job_id)
        assert j.status == "failed" and j.finished is not None
        db.session.delete(j)
        db.session.commit()
