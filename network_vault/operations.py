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

_CLI_ERROR = re.compile(
    r"^\s*(%\s*(invalid|unrecognized|incomplete|ambiguous|bad parameter|unknown)|syntax error|error:|unknown command|"
    r"invalid input|\^)", re.I | re.M)


def _cli_rejected(out):
    """The switch refused the command: netmiko returns the error text as output, it does not raise."""
    head = "\n".join((out or "").strip().splitlines()[:4])
    return bool(head) and bool(_CLI_ERROR.search(head))


def _filtered_or_full(conn, filtered, full):
    """Run the filtered command and trust its answer, even when empty (= not on this switch).
    The whole table is only fetched when the filtered command itself fails or is rejected (unsupported syntax)."""
    for cmd in list(filtered) + list(full):
        try:
            out = engine.run(conn, cmd)
        except Exception:  # noqa: BLE001 - try the next form
            continue
        if not _cli_rejected(out):
            return out
    return ""


def _with_conn(device, conn, fn):
    """Run fn(conn) on a caller-supplied SSH session, or on a new one that is closed afterwards."""
    if conn is not None:
        return fn(conn)
    c = engine.connect(device)
    try:
        return fn(c)
    finally:
        try:
            c.disconnect()
        except Exception:  # noqa: BLE001
            pass


def query_arp(device, target_ip=None, target_mac_clean=None, conn=None):
    """Query ARP table on switch. Returns list of dicts: [{ip, mac, clean_mac, interface, vlan}]."""
    results = []

    def fetch(c):
        if device.is_juniper:
            filtered = [f"show arp no-resolve | match {target_ip} | no-more"] if target_ip else (
                [f"show arp no-resolve | match {format_mac(target_mac_clean, 'colon')} | no-more"] if target_mac_clean else [])
            return _filtered_or_full(c, filtered, ["show arp no-resolve | no-more"])
        filtered = [f"show ip arp | include {target_ip}"] if target_ip else (
            [f"show ip arp | include {format_mac(target_mac_clean, 'cisco')}"] if target_mac_clean else [])
        return _filtered_or_full(c, filtered, ["show ip arp"])

    try:
        out = _with_conn(device, conn, fetch)
    except Exception:  # noqa: BLE001 - unreachable switch: no entries
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

def query_mac_table(device, target_mac_clean=None, conn=None):
    """Query MAC address forwarding table. Returns list of dicts: [{mac, clean_mac, vlan, interface, type, age}]."""
    results = []

    def fetch(c):
        if device.is_juniper:
            filtered = ([f"show ethernet-switching table | match {format_mac(target_mac_clean, 'colon')} | no-more"]
                        if target_mac_clean else [])
            return _filtered_or_full(c, filtered, ["show ethernet-switching table | no-more"])
        filtered = []
        if target_mac_clean:
            cisco_mac, colon_mac = format_mac(target_mac_clean, "cisco"), format_mac(target_mac_clean, "colon")
            filtered = [f"show mac address-table address {cisco_mac}", f"show mac-address-table address {cisco_mac}",
                        f"show mac address-table | include {cisco_mac}|{colon_mac}"]
        return _filtered_or_full(c, filtered, ["show mac address-table", "show mac-address-table"])

    try:
        out = _with_conn(device, conn, fetch)
    except Exception:  # noqa: BLE001 - unreachable switch: no entries
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
        for t in tokens[mac_idx + 1:] if mac_idx >= 0 else tokens:
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


# --------------------------------------------------------------------------- LLDP neighbours (trace)

_IPV4 = r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"


def _base_port(p):
    """ge-0/0/14.0 -> ge-0/0/14 ; Gi1/0/14 stays (lower-cased for comparison)."""
    return (p or "").strip().split(".")[0].lower()


def _parse_junos_lldp_table(out):
    """Rows of Junos 'show lldp neighbors'.

    Split on whitespace, not on the header's column positions: a long Port info (a port description such
    as UPLINK_TO_DISTRIBUTION) runs into the System Name column. The System Name is the last field
    (hostnames have no spaces); Port info is whatever sits between the Chassis Id and it."""
    lines = (out or "").splitlines()
    hdr_i = next((i for i, l in enumerate(lines) if "Local Interface" in l and "System Name" in l), None)
    if hdr_i is None:
        return []
    h = lines[hdr_i]
    need = 3 if "Parent Interface" in h else 2
    name_col = h.index("System Name")
    rows = []
    for l in lines[hdr_i + 1:]:
        t = l.split()
        if len(t) < need or l.strip().startswith("{"):
            continue
        row = {"local": t[0], "parent": t[1] if need == 3 else "", "chassis": t[need - 1]}
        rest = t[need:]
        if len(rest) >= 2:
            row["port"], row["name"] = " ".join(rest[:-1]), rest[-1]
        elif rest:  # one field: it is the System Name only if it sits in that column
            is_name = l.rstrip().rfind(rest[0]) >= name_col - 1
            row["port"], row["name"] = ("", rest[0]) if is_name else (rest[0], "")
        else:
            row["port"] = row["name"] = ""
        rows.append(row)
    return rows


def _cisco_po_member(c, port):
    """First bundled member of a Cisco port-channel ('Po1' -> 'Gi1/0/49') from 'show etherchannel summary'."""
    m = re.match(r"^po(?:rt-channel)?\s*(\d+)$", _base_port(port))
    if not m:
        return None
    for line in engine.run(c, "show etherchannel summary").splitlines():
        if re.search(rf"\bPo{m.group(1)}\(", line):
            members = re.findall(r"(\S+?)\(P\)", line) or re.findall(r"(\S+?)\(\w+\)", line)[1:]
            return members[0] if members else None
    return None


def query_lldp_neighbor(device, port, conn=None, want_ip=False, cache=None):
    """Who is on the other end of `port` (a physical port or an ae/Po bundle)?

    Returns {"local_port", "parent", "system_name", "mgmt_ip"} or None. Read-only show commands only.
    cache: optional dict shared by one search, so a Juniper LLDP table is fetched once per switch."""
    target = _base_port(port)

    def fetch(c):
        if device.is_juniper:
            key = ("junos-lldp", device.id)
            rows = cache.get(key) if cache is not None else None
            if rows is None:
                rows = _parse_junos_lldp_table(engine.run(c, "show lldp neighbors | no-more"))
                if cache is not None:
                    cache[key] = rows
            hit = (next((r for r in rows if _base_port(r.get("local")) == target), None)
                   or next((r for r in rows if _base_port(r.get("parent")) == target), None))
            if not hit:
                return None
            nb = {"local_port": hit.get("local"), "parent": None if hit.get("parent") in ("", "-") else hit.get("parent"),
                  "system_name": hit.get("name") or "", "mgmt_ip": None}
            if want_ip:
                try:
                    det = engine.run(c, f"show lldp neighbors interface {hit.get('local')} | no-more")
                    m = re.search(r"Address\s*:\s*" + _IPV4, det)
                    nb["mgmt_ip"] = m.group(1) if m else None
                except Exception:  # noqa: BLE001
                    pass
            return nb
        # Cisco IOS / C1300: LLDP detail, else CDP detail - on a member port when the MAC is on a port-channel
        phys, parent = port, None
        if target.startswith("po"):
            try:
                phys = _cisco_po_member(c, port)
            except Exception:  # noqa: BLE001
                phys = None
            if not phys:
                return None
            parent = port
        try:
            det = engine.run(c, f"show lldp neighbors {phys} detail")
            m_name = re.search(r"System Name:\s*(\S+)", det)
            if m_name:
                m_ip = re.search(r"Management Address(?:es)?:?[\s\S]{0,80}?" + _IPV4, det)
                return {"local_port": phys, "parent": parent, "system_name": m_name.group(1),
                        "mgmt_ip": m_ip.group(1) if m_ip else None}
        except Exception:  # noqa: BLE001
            pass
        try:
            det = engine.run(c, f"show cdp neighbors {phys} detail")
            m_name = re.search(r"Device ID:\s*(\S+)", det)
            if m_name:
                m_ip = re.search(r"IP(?:v4)? [Aa]ddress:\s*" + _IPV4, det)
                return {"local_port": phys, "parent": parent, "system_name": m_name.group(1),
                        "mgmt_ip": m_ip.group(1) if m_ip else None}
        except Exception:  # noqa: BLE001
            pass
        return None

    try:
        return _with_conn(device, conn, fetch)
    except Exception:  # noqa: BLE001
        return None


