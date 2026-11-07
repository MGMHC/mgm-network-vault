"""Read ARP, MAC address and LLDP/CDP neighbour tables from switches over SSH."""
import re

from . import ssh
from .netutil import base_port, clean_mac, format_mac


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
            out = ssh.run(conn, cmd)
        except Exception:  # noqa: BLE001 - try the next form
            continue
        if not _cli_rejected(out):
            return out
    return ""


def _with_conn(device, conn, fn):
    """Run fn(conn) on a caller-supplied SSH session, or on a new one that is closed afterwards."""
    if conn is not None:
        return fn(conn)
    with ssh.session(device) as c:
        return fn(c)


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


_IPV4 = r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"


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
    m = re.match(r"^po(?:rt-channel)?\s*(\d+)$", base_port(port))
    if not m:
        return None
    for line in ssh.run(c, "show etherchannel summary").splitlines():
        if re.search(rf"\bPo{m.group(1)}\(", line):
            members = re.findall(r"(\S+?)\(P\)", line) or re.findall(r"(\S+?)\(\w+\)", line)[1:]
            return members[0] if members else None
    return None


def query_lldp_neighbor(device, port, conn=None, want_ip=False, cache=None):
    """Who is on the other end of `port` (a physical port or an ae/Po bundle)?

    Returns {"local_port", "parent", "system_name", "mgmt_ip"} or None. Read-only show commands only.
    cache: optional dict shared by one search, so a Juniper LLDP table is fetched once per switch."""
    target = base_port(port)

    def fetch(c):
        if device.is_juniper:
            key = ("junos-lldp", device.id)
            rows = cache.get(key) if cache is not None else None
            if rows is None:
                rows = _parse_junos_lldp_table(ssh.run(c, "show lldp neighbors | no-more"))
                if cache is not None:
                    cache[key] = rows
            hit = (next((r for r in rows if base_port(r.get("local")) == target), None)
                   or next((r for r in rows if base_port(r.get("parent")) == target), None))
            if not hit:
                return None
            nb = {"local_port": hit.get("local"), "parent": None if hit.get("parent") in ("", "-") else hit.get("parent"),
                  "system_name": hit.get("name") or "", "mgmt_ip": None}
            if want_ip:
                try:
                    det = ssh.run(c, f"show lldp neighbors interface {hit.get('local')} | no-more")
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
            det = ssh.run(c, f"show lldp neighbors {phys} detail")
            m_name = re.search(r"System Name:\s*(\S+)", det)
            if m_name:
                m_ip = re.search(r"Management Address(?:es)?:?[\s\S]{0,80}?" + _IPV4, det)
                return {"local_port": phys, "parent": parent, "system_name": m_name.group(1),
                        "mgmt_ip": m_ip.group(1) if m_ip else None}
        except Exception:  # noqa: BLE001
            pass
        try:
            det = ssh.run(c, f"show cdp neighbors {phys} detail")
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
        if short and short != name and not re.match(r"^\d+\.\d+\.\d+\.\d+$", name):
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
