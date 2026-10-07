"""Switch reachability: ICMP ping + SSH TCP port check."""
import platform
import re
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor

from .models import Device, db, now
from .util import log_event

IS_WIN = platform.system() == "Windows"
_NO_WINDOW = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW


def ping(host, timeout_ms=1000, count=2):
    cmd = ["ping", "-n", str(count), "-w", str(timeout_ms), host] if IS_WIN else \
          ["ping", "-c", str(count), "-W", str(max(1, timeout_ms // 1000)), host]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=count * timeout_ms / 1000 + 5,
                             creationflags=_NO_WINDOW).stdout
    except (subprocess.TimeoutExpired, OSError):
        return None
    # "TTL=" guards against Windows' "Destination host unreachable" replies that exit 0
    if "ttl=" not in out.lower():
        return None
    times = [float(t) for t in re.findall(r"time[=<]\s*([\d.]+)", out, re.I)]
    return min(times) if times else 0.5


def tcp_open(host, port, timeout=3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe(host, port):
    rtt = ping(host)
    ssh = tcp_open(host, port)
    if ssh:
        status = "up"
    elif rtt is not None:
        status = "ssh-down"
    else:
        status = "down"
    return status, rtt


def check_devices(app, device_ids=None):
    with app.app_context():
        q = Device.query.filter_by(enabled=True)
        if device_ids:
            q = Device.query.filter(Device.id.in_(device_ids))
        targets = [(d.id, d.host, d.port or 22) for d in q.all()]
    if not targets:
        return {}
    with ThreadPoolExecutor(max_workers=min(48, len(targets))) as pool:
        results = dict(zip([t[0] for t in targets], pool.map(lambda t: probe(t[1], t[2]), targets)))

    with app.app_context():
        for dev_id, (status, rtt) in results.items():
            d = db.session.get(Device, dev_id)
            if d is None:
                continue
            old = d.status
            d.ping_ms = rtt
            d.last_checked = now()
            if old != status:
                d.status = status
                d.status_since = now()
                if old != "unknown" or status != "up":
                    level = "INFO" if status == "up" else "ERROR" if status == "down" else "WARN"
                    text = {"up": "Device reachable (ping + SSH OK)", "down": "Device UNREACHABLE (no ping, no SSH)",
                            "ssh-down": "Device pings but SSH port is closed"}[status]
                    log_event(f"{text} [was {old}]", "reachability", level, d, commit=False, user="monitor")
        db.session.commit()
    return results
