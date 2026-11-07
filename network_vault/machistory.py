"""MAC/IP history: a scheduled, read-only sweep of the switches' MAC and ARP tables.

Reuses the Port Locator's query functions (no new switch parsers). Edge ports only: uplinks see every
MAC, so they are skipped. One row per (mac, switch, port, vlan); seeing it again only moves last_seen.
"""
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import timedelta

from sqlalchemy import or_

from . import ssh
from .models import Device, MacSighting, db, get_bool, get_int, now, set_setting, log_event
from .netutil import clean_mac, format_mac, is_ip
from .portclass import is_uplink_port
from .switch_tables import query_arp, query_mac_table


MAC_HISTORY_WORKERS = 4      # switches queried at the same time - a sweep must not flood the network
SWITCH_TIMEOUT = 300         # seconds one switch may take once its turn starts; then it is skipped
SWEEP_DEADLINE = 1800        # seconds the whole sweep may take; whatever is still running is skipped
POLL_SECONDS = 1.0
SEARCH_LIMIT = 500
ARP_ROLES = ("core", "distribution")

_SWEEP_LOCK = threading.Lock()


def is_running():
    return _SWEEP_LOCK.locked()


def _is_multicast(clean):
    return bool(int(clean[:2], 16) & 1)


def _edge_sightings(device, entries):
    """(mac, port, vlan) of the MACs learned on edge ports of one switch."""
    per_port = {}
    for e in entries:
        per_port[e["interface"]] = per_port.get(e["interface"], 0) + 1
    out = []
    for e in entries:
        port = e["interface"]
        if not port or port == "—" or _is_multicast(e["clean_mac"]):
            continue
        if is_uplink_port(port, device=device, learned_mac_count=per_port[port]):
            continue
        out.append((e["clean_mac"], port, str(e["vlan"] or "")))
    return out


def _collect_switch(app, device_id):
    """Log in to one switch and read its tables. Returns a dict; never raises."""
    name = f"#{device_id}"
    with app.app_context():
        try:
            d = db.session.get(Device, device_id)
            if not d:
                return {"id": device_id, "name": name, "ok": False, "error": "device removed"}
            name = d.name
            with ssh.session(d) as conn:
                macs = query_mac_table(d, conn=conn)
                arp = query_arp(d, conn=conn) if d.role in ARP_ROLES else []
            # query_mac_table swallows command errors and returns []. A live switch always has learned MACs,
            # so an empty table means the command failed or was rejected: report "no data", not "nothing here".
            return {"id": device_id, "name": name, "ok": True, "empty": not macs,
                    "sightings": _edge_sightings(d, macs),
                    "arp": [(a["clean_mac"], a["ip"]) for a in arp]}
        except Exception as e:  # noqa: BLE001 - timeout / auth / anything: skip this switch
            return {"id": device_id, "name": name, "ok": False, "error": f"{type(e).__name__}: {e}"}


def _sweep_targets():
    devs = Device.query.filter(Device.enabled.is_(True), Device.credential_id.isnot(None)).all()
    return sorted((d for d in devs if not d.is_firewall), key=lambda d: (d.role not in ARP_ROLES, d.name or ""))


def _arp_ips(results):
    """MAC -> sorted unique IPs seen in ARP across all switches."""
    found = {}
    for r in results:
        for mac, ip in r.get("arp", []):
            found.setdefault(mac, set()).add(ip)
    return {mac: sorted(ips) for mac, ips in found.items()}


def _pick_ip(stored, candidates):
    """Keep the stored IP while ARP still shows it for this MAC; else the first IP in sorted order."""
    if not candidates:
        return stored
    return stored if stored in candidates else candidates[0]


def _store_switch(r, ips, stamp):
    existing = {(s.mac, s.port, s.vlan): s for s in MacSighting.query.filter_by(device_id=r["id"]).all()}
    new = updated = 0
    for mac, port, vlan in set(r["sightings"]):
        row = existing.get((mac, port, vlan))
        if row is None:
            db.session.add(MacSighting(mac=mac, ip=_pick_ip(None, ips.get(mac)), device_id=r["id"], port=port,
                                       vlan=vlan, first_seen=stamp, last_seen=stamp))
            new += 1
        else:
            row.last_seen = stamp
            row.ip = _pick_ip(row.ip, ips.get(mac))
            updated += 1
    return new, updated


def _store(results, stamp):
    """Merge the collected sightings into the table, one commit per switch (short write locks).
    Returns (new, updated)."""
    ips = _arp_ips(results)
    new = updated = 0
    for r in results:
        if not r["ok"] or not r["sightings"]:
            continue
        try:
            n, u = _store_switch(r, ips, stamp)
            db.session.commit()
            new, updated = new + n, updated + u
        except Exception as e:  # noqa: BLE001 - one switch's rows failing must not lose the others
            db.session.rollback()
            log_event(f"MAC/IP history: could not store {r['name']} ({e})", "history", "ERROR", user="scheduler")
    return new, updated


def _name_of(app, device_id):
    try:
        with app.app_context():
            d = db.session.get(Device, device_id)
            return d.name if d else f"#{device_id}"
    except Exception:  # noqa: BLE001
        return f"#{device_id}"


