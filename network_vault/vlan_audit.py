"""VLAN and port audit: parse VLANs and port allocations from saved configs, with live port status."""
import json
import logging
import queue
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from .portclass import uplink_description
from .models import Backup, Device, db
from .netutil import norm_port
from .scanprogress import DeviceSteps, fleet_progress, ndjson, timestamp, scan_workers
from .util import BASE_DIR, read_backup_file

logger = logging.getLogger("network_vault.vlan_audit")


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

    # 1. Parse defined VLANs: set vlans <name> vlan-id <id>
    for m in re.finditer(r'set vlans (\S+) vlan-id (\d+)', config_text):
        vname, vid = m.group(1), int(m.group(2))
        vlans_by_name[vname] = {"id": vid, "name": vname, "desc": ""}

    for m in re.finditer(r'set vlans (\S+) description (["\']?)(.*?)\2(?:\n|$)', config_text):
        vname = m.group(1)
        if vname in vlans_by_name:
            vlans_by_name[vname]["desc"] = m.group(3).strip()

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

    ports_map = {}

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

    for m in re.finditer(r'set interfaces ([a-z]+-\d+/\d+/\d+|ae\d+) unit 0 family ethernet-switching (?:port-mode|interface-mode) (trunk|access)', config_text):
        pname, mode = m.group(1), m.group(2)
        ports_map.setdefault(pname, {
            "interface": pname, "description": "", "mode": "access", "vlan_id": None,
            "vlan_name": "", "admin_status": "up", "oper_status": "dormant", "is_uplink": False
        })
        ports_map[pname]["mode"] = mode
        if mode == "trunk":
            ports_map[pname]["is_uplink"] = True

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
        if uplink_description(desc):
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

    po_modes = {}
    po_vlans = {}
    for po_m in re.finditer(r'interface\s+(Port-channel\d+)\s*\n((?:[ \t]+.*\n)+)', config_text):
        po_name = po_m.group(1)
        po_block = po_m.group(2)
        if re.search(r'switchport mode trunk', po_block):
            po_modes[po_name] = "trunk"
            tv_m = re.search(r'switchport trunk allowed vlan\s+(.*)', po_block)
            po_vlans[po_name] = tv_m.group(1).strip() if tv_m else "all"

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

        is_uplink = mode == "trunk" or uplink_description(desc)
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
            "interface": norm_port(iname),
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


def query_device_live_ports(device, on_step=None):
    """Query real-time interface status (connected, notconnect/dormant, disabled) via SSH.
    on_step(text, frac), if given, is told each step."""
    live_map = {}
    with DeviceSteps(on_step).session(device) as st:
        if device.profile.family == "cisco":
            out = st.run("show interfaces status")
            st.parsing()
            for line in out.splitlines():
                line = line.strip()
                if not line or line.startswith("Port") or line.startswith("-") or line.startswith("Ch "):
                    continue
                parts = line.split()
                if not parts:
                    continue
                pname = norm_port(parts[0])
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

        elif device.is_juniper:
            out_terse = st.run("show interfaces terse")
            st.parsing()
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

    return live_map


# Last live port status per switch port, so the audit page shows it without a new SSH scan.
LIVE_CACHE_FILE = "live_ports_cache.json"
_live_cache_lock = threading.Lock()


def _live_key(device_id, port):
    return f"{device_id}:{norm_port(port).lower()}"


def _load_live_cache():
    f = BASE_DIR / LIVE_CACHE_FILE
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except (OSError, ValueError) as e:
        logger.warning("Could not read %s: %s", f, e)
        return {}


def _save_live_statuses(device_id, live_ports):
    with _live_cache_lock:
        data = _load_live_cache()
        data.update({_live_key(device_id, name): port["status"] for name, port in live_ports.items()})
        try:
            (BASE_DIR / LIVE_CACHE_FILE).write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as e:  # the scan result is still streamed; only the saved copy is missing
            logger.warning("Could not write %s: %s", LIVE_CACHE_FILE, e)


def get_vlan_port_audit(app, device_ids):
    """VLAN Matrix & Port Audit of the given switches, from their latest backups and the live port cache."""
    with app.app_context():
        live_cache = _load_live_cache()

        q = Device.query.filter(Device.id.in_(device_ids), Device.enabled.is_(True))
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
            elif dev.profile.family == "cisco":
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

                p["oper_status"] = live_cache.get(_live_key(dev.id, p["interface"]), p["oper_status"])

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

    q = queue.Queue()


    total = len(device_ids) if device_ids else 0
    if total == 0:
        q.put({"type": "start", "total": 0, "ts": timestamp()})
        q.put({"type": "log", "level": "warn", "msg": "No switches with credentials found in selected scope.", "ts": timestamp()})
        q.put({"type": "done", "ts": timestamp()})
        q.put(None)
        yield from ndjson(q)
        return

    step = fleet_progress(q, total, timestamp)

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
                live_ports = query_device_live_ports(dev, on_step=lambda text, frac: step(did, dev_name, text, frac))

            conn_c = sum(1 for p in live_ports.values() if p["status"] == "connected")
            dorm_c = sum(1 for p in live_ports.values() if p["status"] == "dormant")
            dis_c = sum(1 for p in live_ports.values() if p["status"] == "disabled")

            _save_live_statuses(did, live_ports)

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
            step(did, dev_name, f"Done, {conn_c} connected, {dorm_c} dormant", 1.0)
        except Exception as e:
            q.put({
                "type": "device",
                "device_id": did,
                "device_name": dev_name,
                "status": "error",
                "error": str(e),
                "ts": timestamp(),
            })
            step(did, dev_name, "Failed", 1.0)

    def run_all():
        try:
            with app.app_context():
                workers_n = scan_workers(total)
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
    yield from ndjson(q)
