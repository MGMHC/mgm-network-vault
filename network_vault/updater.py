"""Application updater: check for and apply updates from the GitHub repository."""
import os
import shutil
import subprocess
import sys
import threading
import time

from . import config
from .util import log_event
from .version import __version__

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW


def is_git_installation() -> bool:
    """Return True if the application is running inside a Git working copy."""
    git = shutil.which("git")
    return bool(git and (config.APP_ROOT / ".git").is_dir())


def _git(args, **kwargs):
    flags = kwargs.pop("creationflags", _NO_WINDOW)
    return subprocess.run(["git", "-c", "safe.directory=*", *args], creationflags=flags, **kwargs)


def check_for_updates(force_fetch: bool = True) -> dict:
    """Check if updates are available on GitHub remote.

    Returns dict with keys:
        ok (bool), is_git (bool), current_version (str),
        latest_tag (str), behind_count (int), update_available (bool),
        commits (list[str]), message (str), error (str|None)
    """
    if not is_git_installation():
        return {
            "ok": True,
            "is_git": False,
            "current_version": __version__,
            "latest_tag": f"v{__version__}",
            "behind_count": 0,
            "update_available": False,
            "commits": [],
            "message": "Installed from a standalone package (not a Git repository).",
            "error": None,
        }

    try:
        if force_fetch:
            _git(
                ["fetch", "origin", "--tags"],
                cwd=config.APP_ROOT,
                capture_output=True,
                text=True,
                timeout=20,
            )

        # Count commits behind origin/main
        behind_res = _git(
            ["rev-list", "--count", "HEAD..origin/main"],
            cwd=config.APP_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        behind = int(behind_res.stdout.strip() or "0") if behind_res.returncode == 0 else 0

        # Latest tag on remote
        tags_res = _git(
            ["tag", "--sort=-v:refname"],
            cwd=config.APP_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        tags = [t.strip() for t in tags_res.stdout.splitlines() if t.strip()]
        latest_tag = tags[0] if tags else f"v{__version__}"

        # Get summary of new commits if behind
        commits = []
        if behind > 0:
            log_res = _git(
                ["log", "-n", "10", "--format=%h - %s (%cd)", "--date=short", "HEAD..origin/main"],
                cwd=config.APP_ROOT,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if log_res.returncode == 0 and log_res.stdout.strip():
                commits = [l.strip() for l in log_res.stdout.splitlines() if l.strip()]

        update_available = (behind > 0) or (latest_tag.lstrip("v") != __version__)
        msg = f"{behind} new update commit(s) available ({latest_tag})." if update_available else "MGM Network Vault is up to date."

        return {
            "ok": True,
            "is_git": True,
            "current_version": __version__,
            "latest_tag": latest_tag,
            "behind_count": behind,
            "update_available": update_available,
            "commits": commits,
            "message": msg,
            "error": None,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "is_git": True,
            "current_version": __version__,
            "latest_tag": f"v{__version__}",
            "behind_count": 0,
            "update_available": False,
            "commits": [],
            "message": f"Failed to check for updates: {e}",
            "error": str(e),
        }


def apply_update() -> dict:
    """Pull the latest version from origin/main and trigger an application restart."""
    if not is_git_installation():
        return {
            "ok": False,
            "message": "Cannot auto-update: application is not running from a Git repository.",
        }

    try:
        # 1. Fetch latest commits and tags
        _git(
            ["fetch", "origin", "--tags"],
            cwd=config.APP_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )

        # 2. Checkout main branch and pull changes
        _git(
            ["checkout", "main"],
            cwd=config.APP_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        pull_res = _git(
            ["pull", "--ff-only", "origin", "main"],
            cwd=config.APP_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )

        # 3. Check/update dependencies if requirements exist
        req_file = config.APP_ROOT / "requirements.txt"
        if req_file.exists():
            py_exec = sys.executable
            subprocess.run(
                [py_exec, "-m", "pip", "install", "-r", "requirements.txt", "--quiet", "--no-warn-script-location"],
                cwd=config.APP_ROOT,
                capture_output=True,
                text=True,
                timeout=120,
                creationflags=_NO_WINDOW,
            )

        log_event(f"Application update applied successfully: {pull_res.stdout.strip()[:200]}", "system", "WARN")

        # 4. Schedule graceful restart
        def restart_worker():
            time.sleep(1.5)  # Let response reach browser
            os._exit(3)      # start.bat / the service task restart the app on exit code 3

        threading.Thread(target=restart_worker, daemon=True, name="app-updater-restart").start()

        return {
            "ok": True,
            "message": "Update successfully applied! MGM Network Vault is restarting...",
        }
    except subprocess.CalledProcessError as e:
        err_msg = (e.stderr or e.stdout or str(e)).strip()
        log_event(f"Application update failed: {err_msg}", "system", "ERROR")
        return {"ok": False, "message": f"Git update failed: {err_msg}"}
    except Exception as e:  # noqa: BLE001
        log_event(f"Application update error: {e}", "system", "ERROR")
        return {"ok": False, "message": f"Failed to apply update: {e}"}
