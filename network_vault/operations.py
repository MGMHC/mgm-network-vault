import logging
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from flask import current_app
from . import engine
from .models import Device, Backup, db, get_int
from .util import read_backup_file

logger = logging.getLogger("network_vault.operations")


# --------------------------------------------------------------------------- MAC / IP Utilities & OUI Catalog

OUI_CATALOG = {
    # Networking Infrastructure
    "00:04:96": "Extreme Networks",
    "00:08:e3": "Cisco Systems",
    "00:10:db": "Juniper Networks",
    "00:1b:0d": "Cisco Systems",
    "00:1f:12": "Cisco Systems",
    "00:21:59": "Juniper Networks",
    "00:26:88": "Juniper Networks",
    "00:30:b6": "Cisco Systems",
    "00:40:96": "Cisco Systems",
    "00:60:2f": "Cisco Systems",
    "00:e0:14": "Cisco Systems",
    "00:e0:52": "Juniper Networks",
    "08:5b:0e": "Juniper Networks",
    "24:8a:07": "Juniper Networks",
    "2c:6b:f5": "Juniper Networks",
    "30:7b:ac": "Cisco Systems",
    "40:b4:f0": "Juniper Networks",
    "44:f4:77": "Juniper Networks",
    "50:c5:8d": "Juniper Networks",
    "54:4b:8c": "Juniper Networks",
    "5c:5e:ab": "Juniper Networks",
    "60:9c:9f": "Juniper Networks",
    "70:70:8b": "Juniper Networks",
    "7c:25:86": "Juniper Networks",
    "80:71:1f": "Juniper Networks",
    "84:18:88": "Juniper Networks",
    "84:c1:c1": "Juniper Networks",
    "88:e0:f3": "Juniper Networks",
    "9c:cc:83": "Juniper Networks",
    "a8:d0:e5": "Juniper Networks",
    "ac:4b:c8": "Juniper Networks",
    "b0:a8:6e": "Juniper Networks",
    "b8:c2:53": "Juniper Networks",
    "cc:e1:7f": "Juniper Networks",
    "dc:38:e1": "Juniper Networks",
    "ec:13:db": "Juniper Networks",
    "f0:1c:2d": "Juniper Networks",
    "f4:b5:2f": "Juniper Networks",
    "00:0b:86": "Aruba Networks / HPE",
    "00:1a:1e": "Aruba Networks / HPE",
    "00:24:6c": "Aruba Networks / HPE",
    "20:4c:03": "Aruba Networks / HPE",
    "94:b4:0f": "Aruba Networks / HPE",
    "d8:c7:c8": "Aruba Networks / HPE",
    "00:09:0f": "Fortinet",
    "00:1f:f8": "Palo Alto Networks",
    "08:66:98": "Palo Alto Networks",
    "70:30:18": "Fortinet",

    # Virtualization & Servers
    "00:05:69": "VMware",
    "00:0c:29": "VMware",
    "00:1c:14": "VMware",
    "00:50:56": "VMware",
    "00:15:5d": "Microsoft Hyper-V",
    "52:54:00": "QEMU / KVM",
    "00:1e:67": "Intel",
    "00:15:17": "Intel",
    "00:21:70": "Dell EMC",
    "00:24:e8": "Dell EMC",
    "18:03:73": "Dell EMC",
    "78:2b:cb": "Dell EMC",
    "00:02:b3": "Intel",
    "00:1b:21": "Intel",
    "00:26:b9": "Dell EMC",
    "ec:f4:bb": "Dell EMC",
    "00:11:85": "Hewlett Packard Enterprise",
    "3c:d9:2b": "Hewlett Packard Enterprise",

    # Healthcare & Medical Devices
    "00:01:4a": "Philips Medical Systems",
    "00:09:fb": "Philips Healthcare",
    "00:17:61": "Philips Medical Systems",
    "00:24:e4": "Philips Healthcare",
    "28:51:32": "Philips Medical Systems",
    "00:07:f6": "GE Healthcare",
    "00:13:02": "GE Healthcare",
    "00:21:05": "GE Medical Systems",
    "00:50:c2": "GE Healthcare",
    "30:34:2d": "GE Healthcare",
    "00:0c:15": "Siemens Healthineers",
    "00:1a:64": "Siemens Medical Solutions",
    "00:26:4d": "Siemens Healthcare",
    "00:a0:8e": "Mindray Medical",
    "70:b3:d5": "Mindray Bio-Medical",
    "00:08:7c": "Draeger Medical",
    "00:18:23": "Draeger Medical",
    "00:0d:f0": "Baxter Healthcare",
    "00:23:a7": "Hill-Rom Medical",
    "00:1d:92": "Masimo Patient Monitoring",
    "00:16:44": "Nihon Kohden",

    # Client Workstations, Phones, Cameras & IoT
    "00:17:88": "Philips Lighting / IoT",
    "00:03:93": "Apple",
    "00:05:02": "Apple",
    "00:0a:95": "Apple",
    "00:10:fa": "Apple",
    "00:1e:c2": "Apple",
    "00:25:00": "Apple",
    "04:0c:ce": "Apple",
    "14:10:9f": "Apple",
    "3c:06:30": "Apple",
    "a4:83:e7": "Apple",
    "ac:bc:32": "Apple",
    "00:1b:63": "Apple",
    "00:21:6a": "Intel Mobile",
    "00:24:d7": "Intel Wireless",
    "60:57:18": "Intel Wireless",
    "80:86:f2": "Intel Wireless",
    "00:04:f2": "Polycom IP Phones",
    "00:15:65": "Yealink IP Phones",
    "80:5e:c0": "Yealink IP Phones",
    "00:04:0d": "Avaya IP Phones",
    "00:40:8c": "Axis Network Cameras",
    "ac:cc:8e": "Axis Network Cameras",
    "44:19:b6": "Hikvision Digital Cameras",
    "bc:ad:28": "Hikvision Digital Cameras",
    "bc:54:51": "Dahua Security Cameras",
    "00:07:4d": "Zebra Technologies (Printers/Scanners)",
    "00:15:70": "Zebra Technologies",
}


