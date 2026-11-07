"""Optical transceiver (SFP DOM/DDM) diagnostics: parse, rate and scan across switches."""
import queue
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from .models import Device, db
from .scanprogress import DeviceSteps, fleet_progress, ndjson, timestamp, scan_workers


def evaluate_optics_health(rx_dbm, alarms=None):
    """
    Evaluate optical health grade: 'ok', 'warn', 'bad'.
    Standard SFP / SFP+ optics operational thresholds.
    """
    alarms = alarms or []
    if rx_dbm is None:
        return "unknown", "No Telemetry", "Optics installed but digital diagnostic monitoring is unavailable."

    if rx_dbm <= -28.0 or any("rx" in a.lower() and "alarm" in a.lower() for a in alarms):
        return "bad", "Critical / Loss of Light", "Signal power too low (<= -28 dBm). Fiber link dark, broken, or far-end laser off."

    if -28.0 < rx_dbm <= -18.0 or any("rx" in a.lower() and "warning" in a.lower() for a in alarms):
        return "warn", "Low Optical Signal Warning", "High attenuation detected (-18 to -28 dBm). Check fiber patch cables and clean connectors."

    if rx_dbm > -1.0 or any("high" in a.lower() and "alarm" in a.lower() for a in alarms):
        return "warn", "High Signal Warning", "Optical power too hot (> -1.0 dBm). Consider inline optical attenuator to prevent receiver burn-in."

    return "ok", "Optimal Signal", "RX and TX optical power levels are within healthy operating margins."


def parse_junos_optics(text):
    """Parse output of 'show interfaces diagnostics optics' on Juniper switches."""
    ports = []
    # Blocks delimited by 'Physical interface: <name>'
    blocks = re.split(r"Physical interface:\s*(\S+)", text)
    if len(blocks) < 2:
        return ports

    for i in range(1, len(blocks), 2):
        iface_name = blocks[i].strip()
        body = blocks[i+1]

        rx_match = re.search(r"Receiver signal average optical power\s*:\s*([\d\.]+)\s*mW\s*/\s*(-?[\d\.]+|-Inf)\s*dBm", body)
        tx_match = re.search(r"Laser output power\s*:\s*([\d\.]+)\s*mW\s*/\s*(-?[\d\.]+|-Inf)\s*dBm", body)
        temp_match = re.search(r"Module temperature\s*:\s*(-?[\d\.]+)\s*degrees C", body)
        volt_match = re.search(r"Module voltage\s*:\s*([\d\.]+)\s*V", body)
        bias_match = re.search(r"Laser bias current\s*:\s*([\d\.]+)\s*mA", body)

        rx_dbm = None
        if rx_match:
            val = rx_match.group(2)
            rx_dbm = -40.0 if "-Inf" in val else float(val)

        tx_dbm = None
        if tx_match:
            val = tx_match.group(2)
            tx_dbm = -40.0 if "-Inf" in val else float(val)

        temp_c = float(temp_match.group(1)) if temp_match else None
        volt_v = float(volt_match.group(1)) if volt_match else None
        bias_ma = float(bias_match.group(1)) if bias_match else None

        # Check for active alarms (lines ending with ': On')
        alarms = []
        for line in body.splitlines():
            line = line.strip()
            if ":  On" in line or ": On" in line:
                alarm_name = line.split(":")[0].strip()
                alarms.append(alarm_name)

        status, status_label, status_desc = evaluate_optics_health(rx_dbm, alarms)

        rx_percent = 0
        if rx_dbm is not None:
            clamped = max(-30.0, min(0.0, rx_dbm))
            rx_percent = int(((clamped + 30.0) / 30.0) * 100)

        ports.append({
            "interface": iface_name,
            "rx_dbm": rx_dbm,
            "tx_dbm": tx_dbm,
            "temp_c": temp_c,
            "voltage_v": volt_v,
            "bias_ma": bias_ma,
            "status": status,
            "status_label": status_label,
            "status_desc": status_desc,
            "rx_percent": rx_percent,
            "alarms": alarms,
        })
    return ports