def match_neighbor(nb, candidates, site_id=None):
    """Map an LLDP/CDP neighbour to a Vault switch, most certain first:

    1. real hostname or Vault name;  2. management IP;
    3. an IP tail at the end of the name ('...-S1-S2-90.15', '..._90.9' -> a switch whose IP ends .90.15);
    4. a Vault name the system name starts with ('4F-RACK2-ACC-VC2' for '4F-RACK2-ACC-VC2-S1-S2-90.15').
    3 and 4 only count when they point at exactly one switch (switches in site_id preferred)."""
    if not nb:
        return None
    name = (nb.get("system_name") or "").strip().lower()
    ip = (nb.get("mgmt_ip") or "").strip()

    def only(ms):
        ms = list({d.id: d for d in ms}.values())
        if len(ms) > 1 and ip:
            ms = [d for d in ms if (d.host or "").strip() == ip] or ms
        if site_id is not None and any(d.site_id == site_id for d in ms):
            ms = [d for d in ms if d.site_id == site_id]
        return ms[0] if len(ms) == 1 else None

    def names(d):
        return [(c or "").strip().lower() for c in (d.hostname, d.name) if (c or "").strip()]

    if name:
        d = only([d for d in candidates if name in names(d)])
        if d:
            return d
        short = name.split(".")[0]
        if short and short != name and not re.match(r"^\d+\.\d+\.\d+\.\d+$", name):  # FQDN -> host part
            d = only([d for d in candidates if any(c.split(".")[0] == short for c in names(d))])
            if d:
                return d
    if ip:
        d = only([d for d in candidates if (d.host or "").strip() == ip])
        if d:
            return d

    if name:
        m = re.search(r"[-_](\d{1,3})\.(\d{1,3})$", name)
        if m:
            tail = f".{m.group(1)}.{m.group(2)}"
            d = only([d for d in candidates if (d.host or "").strip().endswith(tail)])
            if d:
                return d
        pre = []
        for d in candidates:
            c = (d.name or "").strip().lower()
            if len(c) >= 4 and len(name) > len(c) and name.startswith(c) and name[len(c)] in "-_.":
                pre.append(d)
        if pre:
            longest = max(len(d.name) for d in pre)
            d = only([d for d in pre if len(d.name) == longest])
            if d:
                return d
    return None


# --------------------------------------------------------------------------- Parallel Port Locator Engine

LOCATOR_WORKERS = 16   # parallel SSH sessions for a Port Locator scan (independent of backup max_workers)
MAX_TRACE_HOPS = 4     # LLDP hops followed from the distribution/core switch


def _is_firewall(d):
    return (d.role or "") == "firewall" or (d.platform or "").startswith("sonicwall")


def locate_host(devices, query_str, user=None, app=None, full=False, user_site_ids=None):
    """Non-streaming Port Locator: runs the same engine as stream_locate_host and returns its result."""
    import json
    app = app or current_app._get_current_object()
    result, err = None, None
    for line in stream_locate_host(app, devices, query_str, full=full, user_site_ids=user_site_ids):
        ev = json.loads(line)
        if ev.get("type") == "complete":
            result = ev["result"]
        elif ev.get("type") == "error":
            err = ev.get("msg")
    if result is None:
        raise ValueError(err or "Search failed")
    return result


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
            "step": run.get("step", ""),
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