def clean_mac(s):
    """Normalize MAC address string to clean lowercase 12 hex chars."""
    if not s:
        return ""
    clean = re.sub(r"[^0-9a-fA-F]", "", str(s)).lower()
    return clean if len(clean) == 12 else ""


def format_mac(clean, fmt="colon"):
    """Format 12-char hex MAC into colon (00:11:22:33:44:55) or cisco (0011.2233.4455)."""
    if not clean or len(clean) != 12:
        return clean or ""
    if fmt == "cisco":
        return f"{clean[0:4]}.{clean[4:8]}.{clean[8:12]}"
    if fmt == "dash":
        return "-".join(clean[i:i+2] for i in range(0, 12, 2))
    return ":".join(clean[i:i+2] for i in range(0, 12, 2))


def is_ip(s):
    """Check if string is a valid IPv4 address."""
    s = (s or "").strip()
    parts = s.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def lookup_oui(clean_mac_str):
    """Return recognized hardware vendor / manufacturer name."""
    if not clean_mac_str or len(clean_mac_str) < 6:
        return "Unknown"
    prefix = ":".join(clean_mac_str[i:i+2] for i in range(0, 6, 2))
    return OUI_CATALOG.get(prefix, "Unregistered / Generic Device")


_PORT_CLASSIFICATION_CACHE = {}
_APP_REF = None

def clear_port_classification_cache():
    """Clear cached port classifications."""
    _PORT_CLASSIFICATION_CACHE.clear()

def is_uplink_port(port_name, device=None, device_id=None, learned_mac_count=1, port_config_snippet=None, app=None):
    """
    Determine whether an interface is an uplink/LAG/trunk rather than an edge access port.
    Uses:
    1. Interface name conventions (ae, po, port-channel, eth-trunk, lag, trunk, bundle-ether, bond)
    2. Number of learned MAC addresses (> 2)
    3. Core / Distribution switch role (interconnect fabric hops)
    4. Device configuration (from latest backup or live config):
       - Trunk mode (port-mode trunk / interface-mode trunk / switchport mode trunk)
       - Uplink / transit descriptions (UPLINK, TRUNK, DISTRIBUTION, CSW, DSW, CORE, TO-DIST, TO-CORE, etc.)
       - 802.3ad / channel-group memberships
       - Trunk VLAN memberships ('vlan members all' / multiple tagged VLANs / native-vlan-id)
    """
    p = (port_name or "").lower().strip()
    clean_p = p.split(".")[0].strip()
    if not clean_p or clean_p == "—":
        return False

    # 1. Obvious LAG / virtual trunk interface names
    if clean_p.startswith(("ae", "po", "port-channel", "eth-trunk", "lag", "trunk", "bundle-ether", "bond")):
        return True

    # 2. If multiple MACs learned on physical port, it's a trunk or downstream switch
    if learned_mac_count > 2:
        return True

    # 3. Check live configuration snippet if provided
    if port_config_snippet:
        snip_lower = port_config_snippet.lower()
        if any(k in snip_lower for k in [
            "port-mode trunk", "interface-mode trunk", "switchport mode trunk",
            "vlan members all", "802.3ad", "channel-group", "native-vlan-id"
        ]):
            return True
        if re.search(r"description\s+.*(uplink|trunk|dist|distribution|dsw|csw|core|to[-_]dist|to[-_]core|transit|cascad|interconnect|peer|backbone|switch|sw[-_])", snip_lower, re.I):
            return True

    # 4. Check device backed-up configuration in Network Vault
    dev_id = device_id or (device.id if device else None)
    if not dev_id:
        return False

    cache_key = (dev_id, clean_p)
    if cache_key in _PORT_CLASSIFICATION_CACHE:
        return _PORT_CLASSIFICATION_CACHE[cache_key]

    global _APP_REF
    from flask import has_app_context, current_app
    ctx = None
    target_app = app or _APP_REF
    if not has_app_context():
        if target_app:
            ctx = target_app.app_context()
            ctx.push()
        else:
            try:
                if current_app:
                    ctx = current_app.app_context()
                    ctx.push()
            except Exception:
                pass
            if not ctx:
                try:
                    from . import create_app
                    target_app = create_app()
                    _APP_REF = target_app
                    ctx = target_app.app_context()
                    ctx.push()
                except Exception:
                    pass

    try:
        dev_obj = device or db.session.get(Device, dev_id)
        is_core_or_dist = dev_obj and dev_obj.role in ("core", "distribution")

        b = Backup.query.filter(Backup.device_id == dev_id).order_by(Backup.created_at.desc()).first()
        # Check Junos set-format
        pattern_alts = [re.escape(clean_p)]
        m_hyphen = re.match(r"^([a-zA-Z]+)(?:-)?(\d+/\d+.*)$", clean_p)
        if m_hyphen:
            p_letters, p_num = m_hyphen.groups()
            pattern_alts.append(re.escape(f"{p_letters}-{p_num}"))
            pattern_alts.append(re.escape(f"{p_letters}{p_num}"))
        junos_regex_str = rf"set interfaces (?:{'|'.join(set(pattern_alts))})(?:[\s\.\:]|$)(.*)"

        if b:
            # Check Junos set-format
            if b.set_path:
                set_txt = read_backup_file(b.set_path)
                if set_txt:
                    pattern = re.compile(junos_regex_str, re.I)
                    lines = pattern.findall(set_txt)
                    if lines:
                        joined = " ".join(lines).lower()
                        # Trunk keywords
                        if any(k in joined for k in [
                            "port-mode trunk", "interface-mode trunk",
                            "802.3ad", "vlan members all", "native-vlan-id"
                        ]):
                            _PORT_CLASSIFICATION_CACHE[cache_key] = True
                            return True
                        # Multiple VLANs
                        if len(re.findall(r"vlan\s+members", joined)) > 1 or re.search(r"members\s+\[.*\]", joined):
                            _PORT_CLASSIFICATION_CACHE[cache_key] = True
                            return True
                        # Description matching uplink keywords
                        if re.search(r"description\s+.*(uplink|trunk|dist|distribution|dsw|csw|core|to[-_]dist|to[-_]core|transit|cascad|interconnect|peer|backbone|switch|sw[-_])", joined, re.I):
                            _PORT_CLASSIFICATION_CACHE[cache_key] = True
                            return True
                        # Explicit access mode
                        if "port-mode access" in joined or "interface-mode access" in joined:
                            _PORT_CLASSIFICATION_CACHE[cache_key] = False
                            return False

            # Check Junos / Cisco hierarchical conf format
            if b.path:
                conf_txt = read_backup_file(b.path)
                if conf_txt:
                    # Cisco interface block (supporting abbreviations like Fa4/0/48 for FastEthernet4/0/48, Gi for GigabitEthernet, Te for TenGigabitEthernet, Po for Port-channel)
                    m_pfx = re.match(r"^([a-zA-Z\-]+)\s*([\d\/\.\:]+)$", clean_p)
                    if m_pfx:
                        pfx, slot = m_pfx.groups()
                        cisco_pattern = rf"interface\s+(?:{re.escape(clean_p)}|{re.escape(pfx)}[a-zA-Z]*\s*{re.escape(slot)})\b(.*?)(?=\ninterface|\n!|\Z)"
                    else:
                        cisco_pattern = rf"interface\s+{re.escape(clean_p)}\b(.*?)(?=\ninterface|\n!|\Z)"

                    m_cisco = re.search(cisco_pattern, conf_txt, re.S | re.I)
                    if m_cisco:
                        block = m_cisco.group(1).lower()
                        if any(k in block for k in [
                            "switchport mode trunk", "switchport trunk", "channel-group", "portfast trunk"
                        ]):
                            _PORT_CLASSIFICATION_CACHE[cache_key] = True
                            return True
                        if re.search(r"description\s+.*(uplink|trunk|dist|distribution|dsw|csw|core|to[-_]dist|to[-_]core|transit|cascad|interconnect|peer|backbone|switch|sw[-_])", block, re.I):
                            _PORT_CLASSIFICATION_CACHE[cache_key] = True
                            return True
                        if "switchport mode access" in block:
                            _PORT_CLASSIFICATION_CACHE[cache_key] = False
                            return False

                    # Junos conf interface block (brace balancing)
                    m_junos = None
                    for alt_p in set(pattern_alts):
                        m_junos = re.search(rf"\b{alt_p}\s*\{{", conf_txt, re.I)
                        if m_junos:
                            break
                    if m_junos:
                        start = m_junos.end() - 1
                        depth = 0
                        end = len(conf_txt)
                        for i in range(start, len(conf_txt)):
                            if conf_txt[i] == '{':
                                depth += 1
                            elif conf_txt[i] == '}':
                                depth -= 1
                                if depth == 0:
                                    end = i + 1
                                    break
                        block = conf_txt[start:end].lower()
                        if any(k in block for k in [
                            "port-mode trunk", "interface-mode trunk", "members all",
                            "802.3ad", "native-vlan-id"
                        ]):
                            _PORT_CLASSIFICATION_CACHE[cache_key] = True
                            return True
                        if len(re.findall(r"members\s+", block)) > 1 or re.search(r"members\s+\[.*\]", block):
                            _PORT_CLASSIFICATION_CACHE[cache_key] = True
                            return True
                        if re.search(r"description\s+.*(uplink|trunk|dist|distribution|dsw|csw|core|to[-_]dist|to[-_]core|transit|cascad|interconnect|peer|backbone|switch|sw[-_])", block, re.I):
                            _PORT_CLASSIFICATION_CACHE[cache_key] = True
                            return True
                        if "port-mode access" in block or "interface-mode access" in block:
                            _PORT_CLASSIFICATION_CACHE[cache_key] = False
                            return False

        if is_core_or_dist:
            _PORT_CLASSIFICATION_CACHE[cache_key] = True
            return True

        _PORT_CLASSIFICATION_CACHE[cache_key] = False
        return False
    except Exception as e:
        logger.debug(f"Error checking uplink status for {dev_id}/{clean_p}: {e}")
        return False
    finally:
        if ctx:
            ctx.pop()


