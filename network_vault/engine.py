"""Backup engine: SSH to switches with Netmiko, store configs, detect changes."""
import difflib
import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from . import gitsync
from .models import Backup, Device, Job, db, get_int, get_setting, now
from .util import BACKUP_DIR, decrypt, log_event, safe_name

import paramiko
from netmiko.cisco.cisco_s300 import CiscoS300SSH
from netmiko.channel import SSHChannel
from netmiko.exceptions import NetmikoAuthenticationException
from netmiko.ssh_dispatcher import CLASS_MAPPER


class CiscoC1300SSH(CiscoS300SSH):
    """Cisco C1300 / CBS / SG switch driver with fallback for in-band SSH login.

    On Cisco C1300 and Small Business switches, standard SSH password authentication
    ('ip ssh password-auth') is disabled by factory default. In that case, standard
    Paramiko auth_password fails with 'Bad authentication type; allowed types: []'.
    This driver attempts standard SSH auth first; if that fails, it falls back to
    transport auth_none followed by interactive in-band terminal login ('User Name:', 'Password:').
    """

    def establish_connection(self, width: int = 511, height: int = 1000) -> None:
        try:
            return super().establish_connection(width=width, height=height)
        except NetmikoAuthenticationException:
            return self._establish_inband_connection(width=width, height=height)

    def _establish_inband_connection(self, width: int = 511, height: int = 1000) -> None:
        import time
        self.remote_conn_pre = self._build_ssh_client()
        sock = (self.host, self.port)
        t = paramiko.Transport(sock)
        if self.disabled_algorithms:
            t.disabled_algorithms = self.disabled_algorithms
        t.start_client(timeout=self.conn_timeout)
        try:
            t.auth_none(self.username)
        except Exception:
            pass

        if not t.is_authenticated():
            raise NetmikoAuthenticationException(
                f"Failed to authenticate to {self.host}:{self.port} (neither password nor in-band allowed)"
            )

        self.remote_conn = t.open_session()
        self.remote_conn.get_pty(term="vt100", width=width, height=height)
        self.remote_conn.invoke_shell()
        self.remote_conn.settimeout(self.blocking_timeout)

        buf = ""
        start = time.time()
        timeout = max(10, self.auth_timeout)
        while time.time() - start < timeout:
            if self.remote_conn.recv_ready():
                chunk = self.remote_conn.recv(1024).decode(self.encoding, errors="ignore")
                buf += chunk
                if any(prompt in buf for prompt in ["User Name:", "User Name :", "login as:"]):
                    break
            time.sleep(0.1)

        if any(prompt in buf for prompt in ["User Name:", "User Name :", "login as:"]):
            self.remote_conn.send((self.username + "\r").encode(self.encoding))
            buf = ""
            start = time.time()
            while time.time() - start < timeout:
                if self.remote_conn.recv_ready():
                    chunk = self.remote_conn.recv(1024).decode(self.encoding, errors="ignore")
                    buf += chunk
                    if "Password:" in buf or "Password :" in buf:
                        break
                time.sleep(0.1)

        if "Password:" in buf or "Password :" in buf:
            self.remote_conn.send((self.password + "\r").encode(self.encoding))
            buf = ""
            start = time.time()
            while time.time() - start < timeout:
                if self.remote_conn.recv_ready():
                    chunk = self.remote_conn.recv(1024).decode(self.encoding, errors="ignore")
                    buf += chunk
                    if any(p in buf for p in [">", "#"]):
                        break
                time.sleep(0.1)

        self.channel = SSHChannel(conn=self.remote_conn, encoding=self.encoding)
        return None


CLASS_MAPPER["cisco_s300"] = CiscoC1300SSH

NETMIKO_TYPE = {
    "juniper_junos": "juniper_junos",
    "cisco_c1300": "cisco_s300",
    "cisco_s300": "cisco_s300",
    "cisco_ios": "cisco_ios",
}

