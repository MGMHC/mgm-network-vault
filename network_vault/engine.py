"""Backup engine: SSH to switches with Netmiko, store configs, detect changes."""
import difflib
import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from . import alerts, gitsync, ssh
from .models import Backup, Device, Job, db, get_bool, get_int, now, log_event
from .util import BACKUP_DIR, read_backup_file, safe_name

# Lines that change without a real config change; ignored for change detection.
VOLATILE = re.compile(
    r"^(## Last (commit|changed):|! Last configuration change|! NVRAM config last updated|"
    r"! No configuration change since|Building configuration|Current configuration\s*:|"
    r"! Time:|ntp clock-period|.*System Time:.*|.*Up Time:.*|"
    r"system-time \"|system-uptime \"|time \d\d:\d\d:\d\d$|date \d{4}:\d\d:\d\d$|"  # SonicOS clock
    r"checksum \d+$)", re.I)  # SonicOS export checksum: covers the re-encrypted secrets
# SonicOS re-encrypts every secret with a fresh salt on each export: compare them as one placeholder.
# A real secret change still counts, as saving any change moves the 'last-modified-by' line.
ENCRYPTED = re.compile(r"\b6,[0-9a-fA-F]{16,}")


_device_locks = {}
_locks_guard = threading.Lock()


def _lock_for(device_id):
    with _locks_guard:
        return _device_locks.setdefault(device_id, threading.Lock())


def normalized(text):
    return [ENCRYPTED.sub("6,<encrypted>", l) for l in (text or "").splitlines() if not VOLATILE.match(l.strip())]


def config_hash(text):
    return hashlib.sha256("\n".join(normalized(text)).encode()).hexdigest()


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


def parse_ha(config, active_serial, model):
    """Both units of a SonicOS HA pair, from the config's 'primary-serial' / 'secondary-serial' lines,
    in the VC member shape. The unit SSH reaches (its serial in 'show status') is the active one."""
    active = (active_serial or "").replace("-", "").upper()
    members = []
    for unit in ("Primary", "Secondary"):
        m = re.search(rf"^\s*{unit.lower()}-serial\s+([0-9A-Fa-f]{{12}})\s*$", config or "", re.M)
        if not m or not m.group(1).strip("0"):
            return []  # no HA, or the peer serial is unset
        serial = m.group(1).upper()
        state = "Active" if serial == active else "Standby"
        members.append({"id": unit, "status": "Prsnt", "serial": serial, "model": model or "", "prio": "",
                        "role": f"{unit} ({state})", "this": serial == active})
    return members


def parse_ios_last_change(config):
    """'15:05:28 IST Sat Nov 7 2026 by mgmadmin' from the IOS running-config header, or None."""
    m = re.search(r"^! Last configuration change at (.+?)\s*$", config or "", re.M)
    return m.group(1) if m else None


def parse_sonicos_last_modified(config):
    """'11/05/2026 12:46:38 by dinesh via CLI from 10.10.42.65' from SonicOS
    'last-modified-by "dinesh 10.10.42.65:X26:V99 CLI 11/05/2026 12:46:38"', or None."""
    m = re.search(r'^last-modified-by "(\S+) ([^\s:]+)\S* (\S+) ([^"]+)"', config or "", re.M)
    return f"{m.group(4).strip()} by {m.group(1)} via {m.group(3)} from {m.group(2)}" if m else None


def parse_hostname(version):
    """The switch's own hostname from 'show version' (Juniper 'Hostname: x', Cisco 'x uptime is ...')."""
    m = re.search(r"^Hostname:\s*(\S+)", version or "", re.M) or re.search(r"^(\S+)\s+uptime is\b", version or "", re.M)
    return m.group(1) if m else None


