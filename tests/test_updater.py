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