# Lines that change without a real config change; ignored for change detection.
VOLATILE = re.compile(
    r"^(## Last (commit|changed):|! Last configuration change|! NVRAM config last updated|"
    r"! No configuration change since|Building configuration|Current configuration\s*:|"
    r"! Time:|ntp clock-period)", re.I)
VC_PROMPT_LINE = re.compile(r"^\{(master|backup|linecard)(:\d+)?\}\s*$", re.I)

_device_locks = {}
_locks_guard = threading.Lock()


def _lock_for(device_id):
    with _locks_guard:
        return _device_locks.setdefault(device_id, threading.Lock())


# --------------------------------------------------------------------------- connection

def connect(device):
    from netmiko import ConnectHandler

    cred = device.credential
    if not cred:
        raise RuntimeError("No credential profile assigned to this device")
    t = get_int("ssh_timeout")
    params = dict(
        device_type=NETMIKO_TYPE.get(device.platform, device.platform),
        host=device.host, port=device.port or 22,
        username=cred.username, password=decrypt(cred.password_enc),
        secret=decrypt(cred.secret_enc),
        conn_timeout=t, auth_timeout=t, banner_timeout=t, timeout=t,
        fast_cli=False, global_delay_factor=1,
    )
    if device.legacy_ssh:
        # Old Junos (EX2200/EX3300 on 12.x) and older Cisco: force classic ssh-rsa signatures
        params["disabled_algorithms"] = {"pubkeys": ["rsa-sha2-256", "rsa-sha2-512"]}
    conn = ConnectHandler(**params)
    if device.platform.startswith("cisco") and params["secret"]:
        try:
            if not conn.check_enable_mode():
                conn.enable()
        except Exception:
            pass
    return conn


def run(conn, cmd):
    out = conn.send_command(cmd, read_timeout=get_int("read_timeout"), strip_prompt=True, strip_command=True)
    return clean(out)


def clean(text):
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and (not lines[-1].strip() or VC_PROMPT_LINE.match(lines[-1].strip())):
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(l.rstrip() for l in lines) + "\n"


def normalized(text):
    return [l for l in (text or "").splitlines() if not VOLATILE.match(l.strip())]


def config_hash(text):
    return hashlib.sha256("\n".join(normalized(text)).encode()).hexdigest()


# --------------------------------------------------------------------------- fact parsing

def parse_vc(text):
    members = []
    for line in text.splitlines():
        m = re.match(r"^\s*(\d+)\s+\(FPC\s+\d+\)\s+(\S+)\s+(\S+)?\s*(\S+)?\s*(\d+)?\s*(\S+)?", line)
        if m:
            members.append({
                "id": m.group(1), "status": m.group(2), "serial": m.group(3) or "",
                "model": m.group(4) or "", "prio": m.group(5) or "", "role": (m.group(6) or "").rstrip("*"),
                "this": (m.group(6) or "").endswith("*"),
            })
    return members


def parse_facts(device, version, inventory):
    facts = {}
    if device.is_juniper:
        m = re.search(r"^Model:\s*(\S+)", version, re.M)
        if m:
            facts["hw_model"] = m.group(1)
        m = re.search(r"^Junos:\s*(\S+)", version, re.M) or re.search(r"JUNOS[^\[\n]*\[([^\]]+)\]", version)
        if m:
            facts["sw_version"] = m.group(1)
        m = re.search(r"^Chassis\s+(\S+)", inventory, re.M)
        if m:
            facts["serial"] = m.group(1)
    else:
        m = re.search(r"Version:?\s*([0-9][^,\s]*)", version)
        if m:
            facts["sw_version"] = m.group(1)
        m = re.search(r"PID:\s*(\S+).*?SN:\s*(\S+)", inventory, re.S)
        if m:
            facts["hw_model"], facts["serial"] = m.group(1), m.group(2)
    return facts


def model_family(hw_model):
    """Map an exact hardware model (ex2300-48p, C1300-24P-4G) to the inventory family."""
    h = (hw_model or "").lower()
    for fam in ("ex2200", "ex2300", "ex3300", "c1300"):
        if h.startswith(fam):
            return fam.upper()
    return "Other" if h else None


