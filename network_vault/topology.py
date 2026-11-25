"""Campus & site L2 topology discovery (LLDP/CDP) and graph builder."""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import ssh
from .models import Device, TopologyLink, db, get_int, log_event, now
from .netutil import base_port
from .switch_tables import match_neighbor, query_all_lldp_neighbors


def _device_node(d):
    return {
        "id": f"dev:{d.id}",
        "device_id": d.id,
        "name": d.name,
        "hostname": d.hostname or "",
        "host": d.host or "",
        "role": d.role or "access",
        "platform": d.platform or "",
        "model": d.hw_model or d.model or "",
        "status": d.status or "unknown",
        "ping_ms": d.ping_ms,
        "site_id": d.site_id,
        "site_code": d.site.code if d.site else "",
        "group": d.group or "",
        "location": d.location or "",
        "is_vc": bool(d.is_vc),
        "vendor": d.profile.family,
        # Member ids ("0".."9" on a Junos VC, "Primary"/"Secondary" on a SonicWall HA pair) for the stacked drawing.
        "members": [{"id": str(m.get("id", "")), "role": m.get("role", ""), "model": m.get("model", "")}
                    for m in d.vc_members] if d.is_vc else [],
        "external": False,
    }


def get_cached_topology(devices):
    """Build {nodes, edges, stats, last_scan} from persisted TopologyLink rows for `devices`."""
    dev_map = {d.id: d for d in devices}
    nodes = {f"dev:{d.id}": _device_node(d) for d in devices}
    if not dev_map:
        return {"nodes": [], "edges": [], "stats": {"devices": 0, "links": 0, "external": 0, "lags": 0}, "last_scan": None}

    links = TopologyLink.query.filter(TopologyLink.src_device_id.in_(list(dev_map.keys()))).all()
    edges = []
    seen_pairs = {}
    last_scan = None

    for lk in links:
        if lk.discovered_at and (last_scan is None or lk.discovered_at > last_scan):
            last_scan = lk.discovered_at

        src_key = f"dev:{lk.src_device_id}"
        if lk.dst_device_id and lk.dst_device_id in dev_map:
            dst_key = f"dev:{lk.dst_device_id}"
            # Deduplicate bidirectional links between two Vault devices (matching either physical port or LAG parent)
            a, b = (lk.src_device_id, lk.dst_device_id)
            src_ports = {p for p in (base_port(lk.src_port), base_port(lk.src_parent)) if p}
            dst_p = base_port(lk.dst_port)
            existing = None
            for sp in src_ports:
                k = (a, b, sp, dst_p) if a <= b else (b, a, dst_p, sp)
                if k in seen_pairs:
                    existing = seen_pairs[k]
                    break
            if existing is not None:
                if lk.src_parent and not existing["is_lag"]:
                    existing["is_lag"] = True
                if lk.src_port and lk.src_port not in existing["ports_b"]:
                    existing["ports_b"].append(lk.src_port)
                continue

            edge = {
                "id": f"e:{lk.id}",
                "source": src_key,
                "target": dst_key,
                "src_port": lk.src_port,
                "src_parent": lk.src_parent,
                "dst_port": lk.dst_port,
                "ports_a": [lk.src_port] if lk.src_port else [],
                "ports_b": [lk.dst_port] if lk.dst_port else [],
                "is_lag": bool(lk.src_parent),
                "protocol": lk.protocol,
                "external": False,
            }
            for sp in src_ports or {""}:
                k = (a, b, sp, dst_p) if a <= b else (b, a, dst_p, sp)
                seen_pairs[k] = edge
            edges.append(edge)
        elif lk.dst_name or lk.dst_mgmt_ip:
            ext_label = lk.dst_name or lk.dst_mgmt_ip
            ext_key = f"ext:{ext_label.lower()}"
            if ext_key not in nodes:
                nodes[ext_key] = {
                    "id": ext_key,
                    "device_id": None,
                    "name": ext_label,
                    "hostname": lk.dst_name or "",
                    "host": lk.dst_mgmt_ip or "",
                    "role": "external",
                    "platform": "",
                    "model": "External / Unmanaged",
                    "status": "unknown",
                    "ping_ms": None,
                    "site_id": lk.site_id,
                    "site_code": dev_map[lk.src_device_id].site.code if dev_map[lk.src_device_id].site else "",
                    "group": "",
                    "location": "",
                    "is_vc": False,
                    "external": True,
                }
            edges.append({
                "id": f"e:{lk.id}",
                "source": src_key,
                "target": ext_key,
                "src_port": lk.src_port,
                "src_parent": lk.src_parent,
                "dst_port": lk.dst_port,
                "ports_a": [lk.src_port] if lk.src_port else [],
                "ports_b": [lk.dst_port] if lk.dst_port else [],
                "is_lag": bool(lk.src_parent),
                "protocol": lk.protocol,
                "external": True,
            })

    ext_count = sum(1 for n in nodes.values() if n["external"])
    lag_count = sum(1 for e in edges if e["is_lag"])
    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "stats": {
            "devices": len(devices),
            "links": len(edges),
            "external": ext_count,
            "lags": lag_count,
        },
        "last_scan": last_scan.strftime("%Y-%m-%d %H:%M:%S") if last_scan else None,
    }