def stream_locate_host(app, devices, query_str, run_id=None, full=False, user_site_ids=None):
    """Find which switch port a MAC/IP is on. Generator of newline-delimited JSON events.

    1. Resolve on the site's distribution/core switches (ARP for an IP search, MAC table for both).
    2. Trace: follow the port the MAC is learned on to the next switch via LLDP, until a non-uplink
       (access) port is reached - usually 2-3 switches in total.
    3. If the trace breaks, scan the switches in scope in parallel and stop at the first access-port
       hit (unless `full`). Cancel stops scheduling more switches.

    devices: switch ids (or Device objects) in scope. user_site_ids: sites the user may query (None = all).
    Read-only show commands only. Background state lives in _LOCATOR_RUNS so a trace survives page navigation.
    """
    import json
    from concurrent.futures import as_completed
    from types import SimpleNamespace

    q_str = (query_str or "").strip()
    scope_ids_in = [d if isinstance(d, int) else getattr(d, "id", None) for d in devices]
    scope_ids_in = [i for i in scope_ids_in if i is not None]
    allowed_sites = set(user_site_ids) if user_site_ids is not None else None

    def _ok_site(site_id):
        return allowed_sites is None or site_id in allowed_sites

    def _snap(d):
        return SimpleNamespace(id=d.id, name=d.name, hostname=d.hostname, host=d.host, site_id=d.site_id,
                               role=d.role, group=d.group, location=d.location)

    with app.app_context():
        usable = (Device.query.filter(Device.enabled.is_(True), Device.credential_id.isnot(None)).all())
        usable = [d for d in usable if not _is_firewall(d) and _ok_site(d.site_id)]
        by_id = {d.id: _snap(d) for d in usable}
        scope = [by_id[i] for i in scope_ids_in if i in by_id]
        scope_sites = {d.site_id for d in scope}
        # Distribution/core of the sites in scope - used for ARP and as the trace start, whatever the filters
        cores = [s for s in by_id.values() if s.role in ("core", "distribution") and s.site_id in scope_sites]
        cores.sort(key=lambda s: 0 if s.role == "distribution" else 1)
        candidates = list(by_id.values())
        firewall_skipped = len([i for i in scope_ids_in if i not in by_id])

    total = len(scope)
    run_id = run_id or f"trace_{int(time.time() * 1000)}"
    started = time.time()
    with _LOCATOR_LOCK:
        for k in list(_LOCATOR_RUNS.keys()):
            if started - _LOCATOR_RUNS[k].get("started_at", started) > 7200:
                _LOCATOR_RUNS.pop(k, None)
        _LOCATOR_RUNS[run_id] = {
            "id": run_id, "query": q_str, "status": "running", "is_running": True, "percent": 0, "done": 0,
            "total": total, "device": "", "matches_found": 0, "resolved_ip": None, "resolved_mac": None,
            "logs": [], "result": None, "error": None, "started_at": started,
        }

    q = queue.Queue()

    def timestamp():
        return datetime.now().strftime("%H:%M:%S")

    def emit_log(level, msg):
        t_str = timestamp()
        with _LOCATOR_LOCK:
            if run_id in _LOCATOR_RUNS:
                _LOCATOR_RUNS[run_id]["logs"].append({"timestamp": t_str, "level": level, "msg": msg})
        q.put({"type": "log", "level": level, "timestamp": t_str, "msg": msg})

    def update(**kw):
        with _LOCATOR_LOCK:
            if run_id in _LOCATOR_RUNS:
                _LOCATOR_RUNS[run_id].update(kw)

    def cancelled():
        with _LOCATOR_LOCK:
            return _LOCATOR_RUNS.get(run_id, {}).get("status") == "cancelled"

    def finish(result):
        if cancelled():
            result["cancelled"] = True
        update(status="cancelled" if result.get("cancelled") else "complete", is_running=False, percent=100, result=result,
               matches_found=len(result.get("all_matches") or []),
               resolved_ip=result.get("resolved_ip"), resolved_mac=result.get("resolved_mac"))
        q.put({"type": "complete", "run_id": run_id, "result": result, "timestamp": timestamp()})

    def fail(msg):
        emit_log("error", msg)
        update(status="error", is_running=False, error=msg)
        q.put({"type": "error", "run_id": run_id, "timestamp": timestamp(), "msg": msg})

    global _APP_REF
    _APP_REF = app

    def device(did):
        return db.session.get(Device, did)

    def run_tracer():
        ctx = app.app_context()
        ctx.push()
        queried = set()
        all_matches = []
        path = []
        method = "trace"
        stopped_early = False
        resolved_ip = resolved_mac = None
        bar = [0]

        def step(text, pct, done=None, device=""):
            """Progress for the page: what the locator is doing now. A trace has no fixed number of switches,
            so the bar follows the steps (resolve, distribution/core, each LLDP hop) and then the scan."""
            bar[0] = max(bar[0], min(99, int(pct)))
            n = len(queried) if done is None else done
            update(percent=bar[0], done=n, device=device, step=text)
            q.put({"type": "progress", "run_id": run_id, "step": text, "percent": bar[0], "done": n, "total": total,
                   "device": device, "matches_found": len(all_matches), "resolved_ip": resolved_ip,
                   "resolved_mac": resolved_mac, "timestamp": timestamp()})

        try:
            q.put({"type": "init", "run_id": run_id, "total": total, "timestamp": timestamp(),
                   "msg": f"Starting trace for '{q_str}' ({total} switches in scope)..."})
            emit_log("info", f"Starting trace for '{q_str}' ({total} switches in scope)...")
            if firewall_skipped:
                emit_log("info", f"Skipping {firewall_skipped} firewall / non-switch device(s).")
            if not q_str:
                return fail("Search target is empty.")
            if not scope:
                return fail("No reachable switches available with credentials in this scope.")

            target_ip = q_str if is_ip(q_str) else None
            mac_clean = None if target_ip else clean_mac(q_str)
            if not target_ip and not mac_clean:
                return fail(f"'{q_str}' is neither a valid IPv4 address nor a recognized MAC address.")
            if mac_clean:
                resolved_mac = format_mac(mac_clean, "colon")
            resolved_ip = target_ip
            update(resolved_ip=resolved_ip, resolved_mac=resolved_mac)
            core_pool = cores or scope  # no distribution/core in these sites: use the scope itself
            workers = max(1, min(LOCATOR_WORKERS, len(core_pool)))

            # Step 1a: an IP needs its MAC first - ask distribution/core ARP, take the first answer
            if target_ip:
                emit_log("info", f"[Step 1] Resolving {target_ip} via ARP on {len(core_pool)} distribution/core switch(es)...")
                step(f"Resolving {target_ip} to a MAC on the distribution/core switches…", 5)

                def arp_one(s):
                    if cancelled():
                        return s, []
                    with app.app_context():
                        d = device(s.id)
                        return s, (query_arp(d, target_ip=target_ip) if d else [])

                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futs = [pool.submit(arp_one, s) for s in core_pool]
                    for f in as_completed(futs):
                        s, entries = f.result()
                        if entries and not mac_clean:
                            mac_clean = entries[0]["clean_mac"]
                            resolved_mac = entries[0]["mac"]
                            emit_log("ok", f"[{s.name}] ARP: {target_ip} -> {resolved_mac} ({entries[0]['interface']})")
                            for other in futs:
                                other.cancel()
                update(resolved_mac=resolved_mac)
                if not mac_clean:
                    emit_log("warn", f"{target_ip} is not in the ARP table of the distribution/core switches.")
                    return finish({
                        "query": q_str, "query_type": "ip", "resolved_ip": target_ip, "resolved_mac": None,
                        "vendor": "Unknown", "best_match": None, "all_matches": [], "path": [], "method": "trace",
                        "stopped_early": False, "scanned": 0, "in_scope": total, "switches_queried": 0,
                        "switches_responding": 0,
                        "message": f"Host IP {target_ip} not found in the ARP tables of the distribution/core switches.",
                    })

            vendor = lookup_oui(mac_clean)
            emit_log("ok", f"Hardware vendor: {vendor} ({format_mac(mac_clean)})")

            # The IP behind a MAC is for display only: look it up in the background (core first), never wait on it
            arp_box = {}

            def arp_bg():
                for s in sorted(cores, key=lambda s: 0 if s.role == "core" else 1):
                    if cancelled() or arp_box:
                        return
                    try:
                        with app.app_context():
                            d = device(s.id)
                            entries = query_arp(d, target_mac_clean=mac_clean) if d else []
                    except Exception:  # noqa: BLE001
                        entries = []
                    if entries:
                        arp_box.update(ip=entries[0]["ip"], switch=s.name)
                        return

            arp_thread = None
            if not target_ip:
                arp_thread = threading.Thread(target=arp_bg, daemon=True, name=f"locator-arp-{run_id}")
                arp_thread.start()

            lldp_cache = {}

            def probe(s, lldp="always"):
                """One SSH session on switch s: where the MAC is learned and the LLDP neighbour on that port.

                Returns (hits, hit, nb, nxt). nxt is the Vault switch behind the port; when there is one the
                port is an uplink, whatever its name or config says. lldp='edge' (scan) asks LLDP only when
                the port looks like an access port."""
                with app.app_context():
                    d = device(s.id)
                    if not d:
                        return [], None, None, None
                    conn = engine.connect(d)
                    try:
                        hits = query_mac_table(d, target_mac_clean=mac_clean, conn=conn)
                        for h in hits:
                            h["is_uplink"] = is_uplink_port(h["interface"], device=d, device_id=s.id, app=app)
                        if not hits:
                            return hits, None, None, None
                        hit = next((h for h in hits if not h["is_uplink"]), hits[0])
                        if lldp == "edge" and hit["is_uplink"]:
                            return hits, hit, None, None
                        nb = query_lldp_neighbor(d, hit["interface"], conn=conn, cache=lldp_cache)
                        nxt = match_neighbor(nb, candidates, site_id=s.site_id)
                        if nb and not nxt and d.is_juniper:
                            nb = query_lldp_neighbor(d, hit["interface"], conn=conn, want_ip=True, cache=lldp_cache) or nb
                            nxt = match_neighbor(nb, candidates, site_id=s.site_id)
                        if nxt:
                            hit["is_uplink"] = True
                            hit["lldp_neighbor"] = nxt.name
                        return hits, hit, nb, nxt
                    finally:
                        try:
                            conn.disconnect()
                        except Exception:  # noqa: BLE001
                            pass

            def port_note(h):
                if h.get("lldp_neighbor"):
                    return f"link to {h['lldp_neighbor']} (LLDP)"
                return "uplink / trunk" if h["is_uplink"] else "access port"

            # Step 2: where do the distribution/core switches learn the MAC?
            if cores:
                emit_log("info", f"[Step 2] Looking up {format_mac(mac_clean)} on {len(cores)} distribution/core switch(es)...")
                step(f"Looking up the MAC on {len(cores)} distribution/core switch{'' if len(cores) == 1 else 'es'}…", 10)

            def core_one(s):
                if cancelled():
                    return s, None
                try:
                    return s, probe(s)
                except Exception as e:  # noqa: BLE001
                    emit_log("error", f"[{s.name}] Unreachable: {e}")
                    return s, None

            core_res = []
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for s, r in pool.map(core_one, cores):
                    queried.add(s.id)
                    if not r or not r[0]:
                        continue
                    hits, hit, nb, nxt = r
                    for h in hits:
                        emit_log("ok", f"[{s.name}] MAC learned on {h['interface']} (VLAN {h['vlan']}) - {port_note(h)}")
                    all_matches += hits
                    core_res.append((s, hit, nb, nxt))
            if cores:
                step("Distribution/core switches checked", 35)

            # Start from the switch whose port leads down to an access switch (distribution before core)
            core_ids = {s.id for s in cores}

            def start_rank(e):
                _s, hit, _nb, nxt = e
                if nxt and nxt.id not in core_ids:
                    return 0
                if not hit["is_uplink"]:
                    return 1
                return 3 if nxt else 2

            core_res.sort(key=start_rank)

            # Step 3: trace hop by hop over LLDP until a port with no switch behind it
            found = None
            if core_res:
                cur, hit, nb, nxt = core_res[0]
                path.append({"switch_id": cur.id, "switch_name": cur.name, "port": hit["interface"]})
                visited = {cur.id}
                reason = None
                for hop in range(MAX_TRACE_HOPS + 1):
                    if not nxt:
                        if not hit["is_uplink"]:
                            found = hit
                            seen = f" (LLDP neighbour: {nb.get('system_name')})" if nb and nb.get("system_name") else ""
                            emit_log("ok", f"[{cur.name}] {hit['interface']} is an access port{seen}.")
                        elif not nb:
                            reason = f"no LLDP neighbour on {cur.name} {hit['interface']}"
                        else:
                            reason = (f"LLDP neighbour '{nb.get('system_name')}' on {cur.name} {hit['interface']} "
                                      f"is not a switch in the Vault you can access")
                        break
                    if hop == MAX_TRACE_HOPS:
                        reason = f"more than {MAX_TRACE_HOPS} hops"
                        break
                    if cancelled():
                        reason = "cancelled"
                        break
                    if nxt.id in visited:
                        reason = f"loop back to {nxt.name}"
                        break
                    emit_log("info", f"[{cur.name}] {hit['interface']} -> LLDP neighbour {nxt.name}")
                    step(f"Following LLDP, hop {hop + 1}: {cur.name} {hit['interface']} → {nxt.name}…",
                         40 + 20 * hop, done=len(queried) + 1, device=nxt.name)
                    visited.add(nxt.id)
                    queried.add(nxt.id)
                    try:
                        hits, hit2, nb, nxt2 = probe(nxt)
                    except Exception as e:  # noqa: BLE001
                        reason = f"{nxt.name} is unreachable ({e})"
                        break
                    all_matches += hits
                    if not hits:
                        reason = f"{nxt.name} has not learned the MAC"
                        break
                    cur, hit, nxt = nxt, hit2, nxt2
                    path.append({"switch_id": cur.id, "switch_name": cur.name, "port": hit["interface"]})
                    emit_log("ok", f"[{cur.name}] MAC learned on {hit['interface']} (VLAN {hit['vlan']}) - {port_note(hit)}")
                if not found:
                    emit_log("warn", f"Trace stopped: {reason}. Falling back to scanning the switches in scope.")
            elif cores:
                emit_log("warn", "No distribution/core switch has learned this MAC. Scanning the switches in scope.")
            else:
                emit_log("info", "No distribution/core switch in these sites to trace from. Scanning the switches in scope.")

            # Step 4: fallback scan with early stop
            scanned = len(queried)
            if (not found or full) and not cancelled():
                method = "scan" if not found else method
                todo = [s for s in scope if s.id not in queried]
                stop = threading.Event()
                done = [0]
                lock = threading.Lock()
                emit_log("info", f"[Step 4] Scanning {len(todo)} switch(es), {min(LOCATOR_WORKERS, max(1, len(todo)))} at a time"
                         + ("" if full else " - stopping at the first access-port hit") + "...")
                base_pct, base_done = bar[0], len(queried)
                step(f"Scanning {len(todo)} switch{'' if len(todo) == 1 else 'es'}…", base_pct)

                def scan_one(s):
                    if stop.is_set() or cancelled():
                        return s, None
                    try:
                        hits = probe(s, lldp="edge")[0]
                    except Exception as e:  # noqa: BLE001
                        emit_log("error", f"[{s.name}] Query error: {e}")
                        hits = []
                    with lock:
                        done[0] += 1
                        n = done[0]
                    for h in hits:
                        emit_log("ok" if not h["is_uplink"] else "warn",
                                 f"[{s.name}] Found on {h['interface']} (VLAN {h['vlan']}) - {port_note(h)}")
                    if not full and any(not h["is_uplink"] and s.role == "access" for h in hits):
                        stop.set()
                    with lock:
                        step(f"Scanning switch {n} of {len(todo)}: {s.name}…",
                             base_pct + (99 - base_pct) * n / max(1, len(todo)), done=base_done + n, device=s.name)
                    return s, hits

                with ThreadPoolExecutor(max_workers=max(1, min(LOCATOR_WORKERS, len(todo) or 1))) as pool:
                    for s, hits in pool.map(scan_one, todo):
                        if hits is None:
                            continue
                        queried.add(s.id)
                        all_matches += hits
                scanned = len(queried)
                stopped_early = stop.is_set() and any(s.id not in queried for s in scope)
                if cancelled():
                    emit_log("warn", "Search cancelled.")

            if arp_thread:
                arp_thread.join(timeout=3)
                if arp_box.get("ip"):
                    resolved_ip = arp_box["ip"]
                    emit_log("ok", f"[{arp_box['switch']}] ARP: {resolved_mac} -> {resolved_ip}")
                    update(resolved_ip=resolved_ip)

            for m in all_matches:
                m["link_type"] = "Uplink / Trunk" if m.get("is_uplink") else "Edge (Access Port)"
                m["vendor"] = vendor
                m["resolved_ip"] = resolved_ip

            def score(m):
                return (0 if m.get("is_uplink") else 100) + {"access": 50, "distribution": 20}.get(m.get("switch_role"), 0)

            all_matches.sort(key=score, reverse=True)
            best = found or (all_matches[0] if all_matches else None)
            if best and not best.get("is_uplink"):
                how = "traced via LLDP" if method == "trace" else "found by scanning"
                emit_log("ok", f"Found: {best['switch_name']} {best['interface']} (VLAN {best['vlan']}), {how}"
                         f" in {time.time() - started:.1f}s.")
            elif best:
                emit_log("warn", f"Seen on {len(all_matches)} port(s), but only on uplinks/trunks.")
            else:
                emit_log("warn", f"{format_mac(mac_clean)} is not in the forwarding tables of the switches queried.")
            if stopped_early:
                emit_log("info", f"Stopped early after {len([s for s in scope if s.id in queried])} of {total} switches. Use 'Show full path' to scan all.")

            finish({
                "query": q_str, "query_type": "ip" if target_ip else "mac", "resolved_ip": resolved_ip,
                "resolved_mac": resolved_mac or format_mac(mac_clean, "colon"), "vendor": vendor,
                "best_match": best, "all_matches": all_matches, "path": path,
                "method": method, "stopped_early": stopped_early,
                "scanned": len([s for s in scope if s.id in queried]), "in_scope": total,
                "switches_queried": len(queried), "switches_responding": len(queried),
                "elapsed_sec": round(time.time() - started, 1),
            })
        except Exception as e:  # noqa: BLE001
            fail(f"Unexpected locator failure: {e}")
        finally:
            try:
                ctx.pop()
            except Exception:  # noqa: BLE001
                pass
            q.put(None)

    threading.Thread(target=run_tracer, daemon=True, name=f"locator-{run_id}").start()

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