def apply_facts(device, facts, vc, actor):
    """Store discovered facts on the device; logs VC state changes. Caller commits."""
    for k, v in facts.items():
        setattr(device, k, v[:128])
    fam = model_family(device.hw_model)
    if fam:
        device.model = fam
    if vc is not None:
        if len(vc) > 1 and not device.is_vc:
            device.is_vc = True
            log_event(f"Virtual Chassis detected ({len(vc)} members)", "device", "INFO", device, commit=False, user=actor)
        was_degraded = device.vc_degraded
        device.vc_members_json = json.dumps(vc)
        if device.vc_degraded and not was_degraded:
            log_event("Virtual Chassis degraded: member(s) not present", "device", "WARN", device,
                      details=json.dumps(vc, indent=1), commit=False, user=actor)
        elif was_degraded and not device.vc_degraded:
            log_event("Virtual Chassis recovered: all members present", "device", "INFO", device, commit=False, user=actor)


# --------------------------------------------------------------------------- collection

def collect(device):
    """Return dict(config, set_config, info, facts, vc, last_commit). Raises on failure."""
    conn = connect(device)
    try:
        info_parts, vc, last_commit, set_cfg = [], None, None, None
        if device.is_juniper:
            config = run(conn, "show configuration | no-more")
            set_cfg = run(conn, "show configuration | display set | no-more")
            version = run(conn, "show version | no-more")
            inventory = run(conn, "show chassis hardware | no-more")
            commits = run(conn, "show system commit | no-more")
            info_parts += [("show version", version), ("show chassis hardware", inventory),
                           ("show system commit", commits)]
            if device.is_vc:
                vc_out = run(conn, "show virtual-chassis | no-more")
                info_parts.append(("show virtual-chassis", vc_out))
                vc = parse_vc(vc_out)
            m = re.search(r"^\s*0\s+(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d.*)$", commits, re.M)
            last_commit = m.group(1).strip() if m else None
        else:
            config = run(conn, "show running-config")
            version = run(conn, "show version")
            try:
                inventory = run(conn, "show inventory")
            except Exception:
                inventory = ""
            info_parts += [("show version", version), ("show inventory", inventory)]
            if device.is_vc:
                stack = run(conn, "show stack" if device.platform == "cisco_c1300" else "show switch")
                info_parts.append(("stack", stack))
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass

    if len(normalized(config)) < 5 or re.search(r"(syntax error|invalid input|unknown command)", config[:300], re.I):
        raise RuntimeError("Configuration output looks invalid:\n" + config[:500])

    info = "".join(f"===== {cmd} =====\n{out}\n" for cmd, out in info_parts)
    return dict(config=config, set_config=set_cfg, info=info, facts=parse_facts(device, version, inventory),
                vc=vc, last_commit=last_commit)


def discover_device(device_id, user=None):
    """Connect and read model, software version, serial and VC members. Returns True on success."""
    actor = user or "system"
    device = db.session.get(Device, device_id)
    if not device:
        return False
    try:
        conn = connect(device)
        try:
            vc = None
            if device.is_juniper:
                version = run(conn, "show version | no-more")
                inventory = run(conn, "show chassis hardware | no-more")
                vc_out = run(conn, "show virtual-chassis | no-more")
                vc = parse_vc(vc_out) or None
            else:
                version = run(conn, "show version")
                try:
                    inventory = run(conn, "show inventory")
                except Exception:
                    inventory = ""
        finally:
            try:
                conn.disconnect()
            except Exception:
                pass
    except Exception as e:  # noqa: BLE001
        log_event("Model detection failed - will retry on next backup", "device", "WARN", device,
                  details=f"{type(e).__name__}: {e}", user=actor)
        return False
    apply_facts(device, parse_facts(device, version, inventory), vc, actor)
    log_event(f"Detected model {device.hw_model or '?'} ({device.model}), software {device.sw_version or '?'}",
              "device", "INFO", device, commit=False, user=actor)
    db.session.commit()
    return True


