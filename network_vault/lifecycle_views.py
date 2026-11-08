"""Hardware lifecycle (EOL/EOS) page, its export, and model discovery."""
import csv
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from flask import Response, current_app, jsonify, render_template, request, session, stream_with_context

from . import engine, lifecycle
from .models import Device, Site, db, get_int, log_event
from .web import BOM, authorized_device_ids, bp, scannable_ids, scoped_devices, site_filter


@bp.route("/operations/lifecycle", endpoint="lifecycle")
def lifecycle_page():
    q = site_filter(Device.query.filter(Device.enabled.is_(True)), Device)
    devices = q.all()
    analysis = lifecycle.analyze_device_lifecycle(devices)
    all_sites = Site.query.order_by(Site.code).all()
    return render_template("lifecycle.html", analysis=analysis, sites=all_sites)


@bp.route("/api/operations/lifecycle/fetch", methods=["POST"])
def api_lifecycle_fetch_all():
    """
    Live streaming discovery job: connects to switches, queries hardware model, serial, and running OS,
    and returns real-time progress.
    """
    data = request.get_json(silent=True) or {}
    dev_ids = authorized_device_ids(scannable_ids(scoped_devices(data)))  # 403 for read-only users
    app = current_app._get_current_object()
    # Read request-bound values now: the worker threads below have no request context.
    username = session.get("user")
    max_workers = get_int("max_workers")

    def stream_discover():
        import json as _json
        import queue

        q = queue.Queue()
        total = len(dev_ids)
        q.put({"type": "init", "total": total, "msg": f"Starting live inventory and firmware fetch across {total} switches..."})

        if not dev_ids:
            q.put({"type": "complete", "msg": "No switches available with credentials in this scope."})
            q.put(None)
            while True:
                item = q.get()
                if item is None:
                    break
                yield _json.dumps(item) + "\n"
            return

        done_cnt = 0
        from threading import Lock
        lock = Lock()

        def worker(did):
            nonlocal done_cnt
            dev_name = ""
            with app.app_context():
                d = db.session.get(Device, did)
                if not d:
                    return
                dev_name = d.name

            q.put({"type": "log", "level": "info", "msg": f"[{dev_name}] Connecting over SSH to discover hardware inventory & firmware..."})
            ok = False
            try:
                with app.app_context():
                    ok = engine.discover_device(did, user=username)
            except Exception as e:
                q.put({"type": "log", "level": "error", "msg": f"[{dev_name}] SSH discovery error: {e}"})

            with lock:
                done_cnt += 1
                cur_done = done_cnt

            pct = int((cur_done / total) * 100)
            if ok:
                with app.app_context():
                    d = db.session.get(Device, did)
                    q.put({
                        "type": "device",
                        "device_id": did,
                        "device_name": dev_name,
                        "hw_model": d.hw_model,
                        "serial": d.serial,
                        "sw_version": d.sw_version,
                        "percent": pct,
                        "done": cur_done,
                        "total": total
                    })
                    q.put({"type": "log", "level": "ok", "msg": f"[{dev_name}] Detected: Model {d.hw_model or 'Unknown'} | SN {d.serial or 'N/A'} | OS {d.sw_version or 'N/A'}"})
            else:
                q.put({
                    "type": "progress",
                    "percent": pct,
                    "done": cur_done,
                    "total": total
                })

        def run_all():
            try:
                workers = min(total, max(2, max_workers))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    list(pool.map(worker, dev_ids))
                q.put({"type": "complete", "msg": "Live discovery finished across switches."})
            except Exception as e:
                q.put({"type": "log", "level": "error", "msg": f"Discovery failure: {e}"})
                q.put({"type": "complete", "msg": "Finished with errors."})
            finally:
                q.put(None)

        threading.Thread(target=run_all, daemon=True).start()
        while True:
            item = q.get()
            if item is None:
                break
            yield _json.dumps(item) + "\n"

    log_event(f"Started live hardware & firmware discovery fetch across {len(dev_ids)} switches", "device", user=username)
    return Response(
        stream_with_context(stream_discover()),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@bp.route("/api/devices/<int:dev_id>/discover", methods=["POST"])
def api_device_discover(dev_id):
    d = db.get_or_404(Device, dev_id)
    authorized_device_ids([dev_id])  # 403 for read-only users and other sites' devices
    try:
        ok = engine.discover_device(dev_id, user=session.get("user"))
        if ok:
            db.session.refresh(d)
            log_event(f"Live hardware discovery succeeded for {d.name}", "device", device=d)
            return jsonify(
                ok=True,
                hw_model=d.hw_model,
                serial=d.serial,
                sw_version=d.sw_version
            )
        else:
            return jsonify(ok=False, error="Device discovery failed or switch unreachable"), 500
    except Exception as e:
        log_event(f"Discovery error for {d.name}: {e}", "device", "ERROR", device=d)
        return jsonify(ok=False, error=str(e)), 500


def _export_rows(it: dict) -> list[tuple[dict, dict]]:
    """(vc_member, lifecycle row) pairs for one analysed device: one per Virtual Chassis member
    (Master, Backup, Linecard), each with its own serial, model and EOL dates; else the device itself."""
    members = it["device"].vc_members
    if not members:
        return [({}, it)]
    rows = []
    for m in members:
        hw = m.get("model") or it["hw_model"]
        entry = lifecycle.match_lifecycle_entry(hw, it["device"].model)
        _, status_label, _ = lifecycle.calculate_lifecycle_status(entry)
        rec_sw = entry.get("recommended_sw", "") if entry else ""
        _, fw_label = lifecycle.check_firmware_compliance(it["sw_version"], rec_sw)
        rows.append((m, {"hw_model": hw, "serial": m.get("serial") or "N/A", "entry": entry,
                         "status_label": status_label, "recommended_sw": rec_sw, "fw_label": fw_label}))
    return rows


@bp.route("/operations/lifecycle/export.csv")
def lifecycle_export_csv():
    q = site_filter(Device.query.filter(Device.enabled.is_(True)), Device)
    devices = q.all()
    analysis = lifecycle.analyze_device_lifecycle(devices)

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([
        "Switch Name", "IP Address", "Site", "Group / Floor", "Role", "VC Member", "VC Role",
        "Hardware Model", "Detected Model", "Serial Number", "Running Software",
        "Recommended Software", "Firmware Status", "Lifecycle Status",
        "End of Sale (EOS)", "End of SW Maintenance (EoSW)", "End of Security Support (EoSec)",
        "Last Date of Support (EOL)", "Notes"
    ])

    for it in analysis["devices"]:
        d = it["device"]
        site_name = d.site.code if d.site else "N/A"
        for member, row in _export_rows(it):
            entry = row["entry"] or {}
            w.writerow([
                d.name, d.host, site_name, d.group, d.role,
                member.get("id", ""), member.get("role", ""),
                d.model, row["hw_model"], row["serial"], it["sw_version"],
                row["recommended_sw"], row["fw_label"], row["status_label"],
                entry.get("eos_date", "N/A"), entry.get("eosw_date", "N/A"), entry.get("eosec_date", "N/A"),
                entry.get("eol_date", "N/A"), entry.get("notes", "")
            ])

    return Response(
        BOM + out.getvalue(),
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=lifecycle_eol_audit_report.csv"}
    )
