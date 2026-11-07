"""Hardware health (CPU, memory, temperature, fans, PSU) per platform, and fleet scans."""
import queue
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from .models import Device, db
from .scanprogress import DeviceSteps, fleet_progress, ndjson, timestamp, scan_workers


TEMP_BAD_C, TEMP_WARN_C = 65, 52
CPU_BAD, CPU_WARN = 90, 75
MEM_BAD, MEM_WARN = 90, 80


def _empty():
    return {"cpu_pct": None, "mem_pct": None, "temp_c": None, "temp_status": "ok", "fans_status": "ok",
            "psu_status": "ok", "sensor_details": []}


def parse_junos_health(re_text, env_text):
    """
    Parses Junos RE and environment output to extract CPU, memory, temperatures, and fan/PSU statuses.
    """
    res = _empty()

    # CPU utilization:
    #   User                      27 percent
    #   Background                 0 percent
    #   Kernel                    35 percent
    #   Interrupt                  1 percent
    #   Idle                      38 percent
    idle_match = re.search(r"Idle\s+(\d+)\s+percent", re_text)
    if idle_match:
        try:
            idle_val = int(idle_match.group(1))
            res["cpu_pct"] = max(0, min(100, 100 - idle_val))
        except (ValueError, TypeError):
            pass

    mem_match = re.search(r"Memory utilization\s+(\d+)\s+percent", re_text)
    if mem_match:
        try:
            res["mem_pct"] = int(mem_match.group(1))
        except (ValueError, TypeError):
            pass

    temp_match = re.search(r"Temperature\s+(\d+)\s+degrees\s+C", re_text)
    if not temp_match:
        temp_match = re.search(r"CPU temperature\s+(\d+)\s+degrees\s+C", re_text)
    if temp_match:
        try:
            res["temp_c"] = int(temp_match.group(1))
        except (ValueError, TypeError):
            pass

    highest_temp = res["temp_c"] or 0
    any_fan_bad = False
    any_psu_bad = False
    any_temp_bad = False

    for line in env_text.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue
        # Class Item Status Measurement
        # Temp FPC 0 CPU OK 37 degrees C / 98 degrees F
        # Fans FPC 0 Fan 1 OK Spinning at normal speed
        # Power FPC 0 Power Supply 0 OK
        t_match = re.search(r"Temp\s+(.+?)\s+(OK|Failed|Testing|Absent|Check)\s+(\d+)\s+degrees\s+C", line_clean, re.I)
        if t_match:
            sensor_name = t_match.group(1).strip()
            s_status = t_match.group(2).upper()
            deg_c = int(t_match.group(3))
            if deg_c > highest_temp:
                highest_temp = deg_c
            if s_status not in ("OK", "TESTING"):
                any_temp_bad = True
            res["sensor_details"].append({"item": sensor_name, "temp_c": deg_c, "status": s_status})
        
        if "fan" in line_clean.lower():
            if "failed" in line_clean.lower() or "not ok" in line_clean.lower():
                any_fan_bad = True
        if "power supply" in line_clean.lower() or "psu" in line_clean.lower():
            if "failed" in line_clean.lower() or "not ok" in line_clean.lower():
                any_psu_bad = True

    if highest_temp > 0:
        res["temp_c"] = highest_temp
        if highest_temp >= TEMP_BAD_C:
            res["temp_status"] = "bad"
        elif highest_temp >= TEMP_WARN_C:
            res["temp_status"] = "warn"
        elif any_temp_bad:
            res["temp_status"] = "warn"
        else:
            res["temp_status"] = "ok"
    elif any_temp_bad:
        res["temp_status"] = "warn"

    res["fans_status"] = "bad" if any_fan_bad else "ok"
    res["psu_status"] = "bad" if any_psu_bad else "ok"

    return res