def parse_cisco_c1300_optics(text):
    """Parse optical diagnostics output on Cisco C1300 / CBS / SG switches."""
    ports = []
    # Lines like: gi1/0/25  31.4  3.32  6.4  -5.21  -6.10  OK
    lines = text.splitlines()
    for line in lines:
        line = line.strip()
        m = re.match(r"^(\S+)\s+(-?[\d\.]+)\s+([\d\.]+)\s+([\d\.]+)\s+(-?[\d\.]+)\s+(-?[\d\.]+)(.*)$", line)
        if m:
            iface = m.group(1)
            temp_c = float(m.group(2))
            volt_v = float(m.group(3))
            bias_ma = float(m.group(4))
            tx_dbm = float(m.group(5))
            rx_dbm = float(m.group(6))
            extra = m.group(7).strip()
            alarms = [extra] if extra and extra != "OK" else []

            status, status_label, status_desc = evaluate_optics_health(rx_dbm, alarms)
            clamped = max(-30.0, min(0.0, rx_dbm))
            rx_percent = int(((clamped + 30.0) / 30.0) * 100)

            ports.append({
                "interface": iface,
                "rx_dbm": rx_dbm,
                "tx_dbm": tx_dbm,
                "temp_c": temp_c,
                "voltage_v": volt_v,
                "bias_ma": bias_ma,
                "status": status,
                "status_label": status_label,
                "status_desc": status_desc,
                "rx_percent": rx_percent,
                "alarms": alarms,
            })
    return ports


def parse_cisco_ios_optics(text):
    """Parse optical transceiver output on Cisco IOS switches."""
    ports = []
    # Lines like: Gi1/1/1  31.2  3.28  6.2  -5.3  -6.4
    lines = text.splitlines()
    for line in lines:
        line = line.strip()
        m = re.match(r"^(\S+)\s+(-?[\d\.]+)\s+([\d\.]+)\s+([\d\.]+)\s+(-?[\d\.]+)\s+(-?[\d\.]+)", line)
        if m:
            iface = m.group(1)
            temp_c = float(m.group(2))
            volt_v = float(m.group(3))
            bias_ma = float(m.group(4))
            tx_dbm = float(m.group(5))
            rx_dbm = float(m.group(6))

            status, status_label, status_desc = evaluate_optics_health(rx_dbm, [])
            clamped = max(-30.0, min(0.0, rx_dbm))
            rx_percent = int(((clamped + 30.0) / 30.0) * 100)

            ports.append({
                "interface": iface,
                "rx_dbm": rx_dbm,
                "tx_dbm": tx_dbm,
                "temp_c": temp_c,
                "voltage_v": volt_v,
                "bias_ma": bias_ma,
                "status": status,
                "status_label": status_label,
                "status_desc": status_desc,
                "rx_percent": rx_percent,
                "alarms": [],
            })
    return ports


def query_device_optics(device, on_step=None):
    """Run optical diagnostics on a single switch over SSH. Returns list of port telemetry dicts.
    on_step(text, frac), if given, is told each step so a progress bar can follow it."""
    with DeviceSteps(on_step).session(device) as s:
        s.expect(1)
        if device.is_juniper:
            out, parse = s.run("show interfaces diagnostics optics | no-more"), parse_junos_optics
        elif device.profile.netmiko == "cisco_s300":
            out = s.first("show optical-transceiver-diagnostics", "show fiber-ports optical-transceiver")
            parse = parse_cisco_c1300_optics
        else:
            out = s.first("show interfaces transceiver detail", "show interfaces transceiver")
            parse = parse_cisco_ios_optics
        s.parsing()
        ports = parse(out)

    for p in ports:
        p["switch_id"] = device.id
        p["switch_name"] = device.name
        p["switch_ip"] = device.host
        p["switch_location"] = device.location or "—"
        p["switch_group"] = device.group or "—"
        p["site_code"] = device.site.code if device.site else "—"

    return ports


