"""Mirror switch backups into a (private) GitHub repository with full version history.

Layout of the repository:

    README.md
    devices/<device>/config.conf    running configuration (Junos hierarchical / Cisco running-config)
    devices/<device>/config.set     Junos "display set" format
    devices/<device>/info.txt       version, hardware, VC members, commit history

Files are overwritten in place, so `git log devices/<device>` is the version history of a switch.
Every configuration change becomes one commit dated with the backup time; every backup run that
changed something gets an annotated tag `backup/YYYY-MM-DD_HHMMSS`. The local repository under
data/git-repo is only a working copy - the database + local backups and GitHub are the sources of truth.
"""
import base64
import os
import re
import shutil
import subprocess
import threading
from datetime import timedelta
from pathlib import Path

from .models import Backup, Device, db, get_setting, now, set_setting
from .util import BASE_DIR, decrypt, encrypt, log_event, read_backup_file, safe_name

REPO_DIR = BASE_DIR / "git-repo"
_lock = threading.RLock()
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
_safe_dir_registered = False  # set to True once REPO_DIR is added to safe.directory

GIT_DEFAULTS = {
    "git_enabled": "0",
    "git_url": "",
    "git_branch": "main",
    "git_token_enc": "",
    "git_author_name": "MGM Network Vault",
    "git_author_email": "network-vault@mgmhealthcare.local",
    "git_redact": "1",      # mask password hashes / SNMP communities / RADIUS keys in the GitHub copy
    "git_tag_runs": "1",    # annotated tag per backup run that changed something
    "git_last_push": "",
    "git_last_error": "",
}

# Runtime state for the UI (initial history import runs in the background)
progress = {"busy": False, "message": "", "done": 0, "total": 0}


class GitError(RuntimeError):
    pass


def setting(key):
    v = get_setting(key)
    return GIT_DEFAULTS[key] if v is None else v


def is_enabled():
    return setting("git_enabled") == "1" and bool(setting("git_url"))


# --------------------------------------------------------------------------- git plumbing

def _auth_header():
    token = decrypt(setting("git_token_enc"))
    if not token:
        return None
    return "Authorization: Basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()


def _scrub(text):
    token = decrypt(setting("git_token_enc"))
    hdr = _auth_header()
    for secret in filter(None, [token, hdr, hdr and hdr.split()[-1]]):
        text = text.replace(secret, "***")
    return text


def _ensure_safe_directory():
    """Register REPO_DIR as a Git safe.directory (idempotent, once per process).

    On Windows it is common for the git-repo directory to be owned by a different
    account than the one running the service (e.g. SYSTEM vs a domain user).
    Git 2.35.2+ refuses to operate on such directories unless they are explicitly
    listed under safe.directory in the system or global config. We attempt both
    --system (works when running under LocalSystem / admin service) and --global
    (works when running under an interactive user profile).
    """
    global _safe_dir_registered
    if _safe_dir_registered:
        return
    path = REPO_DIR.as_posix()  # git expects forward-slash paths even on Windows
    for scope in ("--system", "--global"):
        try:
            subprocess.run(
                ["git", "config", scope, "--add", "safe.directory", path],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=_NO_WINDOW,
            )
        except Exception:
            pass
    _safe_dir_registered = True


def git(*args, auth=False, check=True, date=None, timeout=180):
    _ensure_safe_directory()
    env = os.environ.copy()
    env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", LC_ALL="C")
    # config via environment keeps the token off the process command line
    cfg = [("credential.helper", ""), ("core.autocrlf", "false"), ("core.quotepath", "off"),
           ("safe.directory", "*"),
           ("user.name", setting("git_author_name")), ("user.email", setting("git_author_email"))]
    if auth and _auth_header():
        cfg.append(("http.extraHeader", _auth_header()))
    env["GIT_CONFIG_COUNT"] = str(len(cfg))
    for i, (k, v) in enumerate(cfg):
        env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = k, v
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date.strftime("%Y-%m-%dT%H:%M:%S")
    cmd = ["git", "-c", "safe.directory=*", *args]
    try:
        r = subprocess.run(cmd, cwd=REPO_DIR, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, creationflags=_NO_WINDOW)
    except FileNotFoundError as e:
        raise GitError("Git is not installed on this server (https://git-scm.com/download/win)") from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {args[0]} timed out after {timeout}s") from e
    if check and r.returncode != 0:
        raise GitError(_scrub((r.stderr or r.stdout).strip()) or f"git {args[0]} failed ({r.returncode})")
    return r.stdout.strip() if check else r