# =========================================================================== VLAN Matrix & Dormant / Unused Port Auditing

def _norm_port(s):
    """Shorten verbose or lowercase interface names to standard compact form, e.g. GigabitEthernet1 or gi1 -> Gi1."""
    s = (s or "").strip()
    abbr = [
        (r'^(?:GigabitEthernet|gi)\s*', 'Gi'),
        (r'^(?:FastEthernet|fa)\s*', 'Fa'),
        (r'^(?:TenGigabitEthernet|tengige|te)\s*', 'Te'),
        (r'^(?:TwentyFiveGigE|twe)\s*', 'Twe'),
        (r'^(?:HundredGigE|hu)\s*', 'Hu'),
        (r'^(?:FortyGigabitEthernet|fo)\s*', 'Fo'),
        (r'^(?:Ethernet|eth)\s*', 'Eth'),
        (r'^(?:Port-channel|po)\s*', 'Po'),
    ]
    for pattern, short in abbr:
        s = re.sub(pattern, short, s, flags=re.IGNORECASE)
    return s


def expand_junos_range(start, end):
    """Expand member-range ge-0/0/0 to ge-0/0/45 into list of individual port names."""
    m1 = re.match(r'([a-z]+-\d+/\d+/?)(.+)', start)
    m2 = re.match(r'([a-z]+-\d+/\d+/?)(.+)', end)
    if not m1 or not m2 or m1.group(1) != m2.group(1):
        return [start, end]
    try:
        prefix = m1.group(1)
        s = int(m1.group(2))
        e = int(m2.group(2))
        return [f"{prefix}{i}" for i in range(min(s, e), max(s, e) + 1)]
    except ValueError:
        return [start, end]