def parse_facts(device, version, inventory):
    facts = {}
    hostname = parse_hostname(version)
    if hostname:
        facts["hostname"] = hostname
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
    elif device.is_sonicwall:
        # SonicOS: Model: NSA 4800, Firmware Version: SonicOS Enhanced 6.5.4.15-116n, Serial Number: 18B169...
        m = re.search(r"Model:\s*([^\r\n]+)", version, re.I) or re.search(r"Product:\s*([^\r\n]+)", version, re.I)
        if m:
            facts["hw_model"] = m.group(1).strip()
        m = re.search(r"Firmware Version:\s*([^\r\n]+)", version, re.I) or re.search(r"SonicOS\s+([^\r\n]+)", version, re.I)
        if m:
            facts["sw_version"] = m.group(1).strip()
        m = re.search(r"Serial Number:\s*([A-Fa-f0-9]+)", version, re.I) or re.search(r"Serial Number:\s*([^\r\n]+)", inventory, re.I)
        if m:
            facts["serial"] = m.group(1).strip()
    else:
        m = re.search(r"Version:?\s*([0-9][^,\s]*)", version)
        if m:
            facts["sw_version"] = m.group(1)
        m = re.search(r"PID:\s*(\S+).*?SN:\s*(\S+)", inventory, re.S)
        if m:
            facts["hw_model"], facts["serial"] = m.group(1), m.group(2)
    return facts


def model_family(hw_model):
    """Map an exact hardware model (ex2300-48p, C1300-24P-4G, NSa 4800) to the inventory family."""
    h = (hw_model or "").lower()
    for fam in ("ex2200", "ex2300", "ex3300", "c1300"):
        if h.startswith(fam):
            return fam.upper()
    if "nsa" in h or "sonicwall" in h:
        return "NSa"
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
            kind = "HA pair" if device.is_sonicwall else "Virtual Chassis"
            log_event(f"{kind} detected ({len(vc)} members)", "device", "INFO", device, commit=False, user=actor)
        was_degraded = device.vc_degraded
        device.vc_members_json = json.dumps(vc)
        if device.vc_degraded and not was_degraded:
            log_event("Virtual Chassis degraded: member(s) not present", "device", "WARN", device,
                      details=json.dumps(vc, indent=1), commit=False, user=actor)
        elif was_degraded and not device.vc_degraded:
            log_event("Virtual Chassis recovered: all members present", "device", "INFO", device, commit=False, user=actor)


def _first(conn, *cmds, optional=False):
    """Output of the first command the device accepts (SonicOS and Cisco versions differ in command names)."""
    for i, cmd in enumerate(cmds):
        try:
            return ssh.run(conn, cmd)
        except Exception:  # noqa: BLE001 - try the next form
            if i == len(cmds) - 1 and not optional:
                raise
    return ""


def _read_facts(conn, device, vc_members):
    """version, inventory, the (command, output) pairs for the info file, and VC members (Juniper) or None."""
    family = device.profile.family
    vc = None
    if family == "juniper":
        version = ssh.run(conn, "show version | no-more")
        inventory = ssh.run(conn, "show chassis hardware | no-more")
        parts = [("show version", version), ("show chassis hardware", inventory)]
        if vc_members:
            vc_out = ssh.run(conn, "show virtual-chassis | no-more")
            parts.append(("show virtual-chassis", vc_out))
            vc = parse_vc(vc_out)
    elif family == "sonicwall":
        version = _first(conn, "show status", "show version")
        inventory = _first(conn, "show system", optional=True)
        parts = [("show status", version), ("show system", inventory)]
    else:
        version = ssh.run(conn, "show version")
        inventory = _first(conn, "show inventory", optional=True)
        parts = [("show version", version), ("show inventory", inventory)]
        if vc_members:
            parts.append(("stack", ssh.run(conn, device.profile.stack_cmd)))
    return version, inventory, parts, vc