# --------------------------------------------------------------------------- ARP Table Discovery

def query_arp(device, target_ip=None, target_mac_clean=None):
    """Query ARP table on switch. Returns list of dicts: [{ip, mac, clean_mac, interface, vlan}]."""
    results = []
    try:
        conn = engine.connect(device)
        try:
            out = ""
            if device.is_juniper:
                if target_ip:
                    try:
                        out = engine.run(conn, f"show arp no-resolve | match {target_ip} | no-more")
                    except Exception:
                        out = ""
                if not out.strip():
                    out = engine.run(conn, "show arp no-resolve | no-more")
            else:
                if target_ip:
                    try:
                        out = engine.run(conn, f"show ip arp | include {target_ip}")
                    except Exception:
                        out = ""
                if not out.strip():
                    out = engine.run(conn, "show ip arp")
        finally:
            try:
                conn.disconnect()
            except Exception:
                pass
    except Exception:
        return results

    mac_regex = re.compile(
        r"\b([0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}|[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4})\b"
    )
    ip_regex = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")

    for line in out.splitlines():
        line = line.strip()
        if not line or "incomplete" in line.lower():
            continue

        m_ip = ip_regex.search(line)
        m_mac = mac_regex.search(line)
        if not m_ip or not m_mac:
            continue

        ip = m_ip.group(1)
        cm = clean_mac(m_mac.group(1))
        if not cm:
            continue
        if target_mac_clean and cm != target_mac_clean:
            continue
        if target_ip and ip != target_ip:
            continue

        tokens = line.split()
        iface = "—"
        for t in reversed(tokens):
            if t != ip and clean_mac(t) != cm and not t.isdigit():
                iface = t.strip("[]")
                break

        results.append({
            "ip": ip,
            "mac": format_mac(cm, "colon"),
            "clean_mac": cm,
            "interface": iface,
            "switch_id": device.id,
            "switch_name": device.name,
        })

    return results


