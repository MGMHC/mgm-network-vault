"""Backup job runner, retention and updater rollback robustness."""
import subprocess
from pathlib import Path

from network_vault import alerts, engine, updater
from network_vault.models import Backup, Job, LogEntry, db


def test_backup_error_still_finishes_job_and_sends_alerts(app, seed, monkeypatch):
    sent = []
    monkeypatch.setattr(engine, "backup_device", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(alerts.BackupRunAlerts, "send", lambda self, app_, label: sent.append(label))
    job_id = engine.start_backup_job(app, [seed["dev"]["b"][0]], trigger="manual", wait=True)
    with app.app_context():
        j = db.session.get(Job, job_id)
        assert j.status == "failed" and j.finished and (j.done, j.failed) == (1, 1)
    assert sent == ["manual"]


def test_skipped_backup_is_logged(app, seed, monkeypatch):
    monkeypatch.setattr(engine, "backup_device", lambda *a, **k: "skipped")
    monkeypatch.setattr(alerts.BackupRunAlerts, "send", lambda self, app_, label: None)
    job_id = engine.start_backup_job(app, [seed["dev"]["b"][0]], trigger="syslog:commit", wait=True)
    with app.app_context():
        assert db.session.get(Job, job_id).status != "running"
        assert LogEntry.query.filter(LogEntry.message.like("Backup skipped (syslog:commit)%")).count() == 1


def test_delete_backup_survives_locked_file(app, seed, monkeypatch):
    from network_vault.util import BACKUP_DIR
    (BACKUP_DIR / "T-CORE").mkdir(parents=True, exist_ok=True)
    (BACKUP_DIR / "T-CORE/locked.conf").write_text("x", encoding="utf-8")

    def locked(self, missing_ok=False):
        raise PermissionError("in use by another process")
    monkeypatch.setattr(Path, "unlink", locked)
    with app.app_context():
        b = Backup(device_id=seed["dev"]["core"][0], path="T-CORE/locked.conf", size=1, trigger="manual", sha256="x")
        db.session.add(b)
        db.session.commit()
        bid = b.id
        engine.delete_backup(b)
        assert db.session.get(Backup, bid) is None
        assert LogEntry.query.filter(LogEntry.message.like("Could not delete backup file T-CORE/locked.conf%")).count()


def test_failed_pip_rolls_code_back(app, monkeypatch):
    calls = []

    def git(args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "abc123\n" if args[0] == "rev-parse" else "", "")

    def pip(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 120)
    monkeypatch.setattr(updater, "is_git_installation", lambda: True)
    monkeypatch.setattr(updater, "_git", git)
    monkeypatch.setattr(updater.subprocess, "run", pip)
    with app.app_context():
        res = updater.apply_update()
    assert not res["ok"] and "rolled back" in res["message"]
    assert ["reset", "--keep", "abc123"] in calls