def collect(device):
    """Return dict(config, set_config, info, facts, vc, last_commit). Raises on failure."""
    set_cfg, last_commit = None, None
    with ssh.session(device) as conn:
        if device.is_juniper:
            config = ssh.run(conn, "show configuration | no-more")
            set_cfg = ssh.run(conn, "show configuration | display set | no-more")
        elif device.is_sonicwall:
            config = _first(conn, "show current-config", "export current-config cli")
            last_commit = parse_sonicos_last_modified(config)
        else:
            config = ssh.run(conn, "show running-config")
            last_commit = parse_ios_last_change(config)
        version, inventory, info_parts, vc = _read_facts(conn, device, device.is_vc)
        if device.is_juniper:
            commits = ssh.run(conn, "show system commit | no-more")
            info_parts.insert(2, ("show system commit", commits))
            m = re.search(r"^\s*0\s+(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d.*)$", commits, re.M)
            last_commit = m.group(1).strip() if m else None

    if len(normalized(config)) < 5 or re.search(r"(syntax error|invalid input|unknown command)", config[:300], re.I):
        raise RuntimeError("Configuration output looks invalid:\n" + config[:500])

    info = "".join(f"===== {cmd} =====\n{out}\n" for cmd, out in info_parts)
    facts = parse_facts(device, version, inventory)
    if device.is_sonicwall:
        vc = parse_ha(config, facts.get("serial") or device.serial, facts.get("hw_model") or device.hw_model) or None
    return dict(config=config, set_config=set_cfg, info=info, facts=facts, vc=vc, last_commit=last_commit)


def discover_device(device_id, user=None):
    """Connect and read model, software version, serial and VC members. Returns True on success."""
    actor = user or "system"
    device = db.session.get(Device, device_id)
    if not device:
        return False
    try:
        with ssh.session(device) as conn:
            version, inventory, _, vc = _read_facts(conn, device, vc_members=device.is_juniper)
    except Exception as e:  # noqa: BLE001
        log_event("Model detection failed - will retry on next backup", "device", "WARN", device,
                  details=f"{type(e).__name__}: {e}", user=actor)
        return False
    apply_facts(device, parse_facts(device, version, inventory), vc or None, actor)
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


def trigger_label(trigger):
    """scheduled | commit-triggered | manual, for a job trigger such as 'schedule:<name>' or 'syslog:commit'."""
    if trigger.startswith("schedule"):
        return "scheduled"
    return "commit-triggered" if trigger.startswith("syslog") else "manual"


def backup_device(device_id, trigger="manual", user=None, run_alerts=None):
    """Back up one device inside an app context. Returns 'ok' | 'unchanged' | 'failed' | 'skipped' (busy).
    run_alerts (alerts.BackupRunAlerts) is told about failures and configuration changes."""
    actor = user or ("scheduler" if trigger_label(trigger) == "scheduled" else "system")
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
            if run_alerts:
                run_alerts.backup_failed(device, err)
            log_event(f"Backup failed ({trigger})", "backup", "ERROR", device, details=err, user=actor)
            return "failed"

        apply_facts(device, data["facts"], data["vc"], actor)

        digest = config_hash(data["config"])
        prev = device.backups.order_by(Backup.created_at.desc()).first()
        changed = prev is not None and prev.sha256 != digest  # first backup is a baseline, not a change
        added = removed = 0
        diff_lines = []  # first changed lines, for the config-changed alert email
        if prev and changed:
            use_set = bool(data["set_config"] and prev.set_path)
            old = normalized(read_backup_file(prev.set_path if use_set else prev.path))
            new = normalized(data["set_config"] if use_set else data["config"])
            for l in difflib.unified_diff(old, new, lineterm="", n=0):
                if l.startswith("+") and not l.startswith("+++"):
                    added += 1
                elif l.startswith("-") and not l.startswith("---"):
                    removed += 1
                else:
                    continue
                if len(diff_lines) < 500:
                    # for the alert email: secrets masked like the GitHub copy, very long lines cut
                    body = gitsync.mask(l[1:], device.is_juniper)
                    diff_lines.append(l[0] + (body[:300] + "…" if len(body) > 300 else body))
            changed = bool(added or removed)  # hash differs only in ignored lines (e.g. stored before a rule change)

        device.last_backup_at = now()
        device.last_backup_status = "ok"
        device.last_backup_error = None
        device.backup_fail_alerted = False

        if prev and not changed and get_bool("skip_unchanged"):
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
        if changed and prev and run_alerts:
            run_alerts.config_changed(device, data["last_commit"], added=added, removed=removed, diff=diff_lines,
                                      diff_total=added + removed, prev_id=prev.id, backup_id=backup.id)
        gitsync.record(backup.id)
        apply_retention(device)
        return "ok" if changed or not prev else "unchanged"
    finally:
        lock.release()


