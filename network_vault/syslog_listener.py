"""Syslog listener daemon for Event-Driven Vaulting.

Listens for UDP syslog packets from switches (Juniper Junos, Cisco IOS, Cisco C1300/CBS).
Detects configuration commit and save events, applies a debounce/cooldown window,
and triggers automatic configuration backups. Raw syslog lines are appended to daily log
files in the configured directory (e.g. D:/NetworkVault/syslog).
"""
import datetime
import logging
import re
import socket
import threading
from pathlib import Path

from . import config, engine
from .models import Device, log_event
from .util import safe_name

logger = logging.getLogger("syslog_listener")

# Regex patterns identifying configuration changes across our switch fleet:
# 1. Juniper Junos:
#    - UI_COMMIT_COMPLETED: commit completed by user
#    - UI_COMMIT_PROGRESS: commit in progress
#    - mgd\[\d+\]:.*UI_COMMIT: commit triggered
#    (Note: UI_CMDLINE_READ_LINE with 'show system commit' must NOT match)
# 2. Cisco IOS / IOS-XE:
#    - %SYS-5-CONFIG_I: Configured from console by <user>
#    - %PARSER-5-CFGLOG_LOGGEDCMD: logged configuration command
# 3. Cisco C1300 / CBS / Small Business:
#    - COPY - TRAP - The copy operation was completed successfully (copy running-config startup-config)
#    - COPY - FILECPY.*startup-config
#    - COPY-I-TRAP
#    - %CLI-I-CFGLOG
COMMIT_PATTERNS = [
    re.compile(r"UI_COMMIT_COMPLETED", re.IGNORECASE),
    re.compile(r"UI_COMMIT_PROGRESS", re.IGNORECASE),
    re.compile(r"UI_COMMIT:", re.IGNORECASE),
    re.compile(r"%SYS-5-CONFIG_I", re.IGNORECASE),
    re.compile(r"%PARSER-5-CFGLOG_LOGGEDCMD", re.IGNORECASE),
    re.compile(r"COPY\s*-\s*TRAP.*The copy operation was completed successfully", re.IGNORECASE),
    re.compile(r"COPY\s*-\s*FILECPY.*startup-config", re.IGNORECASE),
    re.compile(r"COPY-I-TRAP", re.IGNORECASE),
    re.compile(r"%CLI-I-CFGLOG", re.IGNORECASE),
    # 4. SonicWall SonicOS:
    re.compile(r"Commit changes.*succeeded", re.IGNORECASE),
    re.compile(r"Configuration change.*succeeded", re.IGNORECASE),
    re.compile(r"msg=\"Configuration changed\"", re.IGNORECASE),
    re.compile(r"Configuration\s+updated", re.IGNORECASE),
]


# Patterns that look like commits but are actually read-only inspections
EXCLUDE_PATTERNS = [
    re.compile(r"UI_CMDLINE_READ_LINE:.*show", re.IGNORECASE),
]

# Track pending backup triggers per device ID:
# device_id -> {"timer": threading.Timer, "device_name": str, "ip": str, "last_msg": str}
_pending_triggers = {}
_lock = threading.Lock()
_listener_thread = None
_socket = None
_running = False
_app = None


def is_commit_message(msg: str) -> bool:
    """Check if the syslog text contains a switch configuration change / commit event."""
    if any(p.search(msg) for p in EXCLUDE_PATTERNS):
        return False
    return any(p.search(msg) for p in COMMIT_PATTERNS)


def _extract_hostname_from_msg(msg: str) -> str:
    """Attempt to extract hostname from standard syslog headers like RFC5424/RFC3164."""
    # Example Junos: <190>1 2026-10-12T17:57:35Z MGM-11F-SUBRACK1-SW1 ...
    m = re.search(r"<\d+>\S*\s+\S+\s+([A-Za-z0-9_.-]+)", msg)
    if m:
        candidate = m.group(1).strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}", candidate) and ":" not in candidate:
            return candidate
    return ""


def _append_raw_log(ip: str, message: str, log_dir_path: Path, device_name: str = None):
    """Write raw syslog line to daily log file on disk: syslog-<hostname>-<YYYY-MM-DD>.log."""
    try:
        log_dir_path.mkdir(parents=True, exist_ok=True)
        today = datetime.date.today().isoformat()
        
        tag = None
        if device_name:
            tag = safe_name(device_name)
        else:
            extracted = _extract_hostname_from_msg(message)
            if extracted:
                tag = safe_name(extracted)
            else:
                tag = safe_name(ip)

        log_file = log_dir_path / f"syslog-{tag}-{today}.log"
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_file, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"[{now_str}] [{ip}] {message.strip()}\n")
    except Exception as e:
        logger.warning("Failed to write to raw syslog file: %s", e)