def parse_cisco_ios_health(cpu_text, mem_text, env_text):
    """
    Parses Cisco IOS commands to extract CPU, memory, and environmental metrics.
    """
    res = _empty()

    # CPU: "CPU utilization for five seconds: 11%/3%; one minute: 11%; five minutes: 11%"
    cpu_match = re.search(r"one minute:\s*(\d+)%", cpu_text)
    if not cpu_match:
        cpu_match = re.search(r"five seconds:\s*(\d+)%", cpu_text)
    if cpu_match:
        try:
            res["cpu_pct"] = int(cpu_match.group(1))
        except (ValueError, TypeError):
            pass

    # Memory: "Processor Pool Total: 71072712 Used: 32301548 Free: 38771164"
    mem_match = re.search(r"Processor Pool Total:\s*(\d+)\s+Used:\s*(\d+)", mem_text)
    if mem_match:
        try:
            total_m = int(mem_match.group(1))
            used_m = int(mem_match.group(2))
            if total_m > 0:
                res["mem_pct"] = round((used_m / total_m) * 100, 1)
        except (ValueError, TypeError):
            pass

    # Environment: FAN is OK, TEMPERATURE is OK, etc.
    env_lower = env_text.lower()
    if "fan is ok" in env_lower or "fans status is ok" in env_lower or "fan 1 is ok" in env_lower:
        res["fans_status"] = "ok"
    elif "fan" in env_lower and ("fault" in env_lower or "fail" in env_lower or "bad" in env_lower):
        res["fans_status"] = "bad"

    if "temperature is ok" in env_lower:
        res["temp_status"] = "ok"
    elif "temperature" in env_lower and ("fault" in env_lower or "fail" in env_lower or "warn" in env_lower or "hot" in env_lower):
        res["temp_status"] = "bad"

    # Check for specific Celsius values e.g. "Inlet Temperature Value: 34 Degree Celsius"
    temp_c_matches = re.findall(r"(\d+)\s*(?:degrees?\s*c|celsius)", env_text, re.I)
    if temp_c_matches:
        try:
            c_vals = [int(v) for v in temp_c_matches if 10 <= int(v) <= 120]
            if c_vals:
                res["temp_c"] = max(c_vals)
                if res["temp_c"] >= TEMP_BAD_C:
                    res["temp_status"] = "bad"
                elif res["temp_c"] >= TEMP_WARN_C:
                    res["temp_status"] = "warn"
        except (ValueError, TypeError):
            pass

    return res


def parse_cisco_c1300_health(cpu_text, env_text):
    """
    Parses Cisco Catalyst 1300 / CBS output.
    """
    res = _empty()

    # "five seconds: 4%; one minute: 4%; five minutes: 3%"
    cpu_match = re.search(r"one minute:\s*(\d+)%", cpu_text)
    if not cpu_match:
        cpu_match = re.search(r"five seconds:\s*(\d+)%", cpu_text)
    if cpu_match:
        try:
            res["cpu_pct"] = int(cpu_match.group(1))
        except (ValueError, TypeError):
            pass

    env_lower = env_text.lower()
    if "fans status is ok" in env_lower or "fan is ok" in env_lower:
        res["fans_status"] = "ok"
    elif "fan" in env_lower and ("fail" in env_lower or "fault" in env_lower):
        res["fans_status"] = "bad"

    if "temperature is ok" in env_lower:
        res["temp_status"] = "ok"
    elif "temperature" in env_lower and ("fail" in env_lower or "warn" in env_lower or "hot" in env_lower):
        res["temp_status"] = "bad"

    if "power supply active" in env_lower or "power is good" in env_lower or "sys pwr good" in env_lower:
        res["psu_status"] = "ok"

    return res


def parse_sonicos_health(status_text):
    """
    Parses SonicOS 'show status' output.
    """
    res = _empty()

    # CPUs: 2.16% - 8 Cores
    cpu_match = re.search(r"CPUs?:\s*([0-9\.]+)%", status_text)
    if cpu_match:
        try:
            res["cpu_pct"] = round(float(cpu_match.group(1)), 1)
        except (ValueError, TypeError):
            pass

    # Connection Usage: 0.311%
    mem_match = re.search(r"Connection Usage:\s*([0-9\.]+)%", status_text)
    if mem_match:
        try:
            res["mem_pct"] = round(float(mem_match.group(1)), 1)
        except (ValueError, TypeError):
            pass

    return res


# Health commands and parser per CLI dialect. Optional commands may fail on older firmware.
_DIALECTS = {
    "junos": (("show chassis routing-engine | no-more", "show chassis environment | no-more"), (), parse_junos_health),
    "c1300": (("show cpu utilization", "show environment all"), (), parse_cisco_c1300_health),
    "ios": (("show processes cpu | include CPU", "show processes memory | include Processor", "show env all"),
            ("show env all",), parse_cisco_ios_health),
    "sonicos": (("show status",), (), parse_sonicos_health),
}


def _dialect(device):
    p = device.profile
    if p.family == "juniper":
        return "junos"
    if p.family == "sonicwall":
        return "sonicos"
    return "c1300" if p.netmiko == "cisco_s300" else "ios"


def query_device_health(device, on_step=None):
    """
    Connects to a single network device and queries CPU, Memory, Temperature, Fan and Power stats.
    Returns a unified dictionary with health metrics. on_step(text, frac), if given, is told each step.
    """
    commands, optional, parse = _DIALECTS[_dialect(device)]
    unknown = device.profile.family == "other"  # unknown platform: try the IOS commands, take what answers
    with DeviceSteps(on_step).session(device) as s:
        s.expect(len(commands))
        outputs = [s.first(c, optional=unknown or c in optional) for c in commands]
        s.parsing()
    data = parse(*outputs)
    return _rated(device, data)


def _rated(device, data):
    status = "ok"
    reasons = []

    if data["cpu_pct"] is not None:
        if data["cpu_pct"] >= CPU_BAD:
            status = "bad"
            reasons.append(f"High CPU: {data['cpu_pct']}%")
        elif data["cpu_pct"] >= CPU_WARN:
            if status != "bad":
                status = "warn"
            reasons.append(f"Elevated CPU: {data['cpu_pct']}%")

    if data["mem_pct"] is not None:
        if data["mem_pct"] >= MEM_BAD:
            status = "bad"
            reasons.append(f"High Memory: {data['mem_pct']}%")
        elif data["mem_pct"] >= MEM_WARN:
            if status != "bad":
                status = "warn"
            reasons.append(f"Elevated Memory: {data['mem_pct']}%")

    if data["temp_status"] == "bad":
        status = "bad"
        reasons.append(f"Thermal Critical: {data.get('temp_c')}°C" if data.get('temp_c') else "Thermal Critical")
    elif data["temp_status"] == "warn":
        if status != "bad":
            status = "warn"
        reasons.append(f"Thermal Warning: {data.get('temp_c')}°C" if data.get('temp_c') else "Thermal Warning")

    if data["fans_status"] == "bad":
        status = "bad"
        reasons.append("Fan Fault Detected")

    if data["psu_status"] == "bad":
        status = "bad"
        reasons.append("PSU / Power Supply Alert")

    data["overall_status"] = status
    data["reasons"] = reasons
    data["device_id"] = device.id
    data["device_name"] = device.name
    data["host"] = device.host
    data["platform"] = device.platform
    data["site_id"] = device.site_id
    data["site_name"] = device.site.name if device.site else "—"
    data["site_code"] = device.site.code if device.site else "—"
    return data


def stream_scan_health(app, device_ids):
    """
    Generator streaming real-time hardware health scan results across network devices.
    """

    total = len(device_ids)
    q = queue.Queue()


    step = fleet_progress(q, total, timestamp)

    def worker(did):
        with app.app_context():
            dev = db.session.get(Device, did)
            if not dev:
                return
            dev_name = dev.name
            dev_host = dev.host
            dev_platform = dev.platform
            site_code = dev.site.code if dev.site else "—"

        q.put({
            "type": "log",
            "level": "info",
            "device": dev_name,
            "timestamp": timestamp(),
            "msg": f"[{dev_name}] Polling CPU, Memory, and Temperature stats ({dev_host})...",
        })

        try:
            with app.app_context():
                d = db.session.get(Device, did)
                health_data = query_device_health(d, on_step=lambda text, frac: step(did, dev_name, text, frac))

            q.put({
                "type": "device",
                "device_id": did,
                "data": health_data,
                "timestamp": timestamp(),
            })
            step(did, dev_name, f"Done, {health_data['overall_status'].upper()}", 1.0)

            cpu_s = f"{health_data['cpu_pct']}%" if health_data['cpu_pct'] is not None else "N/A"
            mem_s = f"{health_data['mem_pct']}%" if health_data['mem_pct'] is not None else "N/A"
            temp_s = f"{health_data['temp_c']}°C" if health_data['temp_c'] is not None else health_data['temp_status'].upper()
            q.put({
                "type": "log",
                "level": "ok" if health_data["overall_status"] == "ok" else ("warn" if health_data["overall_status"] == "warn" else "error"),
                "device": dev_name,
                "timestamp": timestamp(),
                "msg": f"[{dev_name}] CPU: {cpu_s} | Mem: {mem_s} | Temp: {temp_s} | Status: {health_data['overall_status'].upper()}",
            })

        except Exception as e:
            q.put({
                "type": "device",
                "device_id": did,
                "data": {
                    "device_id": did,
                    "device_name": dev_name,
                    "host": dev_host,
                    "platform": dev_platform,
                    "site_code": site_code,
                    "cpu_pct": None,
                    "mem_pct": None,
                    "temp_c": None,
                    "temp_status": "unknown",
                    "fans_status": "unknown",
                    "psu_status": "unknown",
                    "overall_status": "error",
                    "reasons": [str(e)],
                    "error": str(e)
                },
                "timestamp": timestamp(),
            })
            q.put({
                "type": "log",
                "level": "error",
                "device": dev_name,
                "timestamp": timestamp(),
                "msg": f"[{dev_name}] Connection error: {e}",
            })
            step(did, dev_name, "Failed", 1.0)

    def run_all():
        try:
            with app.app_context():
                workers_n = scan_workers(total)
            q.put({"type": "start", "total": total, "timestamp": timestamp()})
            with ThreadPoolExecutor(max_workers=workers_n) as pool:
                list(pool.map(worker, device_ids))
            q.put({"type": "done", "timestamp": timestamp()})
        except Exception as e:
            q.put({"type": "log", "level": "error", "msg": f"Hardware health scan failure: {e}", "timestamp": timestamp()})
            q.put({"type": "done", "timestamp": timestamp()})
        finally:
            q.put(None)

    threading.Thread(target=run_all, daemon=True).start()
    yield from ndjson(q)