def web_url():
    m = re.match(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", setting("git_url") or "")
    return f"https://github.com/{m.group(1)}/{m.group(2)}" if m else None


def commit_url(sha):
    base = web_url()
    return f"{base}/commit/{sha}" if base and sha else None


def device_url(device_name):
    base = web_url()
    return f"{base}/commits/{setting('git_branch')}/devices/{safe_name(device_name)}" if base else None


# --------------------------------------------------------------------------- secret masking

_JUNOS_RULES = [
    (re.compile(r'"\$\d+\$[^"]*"'), '"<masked>"'),                         # $9$ / $1$ / $5$ / $6$ secrets
    (re.compile(r'(\bcommunity\s+)("[^"]*"|[^\s{;]+)'), r"\1<masked>"),    # SNMP community names
]
_CISCO_RULES = [
    (re.compile(r'^(\s*enable\s+(?:secret|password)\s+(?:level\s+\d+\s+)?(?:\d+\s+|encrypted\s+)?)\S+', re.M), r"\1<masked>"),
    (re.compile(r'^(\s*username\s+\S+\s.*?\b(?:secret|password)\s+(?:\d+\s+|encrypted\s+)?)\S+', re.M), r"\1<masked>"),
    (re.compile(r'(\bsnmp-server\s+community\s+)("[^"]*"|\S+)'), r"\1<masked>"),
    (re.compile(r'(\bsnmp-server\s+user\b.*?\b(?:auth|priv)\s+(?:md5|sha\d*|des|aes\d*)?\s*)(\S+)'), r"\1<masked>"),
    (re.compile(r'(\bpriv\s+(?:des|aes\d*)?\s*)(?!<masked>)(\S+)(?=\s*$)', re.M), r"\1<masked>"),
    (re.compile(r'(\b(?:radius|tacacs)-server\b.*?\bkey\s+(?:\d+\s+|encrypted\s+)?)\S+'), r"\1<masked>"),
    (re.compile(r'^(\s*(?:key|password|secret)\s+(?:\d+\s+|encrypted\s+)?)\S+', re.M), r"\1<masked>"),
    (re.compile(r'(\bencrypted\s+)[A-Za-z0-9+/=$.]{16,}'), r"\1<masked>"),
]
_VOLATILE_INFO = re.compile(r"(uptime is|system up ?time|^\s*uptime)", re.I)


def mask(text, juniper):
    for rx, repl in (_JUNOS_RULES if juniper else _CISCO_RULES):
        text = rx.sub(repl, text)
    return text


# --------------------------------------------------------------------------- repository management

README = """# MGM Network Vault - switch configuration archive

Configuration backups of MGM Healthcare network switches, maintained automatically by
**MGM Network Vault**. Do not edit by hand - changes are overwritten on the next backup.

| Path | Content |
|---|---|
| `devices/<switch>/config.conf` | Running configuration (Junos hierarchical / Cisco running-config) |
| `devices/<switch>/config.set` | Junos `display set` format - best for reading diffs |
| `devices/<switch>/info.txt` | Software version, hardware, Virtual Chassis members, commit history |

## Versioning

* Every configuration change is **one commit**, dated with the time of the backup. The commit
  message says how many lines changed, what triggered the backup and the last commit on the switch.
* Every backup run that changed something is tagged `backup/YYYY-MM-DD_HHMMSS`, so you can check out
  the state of the whole network at that moment.
* History of one switch: open its folder and click *History*, or `git log -p devices/<switch>`.
{masked}
"""
_MASK_NOTE = """
## Masked secrets

Password hashes, SNMP communities and RADIUS/TACACS keys are replaced with `<masked>` in this
repository. Complete, restorable copies are kept by MGM Network Vault on the backup server.
"""


def _has_commits():
    return git("rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0


def head():
    with _lock:
        if not (REPO_DIR / ".git").exists() or not _has_commits():
            return None
        return git("rev-parse", "HEAD")


def _init_local():
    branch = setting("git_branch") or "main"
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    if not (REPO_DIR / ".git").exists():
        git("init", "-q", "-b", branch)
    url = setting("git_url")
    if git("remote", check=False).stdout.split().count("origin"):
        git("remote", "set-url", "origin", url)
    else:
        git("remote", "add", "origin", url)


def _write_static_files():
    masked = setting("git_redact") == "1"
    (REPO_DIR / "README.md").write_text(README.format(masked=_MASK_NOTE if masked else ""), encoding="utf-8")
    (REPO_DIR / ".gitattributes").write_text("* text eol=lf\n", encoding="utf-8")


def _commit(message, date=None):
    """Commit staged changes. Returns the new sha, or None when there was nothing to commit."""
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return None
    git("commit", "-q", "-m", message, date=date)
    return git("rev-parse", "HEAD")


def _write_device_files(device, backup):
    folder = REPO_DIR / "devices" / safe_name(device.name)
    folder.mkdir(parents=True, exist_ok=True)
    masked = setting("git_redact") == "1"
    for rel, name in ((backup.path, "config.conf"), (backup.set_path, "config.set"), (backup.info_path, "info.txt")):
        target = folder / name
        if not rel:
            if target.exists():
                target.unlink()
            continue
        text = read_backup_file(rel)
        if name == "info.txt":
            text = "\n".join(l for l in text.splitlines() if not _VOLATILE_INFO.search(l)) + "\n"
        elif masked:
            text = mask(text, device.is_juniper)
        target.write_text(text, encoding="utf-8", newline="\n")
    git("add", "-A", "--", f"devices/{safe_name(device.name)}")


def _message(device, backup):
    if backup.changed:
        head_line = f"{device.name}: configuration changed (+{backup.lines_added} -{backup.lines_removed})"
    elif backup.previous is None:
        head_line = f"{device.name}: first backup"
    else:
        head_line = f"{device.name}: device info updated"
    body = [
        f"Device:  {device.name} ({device.host}) {device.hw_model or device.model or ''}"
        f"{', ' + device.sw_version if device.sw_version else ''}".rstrip(),
        f"Trigger: {backup.trigger}",
        f"Backup:  {backup.created_at:%Y-%m-%d %H:%M:%S} (#{backup.id})",
    ]
    if backup.last_commit:
        body.append(f"Last commit on switch: {backup.last_commit}")
    return head_line + "\n\n" + "\n".join(body)


def record(backup_id):
    """Commit one stored backup (called right after a backup). Never raises."""
    if not is_enabled() or progress["busy"]:
        return None
    try:
        with _lock:
            if not (REPO_DIR / ".git").exists():
                return None  # not initialised yet - "Sync now" will pick it up
            b = db.session.get(Backup, backup_id)
            if not b:
                return None
            _write_device_files(b.device, b)
            sha = _commit(_message(b.device, b), date=b.created_at)
            if sha:
                b.git_commit = sha
                db.session.commit()
            return sha
    except Exception as e:  # noqa: BLE001 - git trouble must never break a backup
        _fail(f"Git commit failed for backup #{backup_id}: {e}")
        return None


def tag_run(job, head_before):
    """Annotated tag for a backup run that produced commits."""
    if not is_enabled() or setting("git_tag_runs") != "1":
        return
    try:
        with _lock:
            new_head = head()
            if not new_head or new_head == head_before:
                return
            rng = f"{head_before}..HEAD" if head_before else "HEAD"
            changed = sorted({p.split("/")[1] for p in git("diff", "--name-only", rng, "--", "devices").splitlines()
                              if p.count("/") >= 2}) if head_before else []
            name = f"backup/{job.started:%Y-%m-%d_%H%M%S}"
            msg = (f"Backup run #{job.id} ({job.trigger}): {job.ok} ok, {job.failed} failed of {job.total}\n\n"
                   + ("Changed: " + ", ".join(changed) if changed else ""))
            git("tag", "-a", "-f", name, "-m", msg, date=job.started)
    except Exception as e:  # noqa: BLE001
        _fail(f"Git tag failed: {e}")


def remove_device(name):
    _device_change(lambda: git("rm", "-r", "-q", "--ignore-unmatch", "--", f"devices/{safe_name(name)}"),
                   f"{name}: device removed from inventory (history kept)")


def rename_device(old, new):
    def op():
        src = REPO_DIR / "devices" / safe_name(old)
        if src.exists() and safe_name(old) != safe_name(new):
            git("mv", "--", f"devices/{safe_name(old)}", f"devices/{safe_name(new)}")
    _device_change(op, f"{new}: renamed from {old}")


def _device_change(op, message):
    if not is_enabled():
        return
    from flask import current_app
    app = current_app._get_current_object()
    try:
        with _lock:
            if not (REPO_DIR / ".git").exists():
                return
            op()
            if _commit(message):
                threading.Thread(target=_push_in_app, args=[app], daemon=True).start()
    except Exception as e:  # noqa: BLE001
        _fail(f"Git update failed: {e}")


def _fail(msg):
    set_setting("git_last_error", f"{now():%Y-%m-%d %H:%M:%S} {msg}"[:500])
    log_event(msg[:500], "git", "ERROR", user="system")


def ahead():
    """Number of local commits not yet on GitHub."""
    with _lock:
        if not (REPO_DIR / ".git").exists() or not _has_commits():
            return 0
        ref = f"refs/remotes/origin/{setting('git_branch')}"
        if git("rev-parse", "--verify", "-q", ref, check=False).returncode != 0:
            return int(git("rev-list", "--count", "HEAD"))
        return int(git("rev-list", "--count", f"{ref}..HEAD"))


def push(raise_errors=False):
    if not is_enabled():
        return False
    try:
        with _lock:
            if not (REPO_DIR / ".git").exists() or not _has_commits():
                return False
            branch = setting("git_branch")
            git("push", "-q", "--follow-tags", "origin", f"HEAD:refs/heads/{branch}", auth=True)
            git("fetch", "-q", "origin", branch, auth=True, check=False)  # refresh origin/<branch> for "ahead"
        set_setting("git_last_push", f"{now():%Y-%m-%d %H:%M:%S}")
        set_setting("git_last_error", "")
        db.session.commit()
        return True
    except Exception as e:  # noqa: BLE001
        _fail(f"Push to GitHub failed: {e}")
        if raise_errors:
            raise
        return False


def _push_in_app(app):
    with app.app_context():
        push()


def retry_push():
    """Scheduler hook: push commits that could not be pushed earlier (GitHub/network down)."""
    if is_enabled() and not progress["busy"] and ahead():
        push()


# --------------------------------------------------------------------------- setup / full sync

def test_connection():
    """Check URL + token can reach the repository. Returns a human readable result."""
    with _lock:
        REPO_DIR.mkdir(parents=True, exist_ok=True)
        if not (REPO_DIR / ".git").exists():
            _init_local()
        out = git("ls-remote", "--heads", setting("git_url"), auth=True, timeout=60)
    branch = setting("git_branch")
    if f"refs/heads/{branch}" in out:
        return f"Connected. Branch '{branch}' exists on GitHub."
    return f"Connected. Repository is reachable; branch '{branch}' will be created on first push."


def reset_local():
    """Forget the local working copy (used when the repository URL or branch changes)."""
    with _lock:
        if REPO_DIR.exists():
            def onerr(func, path, _exc):  # git marks pack files read-only on Windows
                os.chmod(path, 0o666)
                func(path)
            shutil.rmtree(REPO_DIR, onexc=onerr)


def full_sync(app, user=None):
    """Connect the repository and bring it up to date.

    * New/empty repository: replays every stored backup in chronological order, so the
      repository starts with the complete history (one dated commit per change).
    * Existing repository: commits the latest backup of every device if it differs.
    Runs in a background thread; progress is shown on the GitHub page.
    """
    if progress["busy"]:
        return False

    def run():
        progress.update(busy=True, message="Connecting to GitHub…", done=0, total=0)
        with app.app_context():
            try:
                with _lock:
                    _init_local()
                    branch = setting("git_branch")
                    remote = git("ls-remote", "--heads", "origin", branch, auth=True, timeout=60)
                    if remote and not _has_commits():
                        progress["message"] = "Downloading existing repository…"
                        git("fetch", "-q", "origin", branch, auth=True, timeout=600)
                        git("reset", "-q", "--hard", f"origin/{branch}")
                    elif remote:
                        git("fetch", "-q", "origin", branch, auth=True, timeout=600)
                        if git("merge-base", "--is-ancestor", f"origin/{branch}", "HEAD", check=False).returncode != 0:
                            raise GitError(f"GitHub branch '{branch}' has commits that are not in the local copy. "
                                           "If someone pushed to it by hand, use 'Re-download from GitHub'.")
                    fresh = not (REPO_DIR / "devices").exists()
                    first = Backup.query.order_by(Backup.created_at).first() if fresh else None
                    _write_static_files()
                    git("add", "README.md", ".gitattributes")
                    # dated before the imported history so the timeline reads in order
                    _commit("Repository maintained by MGM Network Vault",
                            date=first.created_at - timedelta(seconds=1) if first else None)

                    if fresh:
                        backups = Backup.query.order_by(Backup.created_at, Backup.id).all()
                        progress.update(total=len(backups), message="Importing backup history…")
                        for i, b in enumerate(backups, 1):
                            _write_device_files(b.device, b)
                            sha = _commit(_message(b.device, b), date=b.created_at)
                            if sha:
                                b.git_commit = sha
                            progress["done"] = i
                            if i % 50 == 0:
                                db.session.commit()
                        db.session.commit()
                        summary = f"imported history of {len(backups)} backup(s)"
                    else:
                        devices = Device.query.all()
                        progress.update(total=len(devices), message="Syncing latest backups…")
                        n = 0
                        for i, d in enumerate(devices, 1):
                            b = d.backups.first()
                            if b:
                                _write_device_files(d, b)
                                sha = _commit(_message(d, b), date=b.created_at)
                                if sha:
                                    b.git_commit, n = sha, n + 1
                            progress["done"] = i
                        db.session.commit()
                        summary = f"{n} device(s) updated"
                progress["message"] = "Pushing to GitHub…"
                push(raise_errors=True)
                log_event(f"GitHub sync complete: {summary}", "git", user=user or "system")
                progress["message"] = f"Done - {summary}."
            except Exception as e:  # noqa: BLE001
                _fail(f"GitHub sync failed: {e}")
                progress["message"] = f"Failed: {_scrub(str(e))}"
            finally:
                progress["busy"] = False

    threading.Thread(target=run, daemon=True, name="git-sync").start()
    return True


def recent_commits(limit=25):
    with _lock:
        if not (REPO_DIR / ".git").exists() or not _has_commits():
            return []
        out = git("log", f"-{limit}", "--date=format:%Y-%m-%d %H:%M", "--format=%H%x1f%ad%x1f%s")
    rows = []
    for line in out.splitlines():
        sha, date, subject = line.split("\x1f", 2)
        rows.append({"sha": sha, "short": sha[:8], "date": date, "subject": subject, "url": commit_url(sha)})
    return rows


def recent_tags(limit=10):
    with _lock:
        if not (REPO_DIR / ".git").exists() or not _has_commits():
            return []
        out = git("tag", "-l", "backup/*", "--sort=-creatordate", "--format=%(refname:short)%1f%(contents:subject)")
    base = web_url()
    rows = []
    for line in out.splitlines()[:limit]:
        name, _, subject = line.partition("\x1f")
        rows.append({"name": name, "subject": subject, "url": f"{base}/tree/{name}" if base else None})
    return rows


def save_settings(form, user=None):
    """Persist the GitHub settings form. Returns True when the local copy had to be reset."""
    old_url, old_branch = setting("git_url"), setting("git_branch")
    url = form.get("git_url", "").strip()
    url = re.sub(r"^https://[^@/]+@", "https://", url)  # never store credentials inside the URL
    if url and not url.startswith("https://"):
        raise ValueError("Use the HTTPS address of the repository, e.g. https://github.com/mgm-it/switch-configs.git")
    if url and not url.endswith(".git"):
        url += ".git"
    branch = form.get("git_branch", "main").strip() or "main"
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
        raise ValueError("Invalid branch name")
    set_setting("git_url", url)
    set_setting("git_branch", branch)
    set_setting("git_author_name", form.get("git_author_name", "").strip() or GIT_DEFAULTS["git_author_name"])
    set_setting("git_author_email", form.get("git_author_email", "").strip() or GIT_DEFAULTS["git_author_email"])
    for key in ("git_enabled", "git_redact", "git_tag_runs"):
        set_setting(key, "1" if form.get(key) in ("on", "1") else "0")
    if form.get("git_token"):
        set_setting("git_token_enc", encrypt(form["git_token"].strip()))
    db.session.commit()
    if (old_url and old_url != url) or (old_branch and old_branch != branch):
        reset_local()
        return True
    return False


def init(app):
    Path(REPO_DIR).parent.mkdir(parents=True, exist_ok=True)
