"""Mirror switch backups into a (private) GitHub repository with full version history.

Per-site branch architecture
------------------------------
Every site gets its own branch on GitHub:

    MGMHC          → branch MGMHC       (MGM Healthcare – main site)
    MGM-Malar      → branch MGM-Malar
    MGM-CORP       → branch MGM-CORP
    MGMCI          → branch MGMCI
    MGM-Sevenhills → branch MGM-Sevenhills

Local working copies live in  data/git-repos/<site-code>/  – one per site.
Using separate directories (instead of branch-switching in a single clone) eliminates
race conditions when concurrent backup threads from different sites commit simultaneously.

Layout of each branch:

    README.md
    devices/<device>/config.conf    running configuration (Junos hierarchical / Cisco running-config)
    devices/<device>/config.set     Junos "display set" format
    devices/<device>/info.txt       version, hardware, VC members, commit history

Files are overwritten in place so `git log devices/<device>` is the full version history.
Every configuration change becomes one commit dated with the backup time; every backup run
that changed something gets an annotated tag `backup/YYYY-MM-DD_HHMMSS`.
"""
import base64
import os
import re
import shutil
import subprocess
import threading
from datetime import timedelta
from pathlib import Path

from .models import Backup, Device, Site, db, get_setting, now, set_setting
from .util import BASE_DIR, decrypt, encrypt, log_event, read_backup_file, safe_name

# ──────────────────────────────────────────────────────────────────── paths ──

REPOS_BASE = BASE_DIR / "git-repos"        # per-site local working copies live here
REPO_DIR   = REPOS_BASE / "MGMHC"          # backward-compat alias → default site repo

# ────────────────────────────────────────────────────────── locking / state ──

_lock = threading.RLock()                           # guards _site_locks dict
_site_locks: dict = {}
_safe_dirs_registered: set = set()
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW on Windows

# ─────────────────────────────────────────────────────────────── settings ───