def apply_retention(device):
    keep = get_int("retention_count")
    days = get_int("retention_days")
    q = device.backups.filter(Backup.pinned.is_(False)).order_by(Backup.created_at.desc())
    victims = []
    if keep > 0:
        victims += q.offset(keep).all()
    if days > 0:
        victims += q.filter(Backup.created_at < now() - timedelta(days=days)).all()
    latest = device.backups.order_by(Backup.created_at.desc()).first()
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


def start_backup_job(app, device_ids, trigger="manual", wait=False, user=None):
    with app.app_context():
        job = Job(kind="backup", trigger=trigger, total=len(device_ids))
        db.session.add(job)
        db.session.commit()
        job_id = job.id
        workers = max(1, get_int("max_workers"))
        git_head_before = gitsync.run_started()
        label = trigger_label(trigger)
        run_alerts = alerts.BackupRunAlerts(manual=label == "manual")

    def worker(dev_id):
        with app.app_context():
            try:
                result = backup_device(dev_id, trigger, user, run_alerts=run_alerts)
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
        run_alerts.send(app, label)
        with app.app_context():
            gitsync.run_finished(db.session.get(Job, job_id), git_head_before)

    if wait:
        runner()
    else:
        threading.Thread(target=runner, daemon=True, name=f"backup-job-{job_id}").start()
    return job_id


def live_command(device, kind, a=None, b=None):
    """Run a read-only live tool on a device. Returns (title, text)."""
    with ssh.session(device) as conn:
        if kind == "test":
            return "Connection test", f"Connected OK. Prompt: {conn.find_prompt()}\n"
        if kind == "commits" and device.is_juniper:
            return "Commit history", ssh.run(conn, "show system commit | no-more")
        if kind == "rollback" and device.is_juniper:
            a, b = int(a or 1), int(b or 0)
            out = ssh.run(conn, f"show system rollback {a} compare {b} | no-more")
            return f"Changes from rollback {a} to rollback {b}", out if out.strip() else "(no differences)\n"
        if kind == "vc":
            if device.is_juniper:
                out = ssh.run(conn, "show virtual-chassis | no-more")
                device.vc_members_json = json.dumps(parse_vc(out))
                db.session.commit()
                return "Virtual Chassis", out
            return "Stack", ssh.run(conn, device.profile.stack_cmd)
        if kind == "unsaved" and not device.is_juniper:
            running = ssh.run(conn, "show running-config")
            startup = ssh.run(conn, "show startup-config")
            diff = list(difflib.unified_diff(normalized(startup), normalized(running),
                                             "startup-config", "running-config", lineterm=""))
            return "Unsaved changes (startup vs running)", "\n".join(diff) + "\n" if diff else \
                "Running config matches startup config - nothing unsaved.\n"
        if kind == "optics":
            if device.is_juniper:
                return "Optical Transceiver Diagnostics (DOM/DDM)", ssh.run(conn, "show interfaces diagnostics optics | no-more")
            if device.profile.netmiko == "cisco_s300":
                return "Optical Transceiver Diagnostics", _first(conn, "show optical-transceiver-diagnostics",
                                                                 "show fiber-ports optical-transceiver")
            return "Optical Transceiver Diagnostics", ssh.run(conn, "show interfaces transceiver detail")
        if kind == "macs":
            if device.is_juniper:
                return "MAC Address Table", ssh.run(conn, "show ethernet-switching table | no-more")
            if device.is_sonicwall:
                return "ARP / MAC Table", ssh.run(conn, "show arp-cache")
            return "MAC Address Table", ssh.run(conn, "show mac address-table")
        if kind == "arp":
            if device.is_juniper:
                return "ARP Table", ssh.run(conn, "show arp no-resolve | no-more")
            if device.is_sonicwall:
                return "ARP Table", ssh.run(conn, "show arp-cache")
            return "ARP Table", ssh.run(conn, "show ip arp")
        raise ValueError("Unsupported tool for this platform")