_TIER = {"firewall": 0, "core": 1, "distribution": 2, "access": 3, "external": 4}

UPLINK_HEADERS = ["Site", "Switch", "Switch IP", "Role", "Location", "Uplink Port(s)", "LAG",
                  "Neighbour", "Neighbour IP", "Neighbour Role", "Neighbour Port(s)", "Protocol", "Link Type"]


def uplink_rows(topo):
    """One row per link from get_cached_topology(), the lower-tier switch first (its uplink to the neighbour)."""
    nodes = {n["id"]: n for n in topo["nodes"]}
    rows = []
    for e in topo["edges"]:
        a, b = nodes[e["source"]], nodes[e["target"]]
        pa, pb = e["ports_a"], e["ports_b"]
        # External neighbours stay on the right; otherwise the deeper tier (access) is the uplinking side.
        if not b["external"] and _TIER.get(a["role"], 3) < _TIER.get(b["role"], 3):
            a, b, pa, pb = b, a, pb, pa
        rows.append([
            a["site_code"], a["name"], a["host"], a["role"], a["location"], ", ".join(pa),
            (e["src_parent"] or "") if e["is_lag"] else "",
            b["name"], b["host"], b["role"], ", ".join(pb), (e["protocol"] or "").upper(),
            "External" if e["external"] else ("LAG" if e["is_lag"] else "Single"),
        ])
    rows.sort(key=lambda r: (r[0], _TIER.get(r[3], 3), r[1], r[5]))
    return rows


def _probe_device_lldp(app, dev_id, all_dev_ids):
    """Query LLDP/CDP neighbors for one switch. Returns (dev_id, name, ok, neighbors_or_error)."""
    with app.app_context():
        d = db.session.get(Device, dev_id)
        if not d:
            return dev_id, f"#{dev_id}", False, "device not found"
        all_devs = Device.query.filter(Device.id.in_(all_dev_ids)).all()
        try:
            with ssh.session(d) as conn:
                raw_nbs = query_all_lldp_neighbors(d, conn=conn)
            matched = []
            for nb in raw_nbs:
                peer = match_neighbor(nb, all_devs, site_id=d.site_id)
                matched.append({
                    "local_port": nb.get("local_port") or "",
                    "parent": nb.get("parent") or "",
                    "remote_port": nb.get("remote_port") or "",
                    "system_name": nb.get("system_name") or "",
                    "mgmt_ip": nb.get("mgmt_ip") or "",
                    "protocol": nb.get("protocol") or "lldp",
                    "dst_device_id": peer.id if peer and peer.id != d.id else None,
                })
            return dev_id, d.name, True, matched
        except Exception as e:  # noqa: BLE001
            return dev_id, d.name, False, f"{type(e).__name__}: {e}"


def stream_discover_topology(app, dev_ids):
    """NDJSON stream: probe LLDP/CDP on each switch in `dev_ids`, persist TopologyLink rows, yield final graph."""
    def emit(obj):
        return json.dumps(obj) + "\n"

    with app.app_context():
        all_dev_ids = [d.id for d in Device.query.all()]
        workers = max(1, min(get_int("max_workers") or 4, len(dev_ids) or 1, 8))

    total = len(dev_ids)
    yield emit({"type": "start", "total": total})
    if not total:
        with app.app_context():
            yield emit({"type": "done", "graph": get_cached_topology([]), "ok": 0, "failed": 0})
        return

    ok_count = fail_count = 0
    done_count = 0
    stamp = now()

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="topo-lldp") as pool:
        futs = {pool.submit(_probe_device_lldp, app, did, all_dev_ids): did for did in dev_ids}
        for fut in as_completed(futs):
            did, name, ok, payload = fut.result()
            done_count += 1
            if ok:
                ok_count += 1
                with app.app_context():
                    d = db.session.get(Device, did)
                    site_id = d.site_id if d else None
                    TopologyLink.query.filter_by(src_device_id=did).delete(synchronize_session=False)
                    for nb in payload:
                        db.session.add(TopologyLink(
                            site_id=site_id,
                            src_device_id=did,
                            src_port=nb["local_port"][:64],
                            src_parent=nb["parent"][:64],
                            dst_device_id=nb["dst_device_id"],
                            dst_name=nb["system_name"][:128],
                            dst_port=nb["remote_port"][:64],
                            dst_mgmt_ip=nb["mgmt_ip"][:64],
                            protocol=nb["protocol"][:16],
                            discovered_at=stamp,
                        ))
                    db.session.commit()
                yield emit({"type": "progress", "done": done_count, "total": total, "device": name,
                            "ok": True, "neighbors": len(payload)})
            else:
                fail_count += 1
                yield emit({"type": "progress", "done": done_count, "total": total, "device": name,
                            "ok": False, "error": payload})

    with app.app_context():
        devs = Device.query.filter(Device.id.in_(dev_ids)).order_by(Device.name).all()
        graph = get_cached_topology(devs)
        log_event(f"Topology LLDP discovery finished: {ok_count}/{total} switches, "
                  f"{graph['stats']['links']} link(s)", "device", user="system")
        yield emit({"type": "done", "graph": graph, "ok": ok_count, "failed": fail_count})