# --------------------------------------------------------------------------- MAC Address Table Discovery

def query_mac_table(device, target_mac_clean=None):
    """Query MAC address forwarding table. Returns list of dicts: [{mac, clean_mac, vlan, interface, type, age}]."""
    results = []
    try:
        conn = engine.connect(device)
        try:
            out = ""
            if device.is_juniper:
                if target_mac_clean:
                    c_mac = format_mac(target_mac_clean, "colon")
                    try:
                        out = engine.run(conn, f"show ethernet-switching table | match {c_mac} | no-more")
                    except Exception:
                        out = ""
                if not out.strip():
                    out = engine.run(conn, "show ethernet-switching table | no-more")
            else:
                if target_mac_clean:
                    cisco_mac = format_mac(target_mac_clean, "cisco")
                    colon_mac = format_mac(target_mac_clean, "colon")
                    try:
                        out = engine.run(conn, f"show mac address-table address {cisco_mac}")
                    except Exception:
                        try:
                            out = engine.run(conn, f"show mac-address-table address {cisco_mac}")
                        except Exception:
                            try:
                                out = engine.run(conn, f"show mac address-table | include {cisco_mac}|{colon_mac}")
                            except Exception:
                                out = ""
                if not out.strip():
                    try:
                        out = engine.run(conn, "show mac address-table")
                    except Exception:
                        try:
                            out = engine.run(conn, "show mac-address-table")
                        except Exception:
                            out = ""
        finally:
            try:
                conn.disconnect()
            except Exception:
                pass
    except Exception:
        return results

    mac_regex = re.compile(
        r"\b([0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}|[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4})\b"
    )
    iface_regex = re.compile(
        r"^(ge|xe|et|fe|ae|gi|fa|te|po|eth|port-channel)[\w\.\-/]+|\d+/\d+", re.I
    )

    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith(("-", "Ethernet-switching", "Routing", "MAC flags", "Bridging")):
            continue

        m_mac = mac_regex.search(line)
        if not m_mac:
            continue

        raw_mac = m_mac.group(1)
        cm = clean_mac(raw_mac)
        if not cm:
            continue
        if target_mac_clean and cm != target_mac_clean:
            continue

        tokens = line.split()
        mac_idx = -1
        for idx, t in enumerate(tokens):
            if clean_mac(t) == cm:
                mac_idx = idx
                break

        # Detect VLAN name or number
        vlan = "default"
        if mac_idx > 0:
            vlan = tokens[0]

        # Detect interface (e.g. ge-3/0/13.0 or gi1/0/14)
        iface = "—"
        for t in tokens:
            if t != raw_mac and iface_regex.search(t):
                iface = t
                break

        if iface == "—" and len(tokens) >= 3:
            candidate = tokens[-1]
            if candidate != raw_mac and candidate.lower() not in ("learn", "dynamic", "static"):
                iface = candidate

        # Clean interface: strip .0 subinterface suffix (e.g. ge-3/0/13.0 -> ge-3/0/13)
        if iface.endswith(".0"):
            iface = iface[:-2]

        # Detect type & age (e.g. Learn, 2:07)
        m_type = "dynamic"
        age = "—"
        for t in tokens:
            if t.lower() in ("learn", "dynamic", "static"):
                m_type = t
            elif re.search(r"^\d+:\d+$|^\d+$", t) and t != raw_mac:
                age = t

        results.append({
            "mac": format_mac(cm, "colon"),
            "clean_mac": cm,
            "vlan": vlan,
            "interface": iface,
            "type": m_type,
            "age": age,
            "switch_id": device.id,
            "switch_name": device.name,
            "switch_ip": device.host,
            "switch_location": device.location or "—",
            "switch_group": device.group or "—",
            "switch_role": device.role,
        })

    return results


# --------------------------------------------------------------------------- Parallel Port Locator Engine

