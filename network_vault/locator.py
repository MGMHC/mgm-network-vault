"""Port Locator: find the switch port a MAC or IP address is connected to, tracing over LLDP."""
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import ssh
from .scanprogress import ndjson, timestamp
from .models import Device, db
from .netutil import clean_mac, format_mac, is_ip, lookup_oui
from .portclass import is_uplink_port
from .switch_tables import match_neighbor, query_arp, query_lldp_neighbor, query_mac_table


LOCATOR_WORKERS = 16   # parallel SSH sessions for a Port Locator scan (independent of backup max_workers)


MAX_TRACE_HOPS = 4     # LLDP hops followed from the distribution/core switch


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
        usable = [d for d in usable if not d.is_firewall and _ok_site(d.site_id)]
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
                    with ssh.session(d) as conn:
                        hits = query_mac_table(d, target_mac_clean=mac_clean, conn=conn)
                        for h in hits:
                            h["is_uplink"] = is_uplink_port(h["interface"], device=d)
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

            def port_note(h):
                if h.get("lldp_neighbor"):
                    return f"link to {h['lldp_neighbor']} (LLDP)"
                return "uplink / trunk" if h["is_uplink"] else "access port"

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

    yield from ndjson(q)