def parse_junos_vlans_and_ports(config_text):
    """Parse JunOS 'set' configuration to extract defined VLANs, interface allocations, trunks and LAGs."""
    vlans_by_name = {}  # name -> {id, name, desc}
    vlans_by_id = {}    # id -> name

    # 1. Parse defined VLANs: set vlans <name> vlan-id <id>
    for m in re.finditer(r'set vlans (\S+) vlan-id (\d+)', config_text):
        vname, vid = m.group(1), int(m.group(2))
        vlans_by_name[vname] = {"id": vid, "name": vname, "desc": ""}
        vlans_by_id[vid] = vname

    for m in re.finditer(r'set vlans (\S+) description (["\']?)(.*?)\2(?:\n|$)', config_text):
        vname = m.group(1)
        if vname in vlans_by_name:
            vlans_by_name[vname]["desc"] = m.group(3).strip()

    # 2. Parse LAG bundles (802.3ad) and AE interfaces
    lag_parent = dict(re.findall(r'set interfaces (\S+) ether-options 802.3ad (\S+)', config_text))
    ae_modes = dict(re.findall(r'set interfaces (ae\d+) unit 0 family ethernet-switching (?:port-mode|interface-mode) (trunk|access)', config_text))
    ae_vlans = dict(re.findall(r'set interfaces (ae\d+) unit 0 family ethernet-switching vlan members (\S+)', config_text))

    # 3. Parse interface-ranges (e.g. member-range ge-0/0/0 to ge-0/0/45)
    ranges = {}
    for m in re.finditer(r'set interfaces interface-range (\S+) member-range (\S+) to (\S+)', config_text):
        rname, start, end = m.group(1), m.group(2), m.group(3)
        ranges.setdefault(rname, {"members": [], "vlan": None, "mode": "access", "disabled": False})
        ranges[rname]["members"].extend(expand_junos_range(start, end))

    for m in re.finditer(r'set interfaces interface-range (\S+) member (\S+)', config_text):
        rname, member = m.group(1), m.group(2)
        ranges.setdefault(rname, {"members": [], "vlan": None, "mode": "access", "disabled": False})
        ranges[rname]["members"].append(member)

    for m in re.finditer(r'set interfaces interface-range (\S+) unit 0 family ethernet-switching vlan members (\S+)', config_text):
        rname, vmember = m.group(1), m.group(2)
        if rname in ranges:
            ranges[rname]["vlan"] = vmember

    for m in re.finditer(r'set interfaces interface-range (\S+) unit 0 family ethernet-switching (?:port-mode|interface-mode) (trunk|access)', config_text):
        rname, mode = m.group(1), m.group(2)
        if rname in ranges:
            ranges[rname]["mode"] = mode

    for m in re.finditer(r'set interfaces interface-range (\S+) disable', config_text):
        rname = m.group(1)
        if rname in ranges:
            ranges[rname]["disabled"] = True

    # 4. Parse individual physical interfaces: ge-*, xe-*, et-*
    ports_map = {}

    # Pre-populate from ranges
    for rname, rdata in ranges.items():
        vname = rdata["vlan"]
        vid = vlans_by_name[vname]["id"] if vname in vlans_by_name else None
        for m in rdata["members"]:
            ports_map[m] = {
                "interface": m,
                "description": "",
                "mode": rdata["mode"],
                "vlan_id": vid,
                "vlan_name": vname or "",
                "admin_status": "disabled" if rdata["disabled"] else "up",
                "oper_status": "disabled" if rdata["disabled"] else "connected",
                "is_uplink": rdata["mode"] == "trunk",
            }

    # Match interface descriptions: set interfaces <iface> description "..."
    for m in re.finditer(r'set interfaces ([a-z]+-\d+/\d+/\d+|ae\d+) description (["\']?)(.*?)\2(?:\n|$)', config_text):
        pname, desc = m.group(1), m.group(3).strip()
        ports_map.setdefault(pname, {
            "interface": pname, "description": "", "mode": "access", "vlan_id": None,
            "vlan_name": "", "admin_status": "up", "oper_status": "dormant", "is_uplink": False
        })
        ports_map[pname]["description"] = desc

    # Match interface disable: set interfaces <iface> disable
    for m in re.finditer(r'set interfaces ([a-z]+-\d+/\d+/\d+|ae\d+) disable', config_text):
        pname = m.group(1)
        ports_map.setdefault(pname, {
            "interface": pname, "description": "", "mode": "access", "vlan_id": None,
            "vlan_name": "", "admin_status": "up", "oper_status": "dormant", "is_uplink": False
        })
        ports_map[pname]["admin_status"] = "disabled"
        ports_map[pname]["oper_status"] = "disabled"

    # Match port-mode or interface-mode trunk/access
    for m in re.finditer(r'set interfaces ([a-z]+-\d+/\d+/\d+|ae\d+) unit 0 family ethernet-switching (?:port-mode|interface-mode) (trunk|access)', config_text):
        pname, mode = m.group(1), m.group(2)
        ports_map.setdefault(pname, {
            "interface": pname, "description": "", "mode": "access", "vlan_id": None,
            "vlan_name": "", "admin_status": "up", "oper_status": "dormant", "is_uplink": False
        })
        ports_map[pname]["mode"] = mode
        if mode == "trunk":
            ports_map[pname]["is_uplink"] = True

    # Match vlan members
    for m in re.finditer(r'set interfaces ([a-z]+-\d+/\d+/\d+|ae\d+) unit 0 family ethernet-switching vlan members (\S+)', config_text):
        pname, vname = m.group(1), m.group(2)
        ports_map.setdefault(pname, {
            "interface": pname, "description": "", "mode": "access", "vlan_id": None,
            "vlan_name": "", "admin_status": "up", "oper_status": "dormant", "is_uplink": False
        })
        if ports_map[pname]["mode"] == "access":
            vid = vlans_by_name[vname]["id"] if vname in vlans_by_name else (int(vname) if vname.isdigit() else None)
            ports_map[pname]["vlan_id"] = vid
            ports_map[pname]["vlan_name"] = vname
        else:
            ports_map[pname]["vlan_name"] = "Trunk (" + vname.replace("[", "").replace("]", "").strip() + ")"

    # 5. Resolve LAG member interfaces (802.3ad) and uplink keywords
    for pname, pdata in ports_map.items():
        desc = pdata.get("description", "")
        if pname in lag_parent:
            ae = lag_parent[pname]
            ae_m = ae_modes.get(ae, "trunk")
            pdata["mode"] = ae_m
            pdata["is_uplink"] = True
            v_spec = ae_vlans.get(ae, "all")
            pdata["vlan_name"] = f"LAG {ae} ({v_spec})"
            pdata["vlan_id"] = None
        if re.search(r'(?i)uplink|trunk|core|dist|sw-to-sw|server-sw|aggregation', desc):
            pdata["is_uplink"] = True
            pdata["mode"] = "trunk"
            if not pdata.get("vlan_name"):
                pdata["vlan_name"] = "Trunk (Uplink)"

        # Set default provisional oper_status from backup configuration
        if pdata["admin_status"] != "disabled":
            if pdata["is_uplink"] or pdata["mode"] == "trunk" or desc.strip():
                pdata["oper_status"] = "connected"
            else:
                pdata["oper_status"] = "dormant"

    valid_ports = [p for p in ports_map.values() if re.match(r'^(ge|xe|et)-?\d+', p["interface"])]
    valid_ports.sort(key=lambda x: [int(c) if c.isdigit() else c for c in re.split(r'(\d+)', x["interface"])])

    return {"vlans": list(vlans_by_name.values()), "ports": valid_ports}


def parse_cisco_vlans_and_ports(config_text):
    """Parse Cisco IOS / C1300 configuration to extract defined VLANs and interface port allocations."""
    vlans_by_id = {}

    # 1. Parse defined VLANs
    vlan_blocks = re.findall(r'vlan\s+(\d+)\s*\n((?:[ \t]+.*\n)+)', config_text)
    for vid_str, block in vlan_blocks:
        vid = int(vid_str)
        name_m = re.search(r'name\s+(\S+)', block)
        vname = name_m.group(1) if name_m else f"VLAN{vid}"
        vlans_by_id[vid] = {"id": vid, "name": vname, "desc": ""}

    for m in re.finditer(r'vlan\s+(\d+)\s*$', config_text, re.MULTILINE):
        vid = int(m.group(1))
        if vid not in vlans_by_id:
            vlans_by_id[vid] = {"id": vid, "name": f"VLAN{vid}", "desc": ""}

    # 2. Check Port-channel trunk definitions
    po_modes = {}
    po_vlans = {}
    for po_m in re.finditer(r'interface\s+(Port-channel\d+)\s*\n((?:[ \t]+.*\n)+)', config_text):
        po_name = po_m.group(1)
        po_block = po_m.group(2)
        if re.search(r'switchport mode trunk', po_block):
            po_modes[po_name] = "trunk"
            tv_m = re.search(r'switchport trunk allowed vlan\s+(.*)', po_block)
            po_vlans[po_name] = tv_m.group(1).strip() if tv_m else "all"

    # 3. Parse interface blocks
    ports = []
    iface_blocks = re.findall(r'interface\s+([A-Za-z0-9/]+)\s*\n((?:[ \t]+.*\n)+)', config_text)
    for iname, block in iface_blocks:
        if not re.match(r'(?i)^(GigabitEthernet|FastEthernet|TenGigabitEthernet|Ethernet|Te|Gi|Fa)\d', iname):
            continue

        desc_m = re.search(r'description\s+(.*)', block)
        desc = desc_m.group(1).strip() if desc_m else ""

        is_shutdown = bool(re.search(r'^[ \t]*shutdown', block, re.MULTILINE))

        mode = "access"
        if re.search(r'switchport mode trunk', block):
            mode = "trunk"
        elif re.search(r'switchport mode access', block):
            mode = "access"

        cg_m = re.search(r'channel-group\s+(\d+)', block)
        if cg_m:
            po = f"Port-channel{cg_m.group(1)}"
            if po in po_modes:
                mode = po_modes[po]

        is_uplink = mode == "trunk" or bool(re.search(r'(?i)uplink|core|dist|trunk|sw-to-sw|aggregation', desc))
        if is_uplink:
            mode = "trunk"

        vlan_id = None
        vlan_name = ""
        if mode == "access":
            vlan_m = re.search(r'switchport access vlan\s+(\d+)', block)
            if vlan_m:
                vlan_id = int(vlan_m.group(1))
                vlan_name = vlans_by_id.get(vlan_id, {}).get("name", f"VLAN{vlan_id}")
            else:
                vlan_id = 1
                vlan_name = vlans_by_id.get(1, {}).get("name", "default")
        else:
            trunk_vlans_m = re.search(r'switchport trunk allowed vlan\s+(.*)', block)
            trunk_str = trunk_vlans_m.group(1).strip() if trunk_vlans_m else "all"
            vlan_name = f"Trunk ({trunk_str})"

        admin_status = "disabled" if is_shutdown else "up"
        if admin_status == "disabled":
            oper_status = "disabled"
        elif is_uplink or mode == "trunk" or desc.strip():
            oper_status = "connected"
        else:
            oper_status = "dormant"

        ports.append({
            "interface": _norm_port(iname),
            "description": desc,
            "mode": mode,
            "vlan_id": vlan_id,
            "vlan_name": vlan_name,
            "admin_status": admin_status,
            "oper_status": oper_status,
            "is_uplink": is_uplink,
        })

    ports.sort(key=lambda x: [int(c) if c.isdigit() else c for c in re.split(r'(\d+)', x["interface"])])
    return {"vlans": list(vlans_by_id.values()), "ports": ports}


def query_device_live_ports(device):
    """Query real-time interface status (connected, notconnect/dormant, disabled) via SSH."""
    live_map = {}
    conn = engine.connect(device)
    try:
        if device.platform.startswith("cisco"):
            out = engine.run(conn, "show interfaces status")
            for line in out.splitlines():
                line = line.strip()
                if not line or line.startswith("Port") or line.startswith("-") or line.startswith("Ch "):
                    continue
                parts = line.split()
                if not parts:
                    continue
                pname = _norm_port(parts[0])
                status = None
                for tok in parts[1:]:
                    t = tok.lower()
                    if t in ("connected", "up"):
                        status = "connected"
                        break
                    elif t in ("notconnect", "down"):
                        status = "dormant"
                        break
                    elif t in ("disabled", "err-disabled"):
                        status = "disabled"
                        break
                if status:
                    live_map[pname] = {"status": status}
                    live_map[pname.lower()] = {"status": status}

        elif device.is_juniper:
            out_terse = engine.run(conn, "show interfaces terse")
            for line in out_terse.splitlines():
                parts = line.split()
                if len(parts) >= 3 and re.match(r'^[a-z]+-\d+/\d+/\d+$', parts[0]):
                    pname = parts[0]
                    admin_up = parts[1].lower() == "up"
                    link_up = parts[2].lower() == "up"
                    if not admin_up:
                        oper = "disabled"
                    elif link_up:
                        oper = "connected"
                    else:
                        oper = "dormant"
                    live_map[pname] = {"status": oper, "admin": parts[1], "link": parts[2]}
                    live_map[pname.lower()] = {"status": oper}
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass

    return live_map


def get_vlan_port_audit(app, site_id=None):
    """Aggregate complete VLAN Matrix & Port Audit across campus switches from Vault backups and live cache."""
    with app.app_context():
        # Load live cache if available
        live_cache = {}
        try:
            from .util import BASE_DIR
            cache_file = BASE_DIR / "live_ports_cache.json"
            if cache_file.exists():
                live_cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Could not read live_ports_cache.json: %s", e)

        q = Device.query.filter_by(enabled=True)
        if site_id and site_id != "all":
            try:
                q = q.filter_by(site_id=int(site_id))
            except (ValueError, TypeError):
                pass
        devices = q.order_by(Device.name).all()

        total_switches = 0
        total_ports = 0
        active_ports = 0
        dormant_ports = 0
        disabled_ports = 0

        vlan_global = {}
        all_ports = []
        switch_summaries = []

        for dev in devices:
            b = Backup.query.filter_by(device_id=dev.id).order_by(Backup.created_at.desc()).first()
            if not b:
                continue

            config_text = read_backup_file(b.set_path or b.path)
            if not config_text:
                continue

            total_switches += 1
            if dev.is_juniper:
                parsed = parse_junos_vlans_and_ports(config_text)
            elif dev.platform.startswith("cisco"):
                parsed = parse_cisco_vlans_and_ports(config_text)
            else:
                parsed = {"vlans": [], "ports": []}

            sw_total = len(parsed["ports"])
            sw_active = 0
            sw_dormant = 0
            sw_disabled = 0

            sw_vlans = set()
            for v in parsed["vlans"]:
                vid = v["id"]
                sw_vlans.add(vid)
                if vid not in vlan_global:
                    vlan_global[vid] = {
                        "id": vid,
                        "primary_name": v["name"],
                        "all_names": set(),
                        "access_ports": 0,
                        "trunk_switches": set(),
                        "switches": {},
                    }
                vlan_global[vid]["all_names"].add(v["name"])
                vlan_global[vid]["switches"][dev.id] = {
                    "switch_id": dev.id,
                    "switch_name": dev.name,
                    "role": dev.role or "access",
                    "site_code": dev.site.code if dev.site else "",
                    "access_count": 0,
                    "is_trunk": False,
                }

            for p in parsed["ports"]:
                total_ports += 1

                # Apply live cache status if present
                live_key = f"{dev.id}:{p['interface']}"
                live_key_lower = live_key.lower()
                live_key_norm = f"{dev.id}:{_norm_port(p['interface'])}".lower()
                if live_key in live_cache:
                    p["oper_status"] = live_cache[live_key]
                elif live_key_lower in live_cache:
                    p["oper_status"] = live_cache[live_key_lower]
                elif live_key_norm in live_cache:
                    p["oper_status"] = live_cache[live_key_norm]

                stat = p["oper_status"]
                if stat == "connected":
                    active_ports += 1
                    sw_active += 1
                elif stat == "disabled":
                    disabled_ports += 1
                    sw_disabled += 1
                else:
                    dormant_ports += 1
                    sw_dormant += 1

                vid = p.get("vlan_id")
                if vid and p["mode"] == "access":
                    if vid not in vlan_global:
                        vlan_global[vid] = {
                            "id": vid,
                            "primary_name": p.get("vlan_name") or f"VLAN{vid}",
                            "all_names": set([p.get("vlan_name") or f"VLAN{vid}"]),
                            "access_ports": 0,
                            "trunk_switches": set(),
                            "switches": {},
                        }
                    vlan_global[vid]["access_ports"] += 1
                    if dev.id not in vlan_global[vid]["switches"]:
                        vlan_global[vid]["switches"][dev.id] = {
                            "switch_id": dev.id,
                            "switch_name": dev.name,
                            "role": dev.role or "access",
                            "site_code": dev.site.code if dev.site else "",
                            "access_count": 0,
                            "is_trunk": False,
                        }
                    vlan_global[vid]["switches"][dev.id]["access_count"] += 1

                elif p["mode"] == "trunk":
                    for vid_sw in sw_vlans:
                        if vid_sw in vlan_global and dev.id in vlan_global[vid_sw]["switches"]:
                            vlan_global[vid_sw]["switches"][dev.id]["is_trunk"] = True
                            vlan_global[vid_sw]["trunk_switches"].add(dev.id)

                all_ports.append({
                    "switch_id": dev.id,
                    "switch_name": dev.name,
                    "switch_ip": dev.host,
                    "switch_role": dev.role or "access",
                    "site_code": dev.site.code if dev.site else "",
                    "site_id": dev.site_id,
                    "port": p["interface"],
                    "description": p["description"],
                    "mode": p["mode"],
                    "vlan_id": p["vlan_id"],
                    "vlan_name": p["vlan_name"],
                    "admin_status": p["admin_status"],
                    "oper_status": p["oper_status"],
                    "is_uplink": p["is_uplink"],
                })

            sw_util = round((sw_active / sw_total) * 100, 1) if sw_total else 0
            switch_summaries.append({
                "id": dev.id,
                "name": dev.name,
                "host": dev.host,
                "role": dev.role or "access",
                "site_code": dev.site.code if dev.site else "",
                "site_id": dev.site_id,
                "model": dev.model or "Switch",
                "total_ports": sw_total,
                "active_ports": sw_active,
                "dormant_ports": sw_dormant,
                "disabled_ports": sw_disabled,
                "utilization_pct": sw_util,
                "backup_date": b.created_at.strftime("%Y-%m-%d %H:%M") if b.created_at else "—",
            })

        vlan_matrix = []
        vlan_mismatches = 0
        for vid in sorted(vlan_global.keys()):
            vdata = vlan_global[vid]
            names_list = sorted(list(vdata["all_names"]))
            has_mismatch = len(names_list) > 1
            if has_mismatch:
                vlan_mismatches += 1
            vlan_matrix.append({
                "vlan_id": vid,
                "primary_name": vdata["primary_name"],
                "all_names": names_list,
                "has_mismatch": has_mismatch,
                "total_access_ports": vdata["access_ports"],
                "total_switches": len(vdata["switches"]),
                "total_trunk_switches": len(vdata["trunk_switches"]),
                "switches": list(vdata["switches"].values()),
            })

        campus_util = round((active_ports / total_ports) * 100, 1) if total_ports else 0

        return {
            "kpis": {
                "total_switches": total_switches,
                "total_ports": total_ports,
                "active_ports": active_ports,
                "dormant_ports": dormant_ports,
                "disabled_ports": disabled_ports,
                "utilization_pct": campus_util,
                "total_vlans": len(vlan_matrix),
                "vlan_mismatches": vlan_mismatches,
                "has_live_data": bool(live_cache),
            },
            "vlan_matrix": vlan_matrix,
            "ports": all_ports,
            "switch_summaries": switch_summaries,
        }