def stream_scan_optics(app, devices):
    """
    Generator yielding newline-delimited JSON events for live progress and streaming logs.
    """
    raw_ids = [d if isinstance(d, int) else getattr(d, "id", None) for d in devices]
    raw_ids = [i for i in raw_ids if i is not None]

    with app.app_context():
        valid_devs = Device.query.filter(Device.id.in_(raw_ids), Device.enabled == True, Device.credential_id.isnot(None)).all() if raw_ids else []
        dev_ids = [d.id for d in valid_devs]
        total = len(dev_ids)
        workers = scan_workers(total)

    q = queue.Queue()


    q.put({
        "type": "init",
        "total": total,
        "timestamp": timestamp(),
        "msg": f"Starting optical health scan across {total} switches...",
    })

    if not dev_ids:
        q.put({
            "type": "complete",
            "summary": {
                "total_ports": 0, "healthy_count": 0, "warning_count": 0,
                "critical_count": 0, "ports": [], "device_summaries": []
            },
            "timestamp": timestamp(),
            "msg": "No reachable switches available with credentials in this scope.",
        })
        q.put(None)
        yield from ndjson(q)
        return

    results = []
    step = fleet_progress(q, total, timestamp)

    def worker(dev_id):
        with app.app_context():
            d = db.session.get(Device, dev_id)
            if not d:
                return
            dev_name = d.name
            dev_host = d.host
            dev_role = d.role
            dev_loc = d.location or "—"
            dev_grp = d.group or "—"

        q.put({
            "type": "log",
            "level": "info",
            "device": dev_name,
            "timestamp": timestamp(),
            "msg": f"[{dev_name}] Connecting to {dev_host} via SSH...",
        })

        ports = []
        err = None
        try:
            with app.app_context():
                d = db.session.get(Device, dev_id)
                ports = query_device_optics(d, on_step=lambda text, frac: step(dev_id, dev_name, text, frac))
        except Exception as e:
            err = str(e)

        if err:
            q.put({
                "type": "log",
                "level": "error",
                "device": dev_name,
                "timestamp": timestamp(),
                "msg": f"[{dev_name}] SSH / Command Error: {err}",
            })
        elif ports:
            port_labels = ", ".join(f"{p['interface']} ({p['rx_dbm']} dBm)" for p in ports[:3])
            if len(ports) > 3:
                port_labels += f" +{len(ports)-3} more"
            q.put({
                "type": "log",
                "level": "ok",
                "device": dev_name,
                "timestamp": timestamp(),
                "msg": f"[{dev_name}] Scanned {len(ports)} optical transceiver(s): {port_labels}",
            })
        else:
            q.put({
                "type": "log",
                "level": "muted",
                "device": dev_name,
                "timestamp": timestamp(),
                "msg": f"[{dev_name}] No optical transceivers active or installed.",
            })

        step(dev_id, dev_name, "Failed" if err else f"Done, {len(ports)} optical port(s)", 1.0,
             ports_count=len(ports))

        results.append((dev_id, dev_name, dev_host, dev_role, dev_loc, dev_grp, ports, err))

    def run_all():
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(worker, dev_ids))

        all_ports = []
        device_summaries = []
        healthy = 0
        warning = 0
        critical = 0

        for dev_id, dev_name, dev_host, dev_role, dev_loc, dev_grp, dev_ports, err in results:
            h = sum(1 for p in dev_ports if p["status"] == "ok")
            w = sum(1 for p in dev_ports if p["status"] == "warn")
            c = sum(1 for p in dev_ports if p["status"] == "bad")
            healthy += h
            warning += w
            critical += c
            all_ports.extend(dev_ports)
            device_summaries.append({
                "id": dev_id, "name": dev_name, "host": dev_host, "role": dev_role,
                "location": dev_loc, "group": dev_grp, "port_count": len(dev_ports),
                "healthy": h, "warning": w, "critical": c, "error": err,
            })

        order = {"bad": 0, "warn": 1, "ok": 2, "unknown": 3}
        all_ports.sort(key=lambda p: (order.get(p["status"], 9), p["switch_name"], p["interface"]))

        summary = {
            "total_ports": len(all_ports),
            "healthy_count": healthy,
            "warning_count": warning,
            "critical_count": critical,
            "ports": all_ports,
            "device_summaries": device_summaries,
        }

        q.put({
            "type": "log",
            "level": "ok",
            "timestamp": timestamp(),
            "msg": f"Scan completed across {total} switches. Total fiber ports: {len(all_ports)} (Optimal: {healthy}, Warnings: {warning}, Critical: {critical}).",
        })
        q.put({
            "type": "complete",
            "summary": summary,
            "timestamp": timestamp(),
        })
        q.put(None)

    threading.Thread(target=run_all, daemon=True).start()

    yield from ndjson(q)
