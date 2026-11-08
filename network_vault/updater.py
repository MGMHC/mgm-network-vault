"""Application updater: check for and apply updates from the GitHub repository."""
import os
import shutil
import subprocess
import sys
import threading
import time

from . import config

from .models import log_event
from .util import NO_WINDOW
from .version import __version__



def is_git_installation() -> bool:
    """Return True if the application is running inside a Git working copy."""
    git = shutil.which("git")
    return bool(git and (config.APP_ROOT / ".git").is_dir())


def _git(args, timeout=10, **kwargs):
    """Run git in the app folder; returns the CompletedProcess (pass check=True to raise on failure)."""
    return subprocess.run(["git", "-c", "safe.directory=*", *args], cwd=config.APP_ROOT, capture_output=True,
                          text=True, timeout=timeout, creationflags=NO_WINDOW, **kwargs)


def check_for_updates(force_fetch: bool = True) -> dict:
    """Check if updates are available on GitHub remote.

    Returns dict with keys:
        ok (bool), is_git (bool), current_version (str),
        latest_tag (str), behind_count (int), update_available (bool),
        commits (list[str]), message (str), error (str|None)
    """
    result = {"ok": True, "is_git": True, "current_version": __version__, "latest_tag": f"v{__version__}",
              "behind_count": 0, "update_available": False, "commits": [], "message": "", "error": None}
    if not is_git_installation():
        return {**result, "is_git": False, "message": "Installed from a standalone package (not a Git repository)."}

    try:
        if force_fetch:
            _git(["fetch", "origin", "--tags"], timeout=20)
        behind_res = _git(["rev-list", "--count", "HEAD..origin/main"])
        behind = int(behind_res.stdout.strip() or "0") if behind_res.returncode == 0 else 0
        tags = [t.strip() for t in _git(["tag", "--sort=-v:refname"]).stdout.splitlines() if t.strip()]
        latest_tag = tags[0] if tags else result["latest_tag"]
        commits = []
        if behind > 0:
            log_res = _git(["log", "-n", "10", "--format=%h - %s (%cd)", "--date=short", "HEAD..origin/main"])
            if log_res.returncode == 0:
                commits = [l.strip() for l in log_res.stdout.splitlines() if l.strip()]
        update_available = behind > 0 or latest_tag.lstrip("v") != __version__
        msg = (f"{behind} new update commit(s) available ({latest_tag})." if update_available
               else "MGM Network Vault is up to date.")
        return {**result, "latest_tag": latest_tag, "behind_count": behind, "update_available": update_available,
                "commits": commits, "message": msg}
    except Exception as e:  # noqa: BLE001
        return {**result, "ok": False, "message": f"Failed to check for updates: {e}", "error": str(e)}


def apply_update() -> dict:
    """Pull the latest version from origin/main and trigger an application restart."""
    if not is_git_installation():
        return {"ok": False, "message": "Cannot auto-update: application is not running from a Git repository."}

    try:
        _git(["fetch", "origin", "--tags"], timeout=30, check=True)
        _git(["checkout", "main"], timeout=15, check=True)
        pull_res = _git(["pull", "--ff-only", "origin", "main"], timeout=30, check=True)
        if (config.APP_ROOT / "requirements.txt").exists():
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "--quiet", "--no-warn-script-location"],
                cwd=config.APP_ROOT, capture_output=True, text=True, timeout=120, creationflags=NO_WINDOW,
                check=True,  # restarting without a new dependency would crash the app on import
            )
        log_event(f"Application update applied successfully: {pull_res.stdout.strip()[:200]}", "system", "WARN")

        def restart_worker():
            time.sleep(1.5)  # Let response reach browser
            os._exit(3)      # start.bat / the service task restart the app on exit code 3

        threading.Thread(target=restart_worker, daemon=True, name="app-updater-restart").start()
        return {"ok": True, "message": "Update successfully applied! MGM Network Vault is restarting..."}
    except subprocess.CalledProcessError as e:
        err_msg = (e.stderr or e.stdout or str(e)).strip()
        log_event(f"Application update failed: {err_msg}", "system", "ERROR")
        return {"ok": False, "message": f"Update failed: {err_msg}"}
    except Exception as e:  # noqa: BLE001
        log_event(f"Application update error: {e}", "system", "ERROR")
        return {"ok": False, "message": f"Failed to apply update: {e}"}