def start_discover_job(app, device_ids, user=None):
    """Detect models in the background (used after CSV import / adding a device)."""
    if not device_ids:
        return

    def worker(dev_id):
        with app.app_context():
            discover_device(dev_id, user)

    def runner():
        with app.app_context():
            workers = max(1, get_int("max_workers"))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(worker, device_ids))

    threading.Thread(target=runner, daemon=True, name="discover").start()


def backup_device(device_id, trigger="manual", user=None):
    """Back up one device. Must be called inside an app context. Returns 'ok' | 'unchanged' | 'failed'."""
    actor = user or ("scheduler" if trigger.startswith("schedule") else "system")
    lock = _lock_for(device_id)
    if not lock.acquire(blocking=False):
        return "skipped"
    try:
        device = db.session.get(Device, device_id)
        if not device:
            return "failed"
        try:
            data = collect(device)
        except Exception as e:  # noqa: BLE001 - surface any netmiko/paramiko error to the user
            err = f"{type(e).__name__}: {e}"
            device.last_backup_status = "failed"
            device.last_backup_error = err[:2000]
            device.last_backup_at = now()
            log_event(f"Backup failed ({trigger})", "backup", "ERROR", device, details=err, user=actor)
            return "failed"

        apply_facts(device, data["facts"], data["vc"], actor)

        digest = config_hash(data["config"])
        prev = device.backups.first()
        changed = prev is not None and prev.sha256 != digest  # first backup is a baseline, not a change
        added = removed = 0
        if prev and changed:
            from .util import read_backup_file
            use_set = bool(data["set_config"] and prev.set_path)
            old = normalized(read_backup_file(prev.set_path if use_set else prev.path))
            new = normalized(data["set_config"] if use_set else data["config"])
            for l in difflib.unified_diff(old, new, lineterm="", n=0):
                if l.startswith("+") and not l.startswith("+++"):
                    added += 1
                elif l.startswith("-") and not l.startswith("---"):
                    removed += 1

        device.last_backup_at = now()
        device.last_backup_status = "ok"
        device.last_backup_error = None

        if prev and not changed and get_setting("skip_unchanged") == "1":
            log_event(f"Backup OK - configuration unchanged, not stored ({trigger})", "backup", "INFO", device, user=actor)
            return "unchanged"

        stamp = now().strftime("%Y%m%d-%H%M%S")
        folder = BACKUP_DIR / safe_name(device.name)
        folder.mkdir(parents=True, exist_ok=True)
        base = f"{safe_name(device.name)}_{stamp}"
        (folder / f"{base}.conf").write_text(data["config"], encoding="utf-8")
        rel = f"{folder.name}/{base}.conf"
        set_rel = info_rel = None
        if data["set_config"]:
            (folder / f"{base}.set").write_text(data["set_config"], encoding="utf-8")
            set_rel = f"{folder.name}/{base}.set"
        if data["info"]:
            (folder / f"{base}.info.txt").write_text(data["info"], encoding="utf-8")
            info_rel = f"{folder.name}/{base}.info.txt"

        backup = Backup(
            device_id=device.id, trigger=trigger, path=rel, set_path=set_rel, info_path=info_rel,
            size=len(data["config"].encode()), sha256=digest, changed=changed,
            lines_added=added, lines_removed=removed, last_commit=data["last_commit"],
        )
        db.session.add(backup)
        if changed and prev:
            msg = f"Backup OK - configuration CHANGED (+{added} / -{removed} lines) ({trigger})"
            log_event(msg, "backup", "WARN" if trigger != "manual" else "INFO", device, commit=False,
                      details=f"Last commit: {data['last_commit']}" if data["last_commit"] else None, user=actor)
        else:
            log_event(f"Backup OK - {'first backup' if not prev else 'no changes'} ({trigger})",
                      "backup", "INFO", device, commit=False, user=actor)
        db.session.commit()
        gitsync.record(backup.id)
        apply_retention(device)
        return "ok" if changed or not prev else "unchanged"
    finally:
        lock.release()