def _collect_all(app, ids, workers):
    """Read the switches in parallel with a per-switch and a sweep-wide time cap. A switch over the cap is
    reported as timed out and its late answer is ignored. Never waits on a hung worker."""
    started, results = {}, {}

    def job(i):
        started[i] = time.monotonic()
        return _collect_switch(app, i)

    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mac-history")
    futs = {pool.submit(job, i): i for i in ids}
    t0 = time.monotonic()
    pending = set(futs)
    try:
        while pending:
            done, pending = wait(pending, timeout=POLL_SECONDS, return_when=FIRST_COMPLETED)
            for f in done:
                results[futs[f]] = f.result()
            tnow = time.monotonic()
            over_sweep = tnow - t0 > SWEEP_DEADLINE
            for f in list(pending):
                i = futs[f]
                if over_sweep or (i in started and tnow - started[i] > SWITCH_TIMEOUT):
                    f.cancel()
                    pending.discard(f)
                    results[i] = {"id": i, "name": _name_of(app, i), "ok": False,
                                  "error": "timed out" if i in started else "not reached before the sweep deadline"}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return [results[i] for i in ids]


def run_sweep(app, workers=None):
    """One full sweep. Returns a summary dict, or None when another sweep is already running."""
    if not _SWEEP_LOCK.acquire(blocking=False):
        return None
    try:
        stamp = now()
        with app.app_context():
            ids = [d.id for d in _sweep_targets()]
            log_event(f"MAC/IP history sweep started: {len(ids)} switch(es)", "history", user="scheduler")
        workers = max(1, min(workers or MAC_HISTORY_WORKERS, len(ids) or 1))
        results = _collect_all(app, ids, workers)
        with app.app_context():
            for r in results:
                if not r["ok"]:
                    log_event(f"MAC/IP history: {r['name']} skipped ({r['error']})", "history", "WARN", user="scheduler")
                elif r["empty"]:
                    log_event(f"MAC/IP history: {r['name']} returned no MAC table entries "
                              "(command failed or rejected?) - nothing recorded", "history", "WARN", user="scheduler")
            new, updated = _store(results, stamp)
            failed = sum(1 for r in results if not r["ok"])
            empty = sum(1 for r in results if r["ok"] and r["empty"])
            summary = {"switches": len(ids), "ok": len(ids) - failed - empty, "empty": empty, "failed": failed,
                       "new": new, "updated": updated}
            text = (f"{summary['ok']} of {len(ids)} switch(es) read, {empty} returned no MAC table, "
                    f"{failed} skipped; {new} new, {updated} refreshed")
            set_setting("mac_history_last_run", stamp.isoformat(timespec="seconds"))
            set_setting("mac_history_last_result", text)
            db.session.commit()
            log_event(f"MAC/IP history sweep finished: {text}", "history", user="scheduler")
        return summary
    except Exception as e:  # noqa: BLE001 - never let a sweep take the scheduler down
        with app.app_context():
            db.session.rollback()
            log_event(f"MAC/IP history sweep failed: {e}", "history", "ERROR", user="scheduler")
        return None
    finally:
        _SWEEP_LOCK.release()


def sweep_if_enabled(app):
    """Scheduler entry point: does nothing when the setting is off."""
    with app.app_context():
        if not get_bool("mac_history_enabled"):
            return None
    return run_sweep(app)


def purge_old(days=None):
    """Delete sightings not seen for `days` (setting default), and those of removed switches.
    Skipped (and logged) while a sweep runs: it must not delete rows the sweep has loaded."""
    if not _SWEEP_LOCK.acquire(blocking=False):
        log_event("MAC/IP history: purge skipped, a sweep is running (it runs again tomorrow)", "history",
                  user="system")
        return 0
    try:
        days = days if days is not None else get_int("mac_history_retention_days")
        gone = MacSighting.query.filter(~MacSighting.device_id.in_(db.session.query(Device.id))).delete(
            synchronize_session=False)
        n = 0
        if days > 0:
            n = MacSighting.query.filter(MacSighting.last_seen < now() - timedelta(days=days)).delete(
                synchronize_session=False)
        db.session.commit()
    finally:
        _SWEEP_LOCK.release()
    if n or gone:
        log_event(f"MAC/IP history: purged {n + gone} old row(s) (kept {days} days)", "history", user="system")
    return n + gone


def _like(text):
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search(base_query, text, limit=SEARCH_LIMIT):
    """Sightings matching a MAC (any format), IP, switch name or port, newest first.

    base_query must already be joined to Device and limited to the sites the user may see."""
    q = (text or "").strip()
    if q:
        cm = clean_mac(q)
        if cm:
            base_query = base_query.filter(MacSighting.mac == cm)
        elif is_ip(q):
            base_query = base_query.filter(MacSighting.ip == q)
        else:
            conds = [MacSighting.ip.ilike(_like(q), escape="\\"), Device.name.ilike(_like(q), escape="\\"),
                     MacSighting.port.ilike(_like(q), escape="\\")]
            hexonly = "".join(ch for ch in q.lower() if ch in "0123456789abcdef")
            if len(hexonly) >= 4 and len(hexonly) == len([c for c in q if c not in ":.-"]):
                conds.append(MacSighting.mac.like(_like(hexonly), escape="\\"))
            base_query = base_query.filter(or_(*conds))
    return (base_query.order_by(MacSighting.last_seen.desc(), MacSighting.first_seen.desc(), MacSighting.id.desc())
            .limit(limit).all())


def row_dict(s):
    return {"mac": format_mac(s.mac), "ip": s.ip or "", "switch_id": s.device_id,
            "switch": s.device.name if s.device else "", "port": s.port, "vlan": s.vlan,
            "first_seen": s.first_seen.isoformat(sep=" "), "last_seen": s.last_seen.isoformat(sep=" ")}