def locate_host(devices, query_str, user=None, app=None):
    """
    Search across switches for a host by IP or MAC address.
    Thread-safe with Flask application context.
    """
    app = app or (current_app._get_current_object() if current_app else None)
    q = (query_str or "").strip()
    if not q:
        raise ValueError("Please provide an IP address or MAC address to locate.")

    target_ip = None
    target_mac_clean = None

    if is_ip(q):
        target_ip = q
        q_type = "ip"
    else:
        target_mac_clean = clean_mac(q)
        if not target_mac_clean:
            raise ValueError(f"'{q}' is neither a valid IPv4 address nor a recognized MAC address.")
        q_type = "mac"

    # Only query enabled devices that have credentials
    target_devices = [d for d in devices if d.enabled and d.credential_id]
    if not target_devices:
        return {
            "query": q,
            "query_type": q_type,
            "resolved_ip": target_ip,
            "resolved_mac": format_mac(target_mac_clean, "colon") if target_mac_clean else None,
            "vendor": lookup_oui(target_mac_clean) if target_mac_clean else "Unknown",
            "best_match": None,
            "all_matches": [],
            "switches_queried": 0,
            "switches_responding": 0,
            "message": "No reachable switches available with credentials in this scope.",
        }

    workers = min(len(target_devices), max(2, get_int("max_workers") if app else 4))

    # Step 1: If searching by IP, query ARP table on core/distribution switches first
    resolved_ip = target_ip
    resolved_mac = format_mac(target_mac_clean, "colon") if target_mac_clean else None

    if target_ip and not target_mac_clean:
        core_dev_ids = [d.id for d in sorted(target_devices, key=lambda d: 0 if d.role in ("core", "distribution") else 1)]

        def arp_worker(did):
            if not app:
                return []
            with app.app_context():
                d = db.session.get(Device, did)
                if not d or not d.credential_id:
                    return []
                return query_arp(d, target_ip=target_ip)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            arp_results = list(pool.map(arp_worker, core_dev_ids))

        for entries in arp_results:
            if entries:
                target_mac_clean = entries[0]["clean_mac"]
                resolved_mac = entries[0]["mac"]
                break

    if not target_mac_clean:
        return {
            "query": q,
            "query_type": q_type,
            "resolved_ip": target_ip,
            "resolved_mac": None,
            "vendor": "Unknown",
            "best_match": None,
            "all_matches": [],
            "switches_queried": len(target_devices),
            "switches_responding": len(target_devices),
            "message": f"Host IP {target_ip} not found in ARP tables on campus switches. Host may be offline or in another subnet.",
        }

    # If searched by MAC directly, attempt to resolve its IP from ARP as well
    if not resolved_ip and target_mac_clean:
        try:
            core_dev_ids = [d.id for d in sorted(target_devices, key=lambda d: 0 if d.role in ("core", "distribution") else 1)]

            def arp_mac_worker(did):
                if not app:
                    return []
                with app.app_context():
                    d = db.session.get(Device, did)
                    if not d or not d.credential_id:
                        return []
                    return query_arp(d, target_mac_clean=target_mac_clean)

            with ThreadPoolExecutor(max_workers=workers) as pool:
                arp_results = list(pool.map(arp_mac_worker, core_dev_ids))

            for entries in arp_results:
                if entries:
                    resolved_ip = entries[0]["ip"]
                    break
        except Exception:
            pass

    # Step 2: Now that we have the target MAC, search MAC forwarding tables across all devices
    vendor = lookup_oui(target_mac_clean)
    all_dev_ids = [d.id for d in target_devices]

    def mac_worker(did):
        if not app:
            return []
        with app.app_context():
            d = db.session.get(Device, did)
            if not d or not d.credential_id:
                return []
            return query_mac_table(d, target_mac_clean=target_mac_clean)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        mac_results_nested = list(pool.map(mac_worker, all_dev_ids))

    all_matches = []
    responding_count = 0
    for res_list in mac_results_nested:
        if res_list is not None:
            responding_count += 1
            all_matches.extend(res_list)

    global _APP_REF
    if app:
        _APP_REF = app

    # Step 3: Classify each match as Edge Port vs Uplink / LAG
    for m in all_matches:
        is_uplink = is_uplink_port(m["interface"], device_id=m.get("switch_id"), app=app)
        m["is_uplink"] = is_uplink
        m["link_type"] = "Uplink / Trunk" if is_uplink else "Edge (Access Port)"
        m["vendor"] = vendor
        m["resolved_ip"] = resolved_ip

    def match_score(m):
        score = 0
        if not m["is_uplink"]:
            score += 100
        if m["switch_role"] == "access":
            score += 50
        elif m["switch_role"] == "distribution":
            score += 20
        return score

    all_matches.sort(key=match_score, reverse=True)
    best_match = all_matches[0] if all_matches else None

    return {
        "query": q,
        "query_type": q_type,
        "resolved_ip": resolved_ip,
        "resolved_mac": resolved_mac or format_mac(target_mac_clean, "colon"),
        "vendor": vendor,
        "best_match": best_match,
        "all_matches": all_matches,
        "switches_queried": len(target_devices),
        "switches_responding": responding_count,
    }


_LOCATOR_RUNS = {}
_LOCATOR_LOCK = threading.Lock()

def get_locator_run(run_id):
    """Retrieve state of an active or recent port locator trace."""
    with _LOCATOR_LOCK:
        run = _LOCATOR_RUNS.get(run_id)
        if not run:
            return None
        return {
            "id": run.get("id"),
            "query": run.get("query"),
            "status": run.get("status"),
            "is_running": run.get("is_running"),
            "percent": run.get("percent", 0),
            "done": run.get("done", 0),
            "total": run.get("total", 0),
            "device": run.get("device", ""),
            "matches_found": run.get("matches_found", 0),
            "resolved_ip": run.get("resolved_ip"),
            "resolved_mac": run.get("resolved_mac"),
            "logs": list(run.get("logs", [])),
            "result": run.get("result"),
            "error": run.get("error"),
            "started_at": run.get("started_at"),
        }

def cancel_locator_run(run_id):
    """Signal cancellation for a running locator trace."""
    with _LOCATOR_LOCK:
        run = _LOCATOR_RUNS.get(run_id)
        if run:
            run["status"] = "cancelled"
            run["is_running"] = False
            return True
        return False


def stream_locate_host(app, devices, query_str, run_id=None):
    """
    Generator yielding newline-delimited JSON events with real-time logging and progress for port locator.
    Maintains persistent background state in _LOCATOR_RUNS so the trace continues across page navigation.
    """
    import json
    q_str = (query_str or "").strip()
    raw_ids = [d if isinstance(d, int) else getattr(d, "id", None) for d in devices]
    raw_ids = [i for i in raw_ids if i is not None]

    with app.app_context():
        valid_devs = Device.query.filter(Device.id.in_(raw_ids), Device.enabled == True, Device.credential_id.isnot(None)).all() if raw_ids else []
        core_dev_ids = [d.id for d in sorted(valid_devs, key=lambda d: 0 if d.role in ("core", "distribution") else 1)]
        all_dev_ids = [d.id for d in valid_devs]
        total = len(all_dev_ids)

    run_id = run_id or f"trace_{int(time.time()*1000)}"

    with _LOCATOR_LOCK:
        # Purge runs older than 2 hours to avoid memory leaks
        now_ts = time.time()
        for k in list(_LOCATOR_RUNS.keys()):
            if now_ts - _LOCATOR_RUNS[k].get("started_at", now_ts) > 7200:
                _LOCATOR_RUNS.pop(k, None)

        _LOCATOR_RUNS[run_id] = {
            "id": run_id,
            "query": q_str,
            "status": "running",
            "is_running": True,
            "percent": 0,
            "done": 0,
            "total": total,
            "device": "",
            "matches_found": 0,
            "resolved_ip": None,
            "resolved_mac": None,
            "logs": [],
            "result": None,
            "error": None,
            "started_at": now_ts,
        }

    q = queue.Queue()

    def timestamp():
        return datetime.now().strftime("%H:%M:%S")

    def emit_log(level, msg, ts=None):
        t_str = ts or timestamp()
        entry = {
            "type": "log",
            "level": level,
            "timestamp": t_str,
            "msg": msg,
        }
        with _LOCATOR_LOCK:
            if run_id in _LOCATOR_RUNS:
                _LOCATOR_RUNS[run_id]["logs"].append({
                    "timestamp": t_str,
                    "level": level,
                    "msg": msg,
                })
        q.put(entry)

    global _APP_REF
    _APP_REF = app

    def run_tracer():
        ctx = app.app_context()
        ctx.push()
        try:
            init_msg = f"Starting trace for '{q_str}' across {total} campus switches..."
            with _LOCATOR_LOCK:
                if run_id in _LOCATOR_RUNS:
                    _LOCATOR_RUNS[run_id]["logs"].append({
                        "timestamp": timestamp(),
                        "level": "info",
                        "msg": init_msg,
                    })

            q.put({
                "type": "init",
                "run_id": run_id,
                "total": total,
                "timestamp": timestamp(),
                "msg": init_msg,
            })

            if not q_str:
                emit_log("error", "Search target is empty.")
                q.put({
                    "type": "error",
                    "timestamp": timestamp(),
                    "msg": "Search target is empty.",
                })
                q.put(None)
                return

            if not all_dev_ids:
                emit_log("error", "No reachable switches available with credentials in this scope.")
                q.put({
                    "type": "error",
                    "timestamp": timestamp(),
                    "msg": "No reachable switches available with credentials in this scope.",
                })
                q.put(None)
                return

            if is_ip(q_str):
                target_ip = q_str
                target_mac_clean = None
                with _LOCATOR_LOCK:
                    if run_id in _LOCATOR_RUNS:
                        _LOCATOR_RUNS[run_id]["resolved_ip"] = target_ip
                emit_log("info", f"[Step 1/2] IP target detected: {target_ip}. Querying ARP on Core & Distribution switches...")
            else:
                target_ip = None
                target_mac_clean = clean_mac(q_str)
                if not target_mac_clean:
                    err_msg = f"'{q_str}' is neither a valid IPv4 address nor a recognized MAC address."
                    emit_log("error", err_msg)
                    q.put({
                        "type": "error",
                        "timestamp": timestamp(),
                        "msg": err_msg,
                    })
                    q.put(None)
                    return
                with _LOCATOR_LOCK:
                    if run_id in _LOCATOR_RUNS:
                        _LOCATOR_RUNS[run_id]["resolved_mac"] = format_mac(target_mac_clean, "colon")
                emit_log("info", f"[Step 1/2] MAC target detected: {format_mac(target_mac_clean)}. Querying ARP to resolve assigned IP...")

            with app.app_context():
                workers = min(total, max(2, get_int("max_workers")))
            resolved_ip = target_ip
            resolved_mac = format_mac(target_mac_clean, "colon") if target_mac_clean else None

            # Step 1: ARP resolution
            def arp_worker(did):
                with app.app_context():
                    d = db.session.get(Device, did)
                    if not d or not d.credential_id:
                        return None, []
                    return d.name, query_arp(d, target_ip=target_ip, target_mac_clean=target_mac_clean)

            with ThreadPoolExecutor(max_workers=workers) as pool:
                arp_results = list(pool.map(arp_worker, core_dev_ids))

            for dev_name, entries in arp_results:
                if entries:
                    if not target_mac_clean:
                        target_mac_clean = entries[0]["clean_mac"]
                        resolved_mac = entries[0]["mac"]
                    if not resolved_ip:
                        resolved_ip = entries[0]["ip"]
                    with _LOCATOR_LOCK:
                        if run_id in _LOCATOR_RUNS:
                            _LOCATOR_RUNS[run_id]["resolved_ip"] = resolved_ip
                            _LOCATOR_RUNS[run_id]["resolved_mac"] = resolved_mac
                    emit_log("ok", f"[{dev_name}] ARP Resolved: IP {entries[0]['ip']} <-> MAC {entries[0]['mac']} (Interface: {entries[0]['interface']})")
                    break

            if not target_mac_clean:
                emit_log("warn", f"Target IP {target_ip} not found in campus ARP tables. Switch forwarding table lookup will not proceed.")
                not_found_res = {
                    "query": q_str,
                    "query_type": "ip",
                    "resolved_ip": target_ip,
                    "resolved_mac": None,
                    "vendor": "Unknown",
                    "best_match": None,
                    "all_matches": [],
                    "switches_queried": total,
                    "switches_responding": total,
                    "message": f"Host IP {target_ip} not found in ARP tables on campus switches.",
                }
                with _LOCATOR_LOCK:
                    if run_id in _LOCATOR_RUNS:
                        _LOCATOR_RUNS[run_id]["status"] = "complete"
                        _LOCATOR_RUNS[run_id]["is_running"] = False
                        _LOCATOR_RUNS[run_id]["percent"] = 100
                        _LOCATOR_RUNS[run_id]["done"] = total
                        _LOCATOR_RUNS[run_id]["resolved_ip"] = target_ip
                        _LOCATOR_RUNS[run_id]["resolved_mac"] = None
                        _LOCATOR_RUNS[run_id]["result"] = not_found_res

                q.put({
                    "type": "complete",
                    "run_id": run_id,
                    "result": not_found_res,
                    "timestamp": timestamp(),
                })
                q.put(None)
                return

            vendor = lookup_oui(target_mac_clean)
            emit_log("ok", f"Hardware Vendor identified: {vendor} ({format_mac(target_mac_clean)})")
            emit_log("info", f"[Step 2/2] Scanning MAC forwarding tables across {total} switches...")

            # Step 2: MAC Forwarding table search across all devices
            done_cnt = 0
            cnt_lock = threading.Lock()
            all_matches = []

            def mac_worker(did):
                nonlocal done_cnt
                with app.app_context():
                    d = db.session.get(Device, did)
                    if not d or not d.credential_id:
                        return
                    d_name = d.name
                    d_host = d.host

                try:
                    with app.app_context():
                        d = db.session.get(Device, did)
                        matches = query_mac_table(d, target_mac_clean=target_mac_clean)
                except Exception as e:
                    matches = []
                    emit_log("error", f"[{d_name}] Query error: {e}")

                with cnt_lock:
                    done_cnt += 1
                    cur_done = done_cnt

                pct = int((cur_done / total) * 100)

                if matches:
                    all_matches.extend(matches)
                    for m in matches:
                        is_up = is_uplink_port(m["interface"], device=d, device_id=did, app=app)
                        lvl = "warn" if is_up else "ok"
                        port_kind = "Uplink / Trunk" if is_up else "⭐ Access Edge Port"
                        emit_log(lvl, f"[{d_name}] Found MAC on {m['interface']} (VLAN {m['vlan']}) -> {port_kind}")

                with _LOCATOR_LOCK:
                    if run_id in _LOCATOR_RUNS:
                        _LOCATOR_RUNS[run_id]["percent"] = pct
                        _LOCATOR_RUNS[run_id]["done"] = cur_done
                        _LOCATOR_RUNS[run_id]["device"] = d_name
                        _LOCATOR_RUNS[run_id]["matches_found"] = len(all_matches)

                q.put({
                    "type": "progress",
                    "run_id": run_id,
                    "done": cur_done,
                    "total": total,
                    "percent": pct,
                    "device": d_name,
                    "matches_found": len(all_matches),
                    "resolved_ip": resolved_ip,
                    "resolved_mac": resolved_mac or (format_mac(target_mac_clean, "colon") if target_mac_clean else None),
                    "timestamp": timestamp(),
                })

            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(mac_worker, all_dev_ids))

            for m in all_matches:
                is_up = is_uplink_port(m["interface"], device_id=m.get("switch_id"), app=app)
                m["is_uplink"] = is_up
                m["link_type"] = "Uplink / Trunk" if is_up else "Edge (Access Port)"
                m["vendor"] = vendor
                m["resolved_ip"] = resolved_ip

            def match_score(m):
                score = 0
                if not m["is_uplink"]:
                    score += 100
                if m["switch_role"] == "access":
                    score += 50
                elif m["switch_role"] == "distribution":
                    score += 20
                return score

            all_matches.sort(key=match_score, reverse=True)
            best_match = all_matches[0] if all_matches else None

            if best_match and not best_match["is_uplink"]:
                emit_log("ok", f"🎯 Success! Host physical edge port pinpointed at {best_match['switch_name']} interface {best_match['interface']} (VLAN {best_match['vlan']}).")
            elif best_match:
                emit_log("warn", f"Host active on switch fabrics (seen on {len(all_matches)} ports), but only transit/uplink ports were reported.")
            else:
                emit_log("warn", f"Target MAC {format_mac(target_mac_clean)} not currently active in switch forwarding tables.")

            final_result = {
                "query": q_str,
                "query_type": "ip" if target_ip else "mac",
                "resolved_ip": resolved_ip,
                "resolved_mac": resolved_mac or (format_mac(target_mac_clean, "colon") if target_mac_clean else None),
                "vendor": vendor,
                "best_match": best_match,
                "all_matches": all_matches,
                "switches_queried": total,
                "switches_responding": total,
            }

            with _LOCATOR_LOCK:
                if run_id in _LOCATOR_RUNS:
                    _LOCATOR_RUNS[run_id]["status"] = "complete"
                    _LOCATOR_RUNS[run_id]["is_running"] = False
                    _LOCATOR_RUNS[run_id]["percent"] = 100
                    _LOCATOR_RUNS[run_id]["done"] = total
                    _LOCATOR_RUNS[run_id]["matches_found"] = len(all_matches)
                    _LOCATOR_RUNS[run_id]["resolved_ip"] = resolved_ip
                    _LOCATOR_RUNS[run_id]["resolved_mac"] = resolved_mac or (format_mac(target_mac_clean, "colon") if target_mac_clean else None)
                    _LOCATOR_RUNS[run_id]["result"] = final_result

            q.put({
                "type": "complete",
                "run_id": run_id,
                "result": final_result,
                "timestamp": timestamp(),
            })

        except Exception as e:
            emit_log("error", f"Unexpected locator failure: {e}")
            with _LOCATOR_LOCK:
                if run_id in _LOCATOR_RUNS:
                    _LOCATOR_RUNS[run_id]["status"] = "error"
                    _LOCATOR_RUNS[run_id]["is_running"] = False
                    _LOCATOR_RUNS[run_id]["error"] = str(e)
            q.put({
                "type": "error",
                "run_id": run_id,
                "timestamp": timestamp(),
                "msg": f"Unexpected locator failure: {e}",
            })
        finally:
            try:
                ctx.pop()
            except Exception:
                pass
            q.put(None)

    threading.Thread(target=run_tracer, daemon=True).start()

    while True:
        item = q.get()
        if item is None:
            break
        yield json.dumps(item) + "\n"


# --------------------------------------------------------------------------- Optical Transceiver Diagnostics (SFP DOM/DDM)

def evaluate_optics_health(rx_dbm, tx_dbm=None, alarms=None):
    """
    Evaluate optical health grade: 'ok', 'warn', 'bad'.
    Standard SFP / SFP+ optics operational thresholds.
    """
    alarms = alarms or []
    if rx_dbm is None:
        return "unknown", "No Telemetry", "Optics installed but digital diagnostic monitoring is unavailable."

    # Loss of signal / Disconnected fiber / Dark fiber
    if rx_dbm <= -28.0 or any("rx" in a.lower() and "alarm" in a.lower() for a in alarms):
        return "bad", "Critical / Loss of Light", "Signal power too low (<= -28 dBm). Fiber link dark, broken, or far-end laser off."

    # High attenuation warning
    if -28.0 < rx_dbm <= -18.0 or any("rx" in a.lower() and "warning" in a.lower() for a in alarms):
        return "warn", "Low Optical Signal Warning", "High attenuation detected (-18 to -28 dBm). Check fiber patch cables and clean connectors."

    # Overpower warning (signal too hot)
    if rx_dbm > -1.0 or any("high" in a.lower() and "alarm" in a.lower() for a in alarms):
        return "warn", "High Signal Warning", "Optical power too hot (> -1.0 dBm). Consider inline optical attenuator to prevent receiver burn-in."

    # Optimal operating range
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

        status, status_label, status_desc = evaluate_optics_health(rx_dbm, tx_dbm, alarms)

        # Percentage for gauge (0 to 100%, where -30 dBm is 0% and -3 dBm is 100%)
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

            status, status_label, status_desc = evaluate_optics_health(rx_dbm, tx_dbm, alarms)
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

            status, status_label, status_desc = evaluate_optics_health(rx_dbm, tx_dbm, [])
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