def apply_retention(device):
    keep = get_int("retention_count")
    days = get_int("retention_days")
    q = device.backups.filter(Backup.pinned.is_(False))
    victims = []
    if keep > 0:
        victims += q.offset(keep).all()
    if days > 0:
        victims += q.filter(Backup.created_at < now() - timedelta(days=days)).all()
    latest = device.backups.first()
    for b in {v.id: v for v in victims}.values():
        if latest and b.id == latest.id:
            continue
        delete_backup(b, commit=False)
    db.session.commit()


def delete_backup(b, commit=True):
    for rel in (b.path, b.set_path, b.info_path):
        if rel:
            p = BACKUP_DIR / rel
            if p.exists():
                p.unlink()
    db.session.delete(b)
    if commit:
        db.session.commit()


# --------------------------------------------------------------------------- jobs

def start_backup_job(app, device_ids, trigger="manual", wait=False, user=None):
    with app.app_context():
        job = Job(kind="backup", trigger=trigger, total=len(device_ids))
        db.session.add(job)
        db.session.commit()
        job_id = job.id
        workers = max(1, get_int("max_workers"))
        git_head_before = gitsync.head() if gitsync.is_enabled() else None

    def worker(dev_id):
        with app.app_context():
            try:
                result = backup_device(dev_id, trigger, user)
            except Exception as e:  # noqa: BLE001
                log_event(f"Unexpected backup error: {e}", "backup", "ERROR")
                result = "failed"
            j = db.session.get(Job, job_id)
            j.done += 1
            if result == "failed":
                j.failed += 1
            elif result == "unchanged":
                j.unchanged += 1
                j.ok += 1
            elif result == "ok":
                j.ok += 1
            db.session.commit()

    def runner():
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(worker, device_ids))
        with app.app_context():
            j = db.session.get(Job, job_id)
            j.finished = now()
            j.status = "failed" if j.failed and not j.ok else ("partial" if j.failed else "done")
            db.session.commit()
            log_event(f"Backup job #{job_id} finished ({trigger}): {j.ok} ok ({j.unchanged} unchanged), "
                      f"{j.failed} failed of {j.total}", "backup",
                      "ERROR" if j.failed else "INFO", user=user or "system")
            if gitsync.is_enabled():
                gitsync.tag_run(j, git_head_before)
                if gitsync.ahead():
                    gitsync.push()

    if wait:
        runner()
    else:
        threading.Thread(target=runner, daemon=True, name=f"backup-job-{job_id}").start()
    return job_id


# --------------------------------------------------------------------------- live tools

def live_command(device, kind, a=None, b=None):
    """Run a read-only live tool on a device. Returns (title, text)."""
    conn = connect(device)
    try:
        if kind == "test":
            return "Connection test", f"Connected OK. Prompt: {conn.find_prompt()}\n"
        if kind == "commits" and device.is_juniper:
            return "Commit history", run(conn, "show system commit | no-more")
        if kind == "rollback" and device.is_juniper:
            a, b = int(a or 1), int(b or 0)
            out = run(conn, f"show system rollback {a} compare {b} | no-more")
            return f"Changes from rollback {a} to rollback {b}", out if out.strip() else "(no differences)\n"
        if kind == "vc":
            if device.is_juniper:
                out = run(conn, "show virtual-chassis | no-more")
                device.vc_members_json = json.dumps(parse_vc(out))
                db.session.commit()
                return "Virtual Chassis", out
            return "Stack", run(conn, "show stack" if device.platform == "cisco_c1300" else "show switch")
        if kind == "unsaved" and not device.is_juniper:
            running = run(conn, "show running-config")
            startup = run(conn, "show startup-config")
            diff = list(difflib.unified_diff(normalized(startup), normalized(running),
                                             "startup-config", "running-config", lineterm=""))
            return "Unsaved changes (startup vs running)", "\n".join(diff) + "\n" if diff else \
                "Running config matches startup config - nothing unsaved.\n"
        raise ValueError("Unsupported tool for this platform")
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass
