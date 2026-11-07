"""Classify a switch port as uplink/trunk or edge (access) port, from its name and the saved config."""
import re
from functools import lru_cache

from .models import Backup
from .util import read_backup_file

# Words in a port description that mark a link to another switch. Shared with the VLAN audit.
UPLINK_WORDS = re.compile(
    r"uplink|trunk|dist|dsw|csw|core|to[-_]dist|to[-_]core|transit|cascad|interconnect|peer|backbone|"
    r"switch|sw[-_]|server-sw|aggregation", re.I)
_DESCRIPTION_LINE = re.compile(r"description\s+(.*)", re.I)
_LAG_PREFIXES = ("ae", "po", "port-channel", "eth-trunk", "lag", "trunk", "bundle-ether", "bond")
_SNIPPET_TRUNK = ("port-mode trunk", "interface-mode trunk", "switchport mode trunk",
                  "vlan members all", "802.3ad", "channel-group", "native-vlan-id")
_JUNOS_TRUNK = ("port-mode trunk", "interface-mode trunk", "802.3ad", "members all", "native-vlan-id")
_JUNOS_ACCESS = ("port-mode access", "interface-mode access")
_CISCO_TRUNK = ("switchport mode trunk", "switchport trunk", "channel-group", "portfast trunk")


def uplink_description(desc):
    """True when a port description names a link to another switch."""
    return bool(UPLINK_WORDS.search(desc or ""))


def _described_as_uplink(config_text):
    return any(uplink_description(d) for d in _DESCRIPTION_LINE.findall(config_text))


def is_uplink_port(port_name, device=None, learned_mac_count=1, port_config_snippet=None):
    """True when an interface is an uplink/LAG/trunk rather than an edge access port.

    Evidence, in order: LAG-style name, more than 2 MACs learned on it, the given config snippet, the
    port's config in the device's latest backup, and finally the device role (core/distribution ports
    are uplinks). With a device, call this inside an app context."""
    p = (port_name or "").lower().strip().split(".")[0].strip()
    if not p or p == "—":
        return False
    if p.startswith(_LAG_PREFIXES) or learned_mac_count > 2:
        return True
    if port_config_snippet:
        snip = port_config_snippet.lower()
        if any(k in snip for k in _SNIPPET_TRUNK) or _described_as_uplink(snip):
            return True
    if device is None:
        return False
    b = Backup.query.filter(Backup.device_id == device.id).order_by(Backup.created_at.desc()).first()
    verdict = _config_verdict(b.id, b.set_path, b.path, p) if b else None
    if verdict is not None:
        return verdict
    return device.role in ("core", "distribution")


@lru_cache(maxsize=4096)
def _config_verdict(backup_id, set_path, conf_path, port):
    """True/False when the backup's config says trunk/access for the port, None when it does not say.
    backup_id is part of the cache key: a new backup gets a fresh answer."""
    alts = {re.escape(port)}
    m = re.match(r"^([a-zA-Z]+)(?:-)?(\d+/\d+.*)$", port)
    if m:
        letters, num = m.groups()
        alts |= {re.escape(f"{letters}-{num}"), re.escape(f"{letters}{num}")}

    set_txt = read_backup_file(set_path) if set_path else ""
    if set_txt:
        lines = re.findall(rf"set interfaces (?:{'|'.join(alts)})(?:[\s\.\:]|$)(.*)", set_txt, re.I)
        if lines:
            v = _junos_verdict(" ".join(lines).lower(), members=r"vlan\s+members")
            if v is not None:
                return v

    conf = read_backup_file(conf_path) if conf_path else ""
    if not conf:
        return None
    m = re.match(r"^([a-zA-Z\-]+)\s*([\d\/\.\:]+)$", port)
    name = rf"(?:{re.escape(port)}|{re.escape(m.group(1))}[a-zA-Z]*\s*{re.escape(m.group(2))})" if m else re.escape(port)
    m_cisco = re.search(rf"interface\s+{name}\b(.*?)(?=\ninterface|\n!|\Z)", conf, re.S | re.I)
    if m_cisco:
        block = m_cisco.group(1).lower()
        if any(k in block for k in _CISCO_TRUNK) or _described_as_uplink(block):
            return True
        if "switchport mode access" in block:
            return False
    for alt in alts:
        m_junos = re.search(rf"\b{alt}\s*\{{", conf, re.I)
        if m_junos:
            return _junos_verdict(_braced_block(conf, m_junos.end() - 1).lower(), members=r"members\s+")
    return None


def _junos_verdict(text, members):
    if any(k in text for k in _JUNOS_TRUNK):
        return True
    if len(re.findall(members, text)) > 1 or re.search(r"members\s+\[.*\]", text):
        return True
    if _described_as_uplink(text):
        return True
    if any(k in text for k in _JUNOS_ACCESS):
        return False
    return None


def _braced_block(text, start):
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]