def query_device_optics(device):
    """Run optical diagnostics on a single switch over SSH. Returns list of port telemetry dicts."""
    ports = []
    conn = engine.connect(device)
    try:
        if device.is_juniper:
            out = engine.run(conn, "show interfaces diagnostics optics | no-more")
            ports = parse_junos_optics(out)
        elif device.platform == "cisco_c1300":
            try:
                out = engine.run(conn, "show optical-transceiver-diagnostics")
            except Exception:
                out = engine.run(conn, "show fiber-ports optical-transceiver")
            ports = parse_cisco_c1300_optics(out)
        else:
            try:
                out = engine.run(conn, "show interfaces transceiver detail")
            except Exception:
                out = engine.run(conn, "show interfaces transceiver")
            ports = parse_cisco_ios_optics(out)
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass

    # Attach switch metadata to each port record
    for p in ports:
        p["switch_id"] = device.id
        p["switch_name"] = device.name
        p["switch_ip"] = device.host
        p["switch_location"] = device.location or "—"
        p["switch_group"] = device.group or "—"
        p["site_code"] = device.site.code if device.site else "—"

    return ports


def scan_network_optics(devices, app=None):
    """
    Run optical diagnostics in parallel across switches in the target set.
    Thread-safe with Flask application context.
    """
    app = app or (current_app._get_current_object() if current_app else None)
    target_devices = [d for d in devices if d.enabled and d.credential_id]
    if not target_devices:
        return {
            "total_ports": 0,
            "healthy_count": 0,
            "warning_count": 0,
            "critical_count": 0,
            "ports": [],
            "device_summaries": [],
        }

    workers = min(len(target_devices), max(2, get_int("max_workers") if app else 4))
    dev_ids = [d.id for d in target_devices]

    def worker(did):
        if not app:
            return None, [], "No application context"
        with app.app_context():
            dev = db.session.get(Device, did)
            if not dev or not dev.credential_id:
                return None, [], "Device unavailable or missing credentials"
            try:
                ports = query_device_optics(dev)
                return dev, ports, None
            except Exception as e:
                return dev, [], str(e)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(worker, dev_ids))

    all_ports = []
    device_summaries = []

    healthy = 0
    warning = 0
    critical = 0

    for dev, dev_ports, err in results:
        if not dev:
            continue
        dev_healthy = sum(1 for p in dev_ports if p["status"] == "ok")
        dev_warn = sum(1 for p in dev_ports if p["status"] == "warn")
        dev_bad = sum(1 for p in dev_ports if p["status"] == "bad")

        healthy += dev_healthy
        warning += dev_warn
        critical += dev_bad
        all_ports.extend(dev_ports)

        device_summaries.append({
            "id": dev.id,
            "name": dev.name,
            "host": dev.host,
            "role": dev.role,
            "location": dev.location or "—",
            "group": dev.group or "—",
            "port_count": len(dev_ports),
            "healthy": dev_healthy,
            "warning": dev_warn,
            "critical": dev_bad,
            "error": err,
        })

    # Sort ports: critical issues first, then warnings, then healthy
    def port_sort(p):
        order = {"bad": 0, "warn": 1, "ok": 2, "unknown": 3}
        return order.get(p["status"], 9), p["switch_name"], p["interface"]

    all_ports.sort(key=port_sort)

    return {
        "total_ports": len(all_ports),
        "healthy_count": healthy,
        "warning_count": warning,
        "critical_count": critical,
        "ports": all_ports,
        "device_summaries": device_summaries,
    }


def stream_scan_optics(app, devices):
    """
    Generator yielding newline-delimited JSON events for live progress and streaming logs.
    """
    import json
    raw_ids = [d if isinstance(d, int) else getattr(d, "id", None) for d in devices]
    raw_ids = [i for i in raw_ids if i is not None]

    with app.app_context():
        valid_devs = Device.query.filter(Device.id.in_(raw_ids), Device.enabled == True, Device.credential_id.isnot(None)).all() if raw_ids else []
        dev_ids = [d.id for d in valid_devs]
        total = len(dev_ids)
        workers = min(total, max(2, get_int("max_workers"))) if total else 2

    q = queue.Queue()

    def timestamp():
        return datetime.now().strftime("%H:%M:%S")

    # Initial announcement
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
        while True:
            item = q.get()
            if item is None:
                break
            yield json.dumps(item) + "\n"
        return

    results = []
    done_count = 0
    done_lock = threading.Lock()

    def worker(dev_id):
        nonlocal done_count
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
                ports = query_device_optics(d)
        except Exception as e:
            err = str(e)

        with done_lock:
            done_count += 1
            current_done = done_count

        pct = int((current_done / total) * 100)

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

        q.put({
            "type": "progress",
            "done": current_done,
            "total": total,
            "percent": pct,
            "device": dev_name,
            "ports_count": len(ports),
            "timestamp": timestamp(),
        })

        results.append((dev_id, dev_name, dev_host, dev_role, dev_loc, dev_grp, ports, err))

    def run_all():
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(worker, dev_ids))

        # Build final summary
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

    while True:
        item = q.get()
        if item is None:
            break
        yield json.dumps(item) + "\n"