GIT_DEFAULTS = {
    "git_enabled":      "0",
    "git_url":          "",
    "git_branch":       "main",            # legacy/fallback – not used per-site
    "git_token_enc":    "",
    "git_author_name":  "MGM Network Vault",
    "git_author_email": "network-vault@mgmhealthcare.local",
    "git_redact":       "1",               # mask secrets in the GitHub copy
    "git_tag_runs":     "1",               # annotated tag per backup run
    "git_last_push":    "",
    "git_last_error":   "",
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


# ─────────────────────────────────────────────────── per-site helpers ───────

def _safe_code(code: str) -> str:
    """Sanitise a site code into a valid git branch / directory name."""
    return re.sub(r"[^A-Za-z0-9._/-]+", "-", code.strip())


def _site_repo(site_code: str) -> Path:
    """Local working-copy directory for a site."""
    return REPOS_BASE / _safe_code(site_code)


def _get_site_lock(site_code: str) -> threading.RLock:
    with _lock:
        if site_code not in _site_locks:
            _site_locks[site_code] = threading.RLock()
        return _site_locks[site_code]


def _all_initialized_sites() -> list:
    """Site codes that have a local git repo initialized under REPOS_BASE."""
    if not REPOS_BASE.exists():
        return []
    return [d.name for d in REPOS_BASE.iterdir()
            if d.is_dir() and (d / ".git").exists()]


def _device_site_code(device) -> str:
    """Return the site code for a Device object (may be None)."""
    if device and getattr(device, "site", None) and device.site.code:
        return device.site.code
    return None


# ─────────────────────────────────────────────────────── git plumbing ───────

def _ensure_safe_directory(repo_dir: Path):
    key = str(repo_dir)
    if key in _safe_dirs_registered:
        return
    path = repo_dir.as_posix()
    for scope in ("--system", "--global"):
        try:
            subprocess.run(
                ["git", "config", scope, "--add", "safe.directory", path],
                capture_output=True, text=True, timeout=10,
                creationflags=_NO_WINDOW,
            )
        except Exception:
            pass
    _safe_dirs_registered.add(key)


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


def git(*args, auth=False, check=True, date=None, timeout=180, repo_dir=None):
    """Run a git command inside the given (or default) repo directory."""
    rd = Path(repo_dir) if repo_dir else REPO_DIR
    _ensure_safe_directory(rd)
    env = os.environ.copy()
    env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", LC_ALL="C")
    cfg = [
        ("credential.helper", ""),
        ("core.autocrlf",     "false"),
        ("core.quotepath",    "off"),
        ("safe.directory",    "*"),
        ("user.name",         setting("git_author_name")),
        ("user.email",        setting("git_author_email")),
    ]
    if auth and _auth_header():
        cfg.append(("http.extraHeader", _auth_header()))
    env["GIT_CONFIG_COUNT"] = str(len(cfg))
    for i, (k, v) in enumerate(cfg):
        env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = k, v
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date.strftime("%Y-%m-%dT%H:%M:%S")
    cmd = ["git", "-c", "safe.directory=*", *args]
    try:
        r = subprocess.run(cmd, cwd=rd, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=_NO_WINDOW)
    except FileNotFoundError as e:
        raise GitError("Git is not installed on this server (https://git-scm.com/download/win)") from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {args[0]} timed out after {timeout}s") from e
    if check and r.returncode != 0:
        raise GitError(_scrub((r.stderr or r.stdout).strip()) or f"git {args[0]} failed ({r.returncode})")
    return r.stdout.strip() if check else r


# ─────────────────────────────────────────────────────────── URL helpers ────

def web_url():
    m = re.match(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", setting("git_url") or "")
    return f"https://github.com/{m.group(1)}/{m.group(2)}" if m else None


def commit_url(sha):
    base = web_url()
    return f"{base}/commit/{sha}" if base and sha else None


def device_url(device):
    """Return the GitHub history URL for a device, on that device's site branch."""
    base = web_url()
    if not base:
        return None
    if isinstance(device, str):
        name = device
        branch = setting("git_branch") or "main"
    else:
        name = device.name
        site_code = _device_site_code(device)
        branch = _safe_code(site_code) if site_code else (setting("git_branch") or "main")
    return f"{base}/commits/{branch}/devices/{safe_name(name)}"


# ──────────────────────────────────────────────────────── secret masking ────

_JUNOS_RULES = [
    (re.compile(r'"\\$\\d+\\$[^"]*"'), '"<masked>"'),                         # $9$ / $1$ / $5$ / $6$ secrets
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


# ─────────────────────────────────────────────── repository management ──────

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


def _has_commits(repo_dir=None):
    rd = repo_dir or REPO_DIR
    return git("rev-parse", "--verify", "-q", "HEAD", check=False, repo_dir=rd).returncode == 0


def head(site_code=None):
    """Return HEAD sha for one site repo, or a dict {site_code: sha} for all initialized sites."""
    if site_code:
        rd = _site_repo(site_code)
        lock = _get_site_lock(site_code)
        with lock:
            if not (rd / ".git").exists() or not _has_commits(rd):
                return None
            return git("rev-parse", "HEAD", repo_dir=rd)
    # Return dict of all initialized sites
    result = {}
    for sc in _all_initialized_sites():
        result[sc] = head(sc)
    return result


def _init_site_repo(site_code: str):
    """Ensure site's local repo exists, is initialized, and has the correct remote."""
    rd = _site_repo(site_code)
    branch = _safe_code(site_code)
    rd.mkdir(parents=True, exist_ok=True)
    if not (rd / ".git").exists():
        git("init", "-q", "-b", branch, repo_dir=rd)
    url = setting("git_url")
    remotes = git("remote", check=False, repo_dir=rd).stdout.split()
    if "origin" in remotes:
        git("remote", "set-url", "origin", url, repo_dir=rd)
    else:
        git("remote", "add", "origin", url, repo_dir=rd)


def _write_static_files(repo_dir: Path):
    masked = setting("git_redact") == "1"
    (repo_dir / "README.md").write_text(README.format(masked=_MASK_NOTE if masked else ""), encoding="utf-8")
    (repo_dir / ".gitattributes").write_text("* text eol=lf\n", encoding="utf-8")


def _commit(message, date=None, repo_dir=None):
    """Stage-and-commit. Returns the new sha, or None if nothing to commit."""
    rd = repo_dir or REPO_DIR
    if git("diff", "--cached", "--quiet", check=False, repo_dir=rd).returncode == 0:
        return None
    git("commit", "-q", "-m", message, date=date, repo_dir=rd)
    return git("rev-parse", "HEAD", repo_dir=rd)


def _write_device_files(device, backup, repo_dir: Path):
    folder = repo_dir / "devices" / safe_name(device.name)
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
    git("add", "-A", "--", f"devices/{safe_name(device.name)}", repo_dir=repo_dir)


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
    """Commit one stored backup into its site's branch. Never raises."""
    if not is_enabled() or progress["busy"]:
        return None
    try:
        b = db.session.get(Backup, backup_id)
        if not b:
            return None
        site_code = _device_site_code(b.device)
        if not site_code:
            return None  # device not associated with a site
        rd = _site_repo(site_code)
        lock = _get_site_lock(site_code)
        with lock:
            if not (rd / ".git").exists():
                return None  # not initialised yet – "Sync now" will pick it up
            _write_device_files(b.device, b, rd)
            sha = _commit(_message(b.device, b), date=b.created_at, repo_dir=rd)
            if sha:
                b.git_commit = sha
                db.session.commit()
            return sha
    except Exception as e:  # noqa: BLE001
        _fail(f"Git commit failed for backup #{backup_id}: {e}")
        return None


def tag_run(job, head_before):
    """Annotated tag for a backup run that produced commits.

    head_before is a dict {site_code: sha} or None, as returned by head().
    """
    if not is_enabled() or setting("git_tag_runs") != "1":
        return
    name = f"backup/{job.started:%Y-%m-%d_%H%M%S}"
    base_msg = (f"Backup run #{job.id} ({job.trigger}): {job.ok} ok, {job.failed} failed of {job.total}\n\n")
    head_before = head_before or {}
    for site_code in _all_initialized_sites():
        try:
            rd = _site_repo(site_code)
            lock = _get_site_lock(site_code)
            with lock:
                if not _has_commits(rd):
                    continue
                new_head = git("rev-parse", "HEAD", repo_dir=rd)
                site_head_before = head_before.get(site_code)
                if not new_head or new_head == site_head_before:
                    continue
                rng = f"{site_head_before}..HEAD" if site_head_before else "HEAD"
                changed = sorted({p.split("/")[1] for p in
                                   git("diff", "--name-only", rng, "--", "devices", repo_dir=rd).splitlines()
                                   if p.count("/") >= 2}) if site_head_before else []
                msg = base_msg + ("Changed: " + ", ".join(changed) if changed else "")
                git("tag", "-a", "-f", name, "-m", msg, date=job.started, repo_dir=rd)
        except Exception as e:  # noqa: BLE001
            _fail(f"Git tag failed for {site_code}: {e}")


def remove_device(name, site_code=None):
    """Remove a device's folder from its site branch."""
    if not is_enabled():
        return
    # Try to infer site if not supplied
    if not site_code:
        d = Device.query.filter_by(name=name).first()
        if d:
            site_code = _device_site_code(d)
    if site_code:
        _device_change_site(
            site_code,
            lambda rd: git("rm", "-r", "-q", "--ignore-unmatch", "--", f"devices/{safe_name(name)}", repo_dir=rd),
            f"{name}: device removed from inventory (history kept)",
        )
    else:
        # Fallback: remove from all initialized site repos
        for sc in _all_initialized_sites():
            _device_change_site(
                sc,
                lambda rd: git("rm", "-r", "-q", "--ignore-unmatch", "--", f"devices/{safe_name(name)}", repo_dir=rd),
                f"{name}: device removed from inventory (history kept)",
            )


def rename_device(old, new, site_code=None):
    """Rename a device's folder in its site branch."""
    if not site_code:
        d = Device.query.filter_by(name=new).first()
        if d:
            site_code = _device_site_code(d)
    if site_code:
        def op(rd):
            src = rd / "devices" / safe_name(old)
            if src.exists() and safe_name(old) != safe_name(new):
                git("mv", "--", f"devices/{safe_name(old)}", f"devices/{safe_name(new)}", repo_dir=rd)
        _device_change_site(site_code, op, f"{new}: renamed from {old}")
    else:
        for sc in _all_initialized_sites():
            def op(rd, _sc=sc):
                src = rd / "devices" / safe_name(old)
                if src.exists() and safe_name(old) != safe_name(new):
                    git("mv", "--", f"devices/{safe_name(old)}", f"devices/{safe_name(new)}", repo_dir=rd)
            _device_change_site(sc, op, f"{new}: renamed from {old}")


def _device_change_site(site_code, op, message):
    from flask import current_app
    app = current_app._get_current_object()
    rd = _site_repo(site_code)
    lock = _get_site_lock(site_code)
    try:
        with lock:
            if not (rd / ".git").exists():
                return
            op(rd)
            if _commit(message, repo_dir=rd):
                threading.Thread(target=_push_site_in_app, args=[app, site_code], daemon=True).start()
    except Exception as e:  # noqa: BLE001
        _fail(f"Git update failed for {site_code}: {e}")


def _fail(msg):
    set_setting("git_last_error", f"{now():%Y-%m-%d %H:%M:%S} {msg}"[:500])
    log_event(msg[:500], "git", "ERROR", user="system")


def ahead():
    """Total number of local commits not yet pushed to GitHub, across all site repos."""
    total = 0
    for site_code in _all_initialized_sites():
        rd = _site_repo(site_code)
        lock = _get_site_lock(site_code)
        try:
            with lock:
                if not (rd / ".git").exists() or not _has_commits(rd):
                    continue
                branch = _safe_code(site_code)
                ref = f"refs/remotes/origin/{branch}"
                if git("rev-parse", "--verify", "-q", ref, check=False, repo_dir=rd).returncode != 0:
                    total += int(git("rev-list", "--count", "HEAD", repo_dir=rd))
                else:
                    total += int(git("rev-list", "--count", f"{ref}..HEAD", repo_dir=rd))
        except Exception:
            pass
    return total


def push(site_code=None, raise_errors=False):
    """Push one or all site repos to GitHub. Returns True if at least one push succeeded."""
    if not is_enabled():
        return False
    sites = [site_code] if site_code else _all_initialized_sites()
    ok = False
    for sc in sites:
        rd = _site_repo(sc)
        branch = _safe_code(sc)
        lock = _get_site_lock(sc)
        try:
            with lock:
                if not (rd / ".git").exists() or not _has_commits(rd):
                    continue
                git("push", "-q", "--follow-tags", "origin", f"HEAD:refs/heads/{branch}",
                    auth=True, repo_dir=rd)
                git("fetch", "-q", "origin", branch, auth=True, check=False, repo_dir=rd)
            ok = True
        except Exception as e:  # noqa: BLE001
            _fail(f"Push to GitHub failed for {sc}: {e}")
            if raise_errors:
                raise
    if ok:
        set_setting("git_last_push", f"{now():%Y-%m-%d %H:%M:%S}")
        set_setting("git_last_error", "")
        db.session.commit()
    return ok


def _push_site_in_app(app, site_code):
    with app.app_context():
        push(site_code)


def _push_in_app(app):
    with app.app_context():
        push()


def retry_push():
    """Scheduler hook: push any commits that couldn't be pushed earlier."""
    if is_enabled() and not progress["busy"] and ahead():
        push()


# ─────────────────────────────────────────────────── setup / full sync ───────

def test_connection():
    """Check URL + token can reach the remote. Returns a human-readable result."""
    url = setting("git_url")
    out = git("ls-remote", "--heads", url, auth=True, timeout=60, repo_dir=REPOS_BASE)
    initialized = _all_initialized_sites()
    branch_names = [_safe_code(sc) for sc in initialized] if initialized else [setting("git_branch") or "main"]
    found = [b for b in branch_names if f"refs/heads/{b}" in out]
    missing = [b for b in branch_names if b not in found]
    parts = []
    if found:
        parts.append(f"Branches on GitHub: {', '.join(found)}.")
    if missing:
        parts.append(f"Will be created on first push: {', '.join(missing)}.")
    if not parts:
        parts.append("Repository is reachable; branches will be created on first sync.")
    return "Connected. " + " ".join(parts)


def reset_local():
    """Delete all local site working copies (used when URL changes)."""
    with _lock:
        if REPOS_BASE.exists():
            def onerr(func, path, _exc):
                os.chmod(path, 0o666)
                func(path)
            shutil.rmtree(REPOS_BASE, onexc=onerr)
        # Also clean up old-style single repo directory
        old = BASE_DIR / "git-repo"
        if old.exists():
            def onerr2(func, path, _exc):
                os.chmod(path, 0o666)
                func(path)
            shutil.rmtree(old, onexc=onerr2)


def full_sync(app, user=None):
    """Connect/update per-site branches and bring them up to date.

    * New/empty site branch: replays every stored backup in chronological order.
    * Existing site branch: commits the latest backup of every device if it differs.
    Runs in a background thread; progress is shown on the GitHub page.
    """
    if progress["busy"]:
        return False

    def run():
        progress.update(busy=True, message="Connecting to GitHub…", done=0, total=0)
        with app.app_context():
            try:
                sites_with_devices = (
                    Site.query.join(Device, Device.site_id == Site.id).distinct().all()
                )
                if not sites_with_devices:
                    sites_with_devices = Site.query.all()

                grand_total = sum(
                    Backup.query.join(Device).filter(Device.site_id == s.id).count()
                    for s in sites_with_devices
                )
                progress.update(total=grand_total, message="Starting per-site sync…")

                all_summary = []
                done_count = 0

                for site in sites_with_devices:
                    sc = site.code
                    rd = _site_repo(sc)
                    branch = _safe_code(sc)
                    lock = _get_site_lock(sc)
                    progress["message"] = f"Syncing {sc}…"

                    with lock:
                        _init_site_repo(sc)

                        # Fetch / align with remote branch
                        remote = git("ls-remote", "--heads", "origin", branch,
                                     auth=True, timeout=60, repo_dir=rd)
                        if remote and not _has_commits(rd):
                            progress["message"] = f"Downloading {branch} from GitHub…"
                            git("fetch", "-q", "origin", branch, auth=True, timeout=600, repo_dir=rd)
                            git("reset", "-q", "--hard", f"origin/{branch}", repo_dir=rd)
                        elif remote and _has_commits(rd):
                            git("fetch", "-q", "origin", branch, auth=True, timeout=600, repo_dir=rd)
                            anc = git("merge-base", "--is-ancestor",
                                      f"origin/{branch}", "HEAD", check=False, repo_dir=rd)
                            if anc.returncode != 0:
                                raise GitError(
                                    f"Branch '{branch}' on GitHub has commits not in the local copy. "
                                    "Use 'Re-download from GitHub' to reset."
                                )

                        fresh = not (rd / "devices").exists()
                        site_backups = (
                            Backup.query
                            .join(Device)
                            .filter(Device.site_id == site.id)
                            .order_by(Backup.created_at, Backup.id)
                            .all()
                        )
                        first_backup = site_backups[0] if site_backups else None

                        _write_static_files(rd)
                        git("add", "README.md", ".gitattributes", repo_dir=rd)
                        _commit(
                            f"Repository maintained by MGM Network Vault ({site.name})",
                            date=first_backup.created_at - timedelta(seconds=1) if first_backup else None,
                            repo_dir=rd,
                        )

                        if fresh:
                            for b in site_backups:
                                _write_device_files(b.device, b, rd)
                                sha = _commit(_message(b.device, b), date=b.created_at, repo_dir=rd)
                                if sha:
                                    b.git_commit = sha
                                done_count += 1
                                progress["done"] = done_count
                            db.session.commit()
                            all_summary.append(f"{sc}: imported {len(site_backups)} backup(s)")
                        else:
                            n = 0
                            devices = site.devices.all()
                            for d in devices:
                                b = d.backups.first()
                                if b:
                                    _write_device_files(d, b, rd)
                                    sha = _commit(_message(d, b), date=b.created_at, repo_dir=rd)
                                    if sha:
                                        b.git_commit, n = sha, n + 1
                                done_count += 1
                                progress["done"] = done_count
                            db.session.commit()
                            all_summary.append(f"{sc}: {n} device(s) updated")

                progress["message"] = "Pushing all site branches to GitHub…"
                push(raise_errors=True)
                summary = "; ".join(all_summary) if all_summary else "nothing to sync"
                log_event(f"GitHub sync complete: {summary}", "git", user=user or "system")
                progress["message"] = f"Done – {summary}."
            except Exception as e:  # noqa: BLE001
                _fail(f"GitHub sync failed: {e}")
                progress["message"] = f"Failed: {_scrub(str(e))}"
            finally:
                progress["busy"] = False

    threading.Thread(target=run, daemon=True, name="git-sync").start()
    return True


def recent_commits(limit=25):
    """Recent commits from the largest / most-active initialized site repo."""
    # Pick the site with the most commits (or first found)
    best_rd = None
    best_count = -1
    for sc in _all_initialized_sites():
        rd = _site_repo(sc)
        if not _has_commits(rd):
            continue
        try:
            c = int(git("rev-list", "--count", "HEAD", repo_dir=rd))
            if c > best_count:
                best_count, best_rd = c, rd
        except Exception:
            pass
    if best_rd is None:
        return []
    out = git("log", f"-{limit}", "--date=format:%Y-%m-%d %H:%M", "--format=%H%x1f%ad%x1f%s",
              repo_dir=best_rd)
    rows = []
    for line in out.splitlines():
        sha, date, subject = line.split("\x1f", 2)
        rows.append({"sha": sha, "short": sha[:8], "date": date, "subject": subject, "url": commit_url(sha)})
    return rows


def recent_tags(limit=10):
    """Recent backup-run tags across all initialized site repos (merged & sorted)."""
    base = web_url()
    rows = []
    for sc in _all_initialized_sites():
        rd = _site_repo(sc)
        if not _has_commits(rd):
            continue
        try:
            branch = _safe_code(sc)
            out = git("tag", "-l", "backup/*", "--sort=-creatordate",
                      "--format=%(refname:short)%1f%(contents:subject)", repo_dir=rd)
            for line in out.splitlines():
                name, _, subject = line.partition("\x1f")
                tag_url = f"{base}/tree/{name}" if base else None
                rows.append({"name": f"{sc}/{name}", "subject": subject, "url": tag_url})
        except Exception:
            pass
    rows.sort(key=lambda r: r["name"], reverse=True)
    return rows[:limit]


def save_settings(form, user=None):
    """Persist the GitHub settings form. Returns True when the local copy had to be reset."""
    old_url = setting("git_url")
    url = form.get("git_url", "").strip()
    url = re.sub(r"^https://[^@/]+@", "https://", url)  # never store credentials in the URL
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
    if old_url and old_url != url:
        reset_local()
        return True
    return False


def init(app):
    REPOS_BASE.mkdir(parents=True, exist_ok=True)