def stream_live_port_audit(app, device_ids):
    """Stream live operational port status (connected vs dormant/notconnect vs disabled) over SSH."""
    import json as _json

    q = queue.Queue()
    cache_lock = threading.Lock()

    def timestamp():
        return datetime.utcnow().strftime("%H:%M:%S")

    total = len(device_ids) if device_ids else 0
    if total == 0:
        q.put({"type": "start", "total": 0, "ts": timestamp()})
        q.put({"type": "log", "level": "warn", "msg": "No switches with credentials found in selected scope.", "ts": timestamp()})
        q.put({"type": "done", "ts": timestamp()})
        q.put(None)
        while True:
            item = q.get()
            if item is None:
                break
            yield _json.dumps(item) + "\n"
        return

    def worker(did):
        with app.app_context():
            dev = db.session.get(Device, did)
            if not dev or not dev.credential_id:
                return

            dev_name = dev.name
            dev_host = dev.host

        q.put({"type": "log", "level": "info", "msg": f"[{dev_name}] Probing live port states via SSH ({dev_host})...", "ts": timestamp()})

        try:
            with app.app_context():
                dev = db.session.get(Device, did)
                live_ports = query_device_live_ports(dev)

            conn_c = sum(1 for p in live_ports.values() if p["status"] == "connected")
            dorm_c = sum(1 for p in live_ports.values() if p["status"] == "dormant")
            dis_c = sum(1 for p in live_ports.values() if p["status"] == "disabled")

            # Persist into data/live_ports_cache.json
            with cache_lock:
                try:
                    from .util import BASE_DIR
                    cache_file = BASE_DIR / "live_ports_cache.json"
                    cdata = {}
                    if cache_file.exists():
                        try:
                            cdata = json.loads(cache_file.read_text(encoding="utf-8"))
                        except Exception:
                            cdata = {}
                    for pname, pinfo in live_ports.items():
                        cdata[f"{did}:{pname}"] = pinfo["status"]
                        cdata[f"{did}:{pname}".lower()] = pinfo["status"]
                        cdata[f"{did}:{_norm_port(pname)}"] = pinfo["status"]
                        cdata[f"{did}:{_norm_port(pname)}".lower()] = pinfo["status"]
                    cache_file.write_text(json.dumps(cdata, indent=2), encoding="utf-8")
                except Exception as ce:
                    logger.warning("Cache write error: %s", ce)

            q.put({
                "type": "device",
                "device_id": did,
                "device_name": dev_name,
                "host": dev_host,
                "status": "ok",
                "connected_count": conn_c,
                "dormant_count": dorm_c,
                "disabled_count": dis_c,
                "ports": live_ports,
                "ts": timestamp(),
            })
        except Exception as e:
            q.put({
                "type": "device",
                "device_id": did,
                "device_name": dev_name,
                "status": "error",
                "error": str(e),
                "ts": timestamp(),
            })

    def run_all():
        try:
            with app.app_context():
                max_w = max(2, get_int("max_workers"))
            workers_n = min(total, max_w) if total > 0 else 1
            q.put({"type": "start", "total": total, "ts": timestamp()})
            with ThreadPoolExecutor(max_workers=workers_n) as pool:
                list(pool.map(worker, device_ids))
            q.put({"type": "done", "ts": timestamp()})
        except Exception as e:
            q.put({"type": "log", "level": "bad", "msg": f"Fatal audit error: {e}", "ts": timestamp()})
            q.put({"type": "done", "ts": timestamp()})
        finally:
            q.put(None)

    threading.Thread(target=run_all, daemon=True).start()
    while True:
        item = q.get()
        if item is None:
            break
        yield _json.dumps(item) + "\n"


# --------------------------------------------------------------------------- Hardware Health (CPU, Memory, Temperature, Fans, PSU)

def parse_junos_health(re_text, env_text):
    """
    Parses Junos RE and environment output to extract CPU, memory, temperatures, and fan/PSU statuses.
    """
    res = {
        "cpu_pct": None,
        "mem_pct": None,
        "temp_c": None,
        "temp_status": "ok",
        "fans_status": "ok",
        "psu_status": "ok",
        "sensor_details": []
    }

    # Extract CPU Idle from Master RE:
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

    # Extract Memory utilization
    mem_match = re.search(r"Memory utilization\s+(\d+)\s+percent", re_text)
    if mem_match:
        try:
            res["mem_pct"] = int(mem_match.group(1))
        except (ValueError, TypeError):
            pass

    # Primary Temperature from RE (or env)
    temp_match = re.search(r"Temperature\s+(\d+)\s+degrees\s+C", re_text)
    if not temp_match:
        temp_match = re.search(r"CPU temperature\s+(\d+)\s+degrees\s+C", re_text)
    if temp_match:
        try:
            res["temp_c"] = int(temp_match.group(1))
        except (ValueError, TypeError):
            pass

    # Parse Environment lines for fans, PSUs, and highest temperature
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
        if highest_temp >= 65:
            res["temp_status"] = "bad"
        elif highest_temp >= 52:
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
    res = {
        "cpu_pct": None,
        "mem_pct": None,
        "temp_c": None,
        "temp_status": "ok",
        "fans_status": "ok",
        "psu_status": "ok",
        "sensor_details": []
    }

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
                if res["temp_c"] >= 65:
                    res["temp_status"] = "bad"
                elif res["temp_c"] >= 52:
                    res["temp_status"] = "warn"
        except (ValueError, TypeError):
            pass

    return res


def parse_cisco_c1300_health(cpu_text, env_text):
    """
    Parses Cisco Catalyst 1300 / CBS output.
    """
    res = {
        "cpu_pct": None,
        "mem_pct": None,
        "temp_c": None,
        "temp_status": "ok",
        "fans_status": "ok",
        "psu_status": "ok",
        "sensor_details": []
    }

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
    res = {
        "cpu_pct": None,
        "mem_pct": None,
        "temp_c": None,
        "temp_status": "ok",
        "fans_status": "ok",
        "psu_status": "ok",
        "sensor_details": []
    }

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


def query_device_health(device):
    """
    Connects to a single network device and queries CPU, Memory, Temperature, Fan and Power stats.
    Returns a unified dictionary with health metrics.
    """
    platform = (device.platform or "").lower()
    conn = engine.connect(device)

    try:
        if "juniper" in platform:
            re_out = engine.run(conn, "show chassis routing-engine | no-more")
            env_out = engine.run(conn, "show chassis environment | no-more")
            data = parse_junos_health(re_out, env_out)
        elif "c1300" in platform or "cbs" in platform or "cisco_s300" in platform:
            cpu_out = engine.run(conn, "show cpu utilization")
            env_out = engine.run(conn, "show environment all")
            data = parse_cisco_c1300_health(cpu_out, env_out)
        elif "cisco" in platform:
            cpu_out = engine.run(conn, "show processes cpu | include CPU")
            mem_out = engine.run(conn, "show processes memory | include Processor")
            try:
                env_out = engine.run(conn, "show env all")
            except Exception:
                env_out = ""
            data = parse_cisco_ios_health(cpu_out, mem_out, env_out)
        elif "sonic" in platform:
            stat_out = engine.run(conn, "show status")
            data = parse_sonicos_health(stat_out)
        else:
            # Generic fallback (try standard Cisco IOS commands)
            try:
                cpu_out = engine.run(conn, "show processes cpu | include CPU")
            except Exception:
                cpu_out = ""
            try:
                mem_out = engine.run(conn, "show processes memory | include Processor")
            except Exception:
                mem_out = ""
            try:
                env_out = engine.run(conn, "show env all")
            except Exception:
                env_out = ""
            data = parse_cisco_ios_health(cpu_out, mem_out, env_out)

        # Compute overall device health status: 'ok', 'warn', 'bad'
        status = "ok"
        reasons = []

        if data["cpu_pct"] is not None:
            if data["cpu_pct"] >= 90:
                status = "bad"
                reasons.append(f"High CPU: {data['cpu_pct']}%")
            elif data["cpu_pct"] >= 75:
                if status != "bad":
                    status = "warn"
                reasons.append(f"Elevated CPU: {data['cpu_pct']}%")

        if data["mem_pct"] is not None:
            if data["mem_pct"] >= 90:
                status = "bad"
                reasons.append(f"High Memory: {data['mem_pct']}%")
            elif data["mem_pct"] >= 80:
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

    finally:
        try:
            conn.disconnect()
        except Exception:
            pass


def stream_scan_health(app, device_ids):
    """
    Generator streaming real-time hardware health scan results across network devices.
    """
    import json as _json

    total = len(device_ids)
    q = queue.Queue()

    def timestamp():
        return datetime.now().strftime("%H:%M:%S")

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
                health_data = query_device_health(d)

            q.put({
                "type": "device",
                "device_id": did,
                "data": health_data,
                "timestamp": timestamp(),
            })

            # Log summary
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

    def run_all():
        try:
            with app.app_context():
                max_w = max(2, get_int("max_workers"))
            workers_n = min(total, max_w) if total > 0 else 1
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
    while True:
        item = q.get()
        if item is None:
            break
        yield _json.dumps(item) + "\n"

