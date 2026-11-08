"""Software update: a failed 'pip install' must stop the update before the restart."""
import subprocess

from network_vault import updater


def test_failed_pip_install_does_not_restart(app, monkeypatch):
    started = []
    monkeypatch.setattr(updater, "is_git_installation", lambda: True)
    monkeypatch.setattr(updater, "_git", lambda args, **kw: subprocess.CompletedProcess(args, 0, "Updating", ""))

    def pip(cmd, **kw):
        if kw.get("check"):
            raise subprocess.CalledProcessError(1, cmd, "", "No matching distribution found for newlib")
        return subprocess.CompletedProcess(cmd, 1, "", "No matching distribution found for newlib")

    monkeypatch.setattr(updater.subprocess, "run", pip)
    monkeypatch.setattr(updater.threading, "Thread", lambda *a, **kw: started.append(kw) or _NoThread())
    monkeypatch.setattr(updater.os, "_exit", lambda code: started.append(code))
    with app.app_context():
        res = updater.apply_update()
    assert not res["ok"] and "newlib" in res["message"]
    assert started == []


class _NoThread:
    def start(self):
        pass


def _fake_git(outputs):
    def run(args, **kw):
        return subprocess.CompletedProcess(args, 0, outputs.get(args[0], ""), "")
    return run


def test_check_reports_commits_behind_and_newest_tag(monkeypatch):
    monkeypatch.setattr(updater, "is_git_installation", lambda: True)
    monkeypatch.setattr(updater, "__version__", "1.0.0")
    monkeypatch.setattr(updater, "_git", _fake_git({"rev-list": "2\n", "tag": "v1.1.0\nv1.0.0\n",
                                                    "log": "abc - fix (2026-11-08)\ndef - feat (2026-11-08)\n"}))
    r = updater.check_for_updates()
    assert (r["ok"], r["is_git"], r["behind_count"], r["latest_tag"], r["update_available"]) == (True, True, 2, "v1.1.0", True)
    assert r["commits"] == ["abc - fix (2026-11-08)", "def - feat (2026-11-08)"] and r["error"] is None


def test_check_up_to_date_and_errors(monkeypatch):
    monkeypatch.setattr(updater, "__version__", "1.0.0")
    monkeypatch.setattr(updater, "is_git_installation", lambda: False)
    assert updater.check_for_updates() == {
        "ok": True, "is_git": False, "current_version": "1.0.0", "latest_tag": "v1.0.0", "behind_count": 0,
        "update_available": False, "commits": [], "error": None,
        "message": "Installed from a standalone package (not a Git repository)."}
    monkeypatch.setattr(updater, "is_git_installation", lambda: True)
    monkeypatch.setattr(updater, "_git", _fake_git({"rev-list": "0\n", "tag": "v1.0.0\n"}))
    assert updater.check_for_updates()["message"] == "MGM Network Vault is up to date."

    def boom(args, **kw):
        raise OSError("git missing")
    monkeypatch.setattr(updater, "_git", boom)
    r = updater.check_for_updates()
    assert (r["ok"], r["is_git"], r["update_available"], r["error"]) == (False, True, False, "git missing")