def purge_old_files(log_dir, days):
    """Delete raw syslog files not written to for `days` days (0 = keep forever). Returns how many."""
    folder = Path(log_dir)
    if days <= 0 or not folder.is_dir():
        return 0
    cutoff = datetime.datetime.now().timestamp() - days * 86400
    n = 0
    for p in folder.glob("syslog-*.log"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                n += 1
        except OSError as e:  # file busy or gone: try again tomorrow
            logger.warning("Could not delete old syslog file %s: %s", p, e)
    return n


def _trigger_backup(device_id: int, device_name: str, trigger_reason: str):
    """Callback after debounce timer expires: initiate backup job for device."""
    with _lock:
        _pending_triggers.pop(device_id, None)

    if not _app:
        return

    logger.info("Executing syslog-triggered backup for %s (id=%s, trigger=%s)", device_name, device_id, trigger_reason)
    try:
        engine.start_backup_job(_app, [device_id], trigger=f"syslog:{trigger_reason}", user="syslog")
    except Exception as e:
        logger.error("Failed to start syslog backup job for device %s: %s", device_name, e)


def _queue_device_backup(device_id: int, device_name: str, ip: str, summary: str, debounce_sec: int):
    """Debounce rapid commits/logs by resetting a timer for debounce_sec seconds."""
    with _lock:
        existing = _pending_triggers.get(device_id)
        if existing and existing.get("timer"):
            existing["timer"].cancel()

        logger.info("Scheduling backup for %s (%s) in %ss due to config event: %s",
                    device_name, ip, debounce_sec, summary[:80])

        timer = threading.Timer(
            debounce_sec,
            _trigger_backup,
            args=[device_id, device_name, "commit"]
        )
        timer.daemon = True
        timer.start()

        _pending_triggers[device_id] = {
            "timer": timer,
            "device_name": device_name,
            "ip": ip,
            "last_msg": summary,
        }


def _process_syslog_packet(raw_bytes: bytes, addr: tuple, log_dir_path: Path, debounce_sec: int):
    """Handle one incoming UDP packet."""
    ip = addr[0]
    try:
        msg = raw_bytes.decode("utf-8", errors="replace").strip()
    except Exception:
        return

    device = None
    if _app:
        with _app.app_context():
            device = Device.query.filter(Device.enabled.is_(True), Device.host == ip).first()

    dev_name = device.name if device else None

    _append_raw_log(ip, msg, log_dir_path, device_name=dev_name)

    if not is_commit_message(msg):
        return

    if device and _app:
        device_id = device.id
        device_name = device.name
        with _app.app_context():
            log_event(
                f"Config change detected via Syslog from '{device_name}' ({ip}): {msg[:180]}",
                category="backup",
                level="INFO",
                device=device,
                user="syslog"
            )

        _queue_device_backup(device_id, device_name, ip, msg, debounce_sec)


def _run_udp_server(host: str, port: int, log_dir_path: Path, debounce_sec: int):
    """UDP listener loop."""
    global _socket
    _socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        _socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _socket.bind((host, port))
        logger.info("Syslog listener active on UDP %s:%s (Debounce: %ss, Logs: %s)",
                    host, port, debounce_sec, log_dir_path)
    except Exception as e:
        logger.error("Failed to bind Syslog UDP socket on %s:%s - %s", host, port, e)
        return

    while _running:
        try:
            data, addr = _socket.recvfrom(8192)
            if data:
                _process_syslog_packet(data, addr, log_dir_path, debounce_sec)
        except OSError:
            break
        except Exception as e:
            logger.warning("Error processing syslog packet: %s", e)


def init_syslog_listener(app):
    """Initialize and start the background Syslog UDP listener."""
    global _app, _listener_thread, _running
    _app = app

    enabled = config.get("syslog_enabled")
    if not enabled:
        logger.info("Syslog listener disabled in configuration.")
        return

    port = config.get("syslog_port")
    debounce_sec = config.get("syslog_debounce_sec")
    raw_dir = config.get("syslog_log_dir")
    log_dir_path = Path(raw_dir)

    _running = True
    _listener_thread = threading.Thread(
        target=_run_udp_server,
        args=("0.0.0.0", port, log_dir_path, debounce_sec),
        daemon=True,
        name="SyslogListener"
    )
    _listener_thread.start()

