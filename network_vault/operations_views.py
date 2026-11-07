"""Network operations pages: Port Locator, MAC history, optics, hardware health, VLAN audit."""
import csv
import io
import threading
from datetime import datetime
from flask import Response, current_app, flash, jsonify, redirect, render_template, request, session, stream_with_context, url_for

from . import health, locator, machistory, netutil, optics, vlan_audit
from .models import Device, MacSighting, Site, db, get_bool, get_int, get_setting, log_event
from .web import bp, check_device_access, current_user, report_devices, require_write, scannable_ids, scoped_devices, selected_site_id, site_filter


@bp.route("/operations/port-locator")
def port_locator():
    u = current_user()
    devs = site_filter(Device.query, Device).order_by(Device.name).all()
    user_sites_list = Site.query.order_by(Site.code).all() if (u and u.is_group_admin) else (u.assigned_sites if u else [])
    usable = [d for d in devs if d.enabled and d.credential_id and not d.is_firewall]
    groups = sorted({(d.group or "").strip() for d in usable if (d.group or "").strip()}, key=str.lower)
    locations = sorted({(d.location or "").strip() for d in usable if (d.location or "").strip()}, key=str.lower)
    return render_template("port_locator.html", devices=usable, sites=user_sites_list, groups=groups,
                           locations=locations)


def _locator_scope(data):
    """Switches a Port Locator search may scan: site / group / location / switch filters, within site access."""
    u = current_user()
    q_devs = scoped_devices(data)
    group = (data.get("group") or "").strip()
    if group:
        q_devs = q_devs.filter(Device.group == group)
    location = (data.get("location") or "").strip()
    if location:
        q_devs = q_devs.filter(Device.location == location)
    user_site_ids = None if (not u or u.is_group_admin) else list(u.allowed_site_ids or [])
    return scannable_ids(q_devs), user_site_ids


def _history_query():
    """Sightings joined to their switch and limited to the sites the user may see."""
    return site_filter(MacSighting.query.join(Device, Device.id == MacSighting.device_id), Device)


@bp.route("/operations/mac-history")
def mac_history():
    q = request.args.get("q", "").strip()
    rows = machistory.search(_history_query(), q)
    return render_template(
        "mac_history.html", q=q, rows=rows, limit=machistory.SEARCH_LIMIT, running=machistory.is_running(),
        enabled=get_bool("mac_history_enabled"), interval=get_int("mac_history_interval_hours"),
        retention=get_int("mac_history_retention_days"), last_run=get_setting("mac_history_last_run"),
        last_result=get_setting("mac_history_last_result"))


@bp.route("/operations/mac-history/sweep", methods=["POST"])
@require_write
def mac_history_sweep():
    if machistory.is_running():
        flash("A sweep is already running.", "ok")
    else:
        app = current_app._get_current_object()
        log_event("MAC/IP history sweep requested", "history")
        threading.Thread(target=machistory.run_sweep, args=(app,), daemon=True, name="mac-history-manual").start()
        flash("Sweep started. Reload this page in a minute to see the result.", "ok")
    return redirect(url_for("web.mac_history", q=request.form.get("q", "")))


@bp.route("/api/operations/mac-history/<mac>")
def api_mac_history(mac):
    cm = netutil.clean_mac(mac)
    if not cm:
        return jsonify(error="Not a valid MAC address."), 400
    rows = (_history_query().filter(MacSighting.mac == cm).order_by(MacSighting.last_seen.desc(), MacSighting.id.desc())
            .limit(5).all())
    return jsonify(rows=[machistory.row_dict(r) for r in rows])


@bp.route("/api/operations/port-locator/stream", methods=["POST"])
def api_port_locator_stream():
    data = request.get_json(silent=True) or {}
    query_str = data.get("query", "").strip()
    if not query_str:
        return jsonify(error="Search target is required (IP or MAC address)."), 400
    dev_ids, user_site_ids = _locator_scope(data)
    app = current_app._get_current_object()
    run_id = data.get("run_id")
    full = bool(data.get("full"))
    log_event(f"Port locator search started for '{query_str}' ({len(dev_ids)} switches in scope"
              f"{', full scan' if full else ''})", "device", user=session.get("user"))
    return Response(
        stream_with_context(locator.stream_locate_host(app, dev_ids, query_str, run_id=run_id, full=full,
                                                          user_site_ids=user_site_ids)),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@bp.route("/api/operations/port-locator/status/<run_id>")
def api_port_locator_status(run_id):
    run = locator.get_locator_run(run_id)
    if not run:
        return jsonify(active=False), 404
    return jsonify(run)


@bp.route("/api/operations/port-locator/cancel/<run_id>", methods=["POST"])
def api_port_locator_cancel(run_id):
    cancelled = locator.cancel_locator_run(run_id)
    return jsonify(cancelled=cancelled)


@bp.route("/operations/optics", endpoint="optics")
def optics_page():
    u = current_user()
    devs = site_filter(Device.query, Device).order_by(Device.name).all()
    user_sites_list = Site.query.order_by(Site.code).all() if (u and u.is_group_admin) else (u.assigned_sites if u else [])
    return render_template("optics.html", devices=devs, sites=user_sites_list)


@bp.route("/api/operations/optics/stream", methods=["POST"])
def api_optics_stream():
    data = request.get_json(silent=True) or {}
    dev_ids = scannable_ids(scoped_devices(data))
    app = current_app._get_current_object()
    log_event(f"Optical diagnostics streaming scan started ({len(dev_ids)} switches)", "device", user=session.get("user"))
    return Response(
        stream_with_context(optics.stream_scan_optics(app, dev_ids)),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@bp.route("/api/devices/<int:dev_id>/optics")
def api_device_optics(dev_id):
    d = db.get_or_404(Device, dev_id)
    check_device_access(d)
    try:
        ports = optics.query_device_optics(d)
        log_event("Single-device optical diagnostics inspected", "device", device=d)
        return jsonify(ports=ports, device_name=d.name, host=d.host)
    except Exception as e:
        log_event(f"Optical diagnostics failed: {e}", "device", "ERROR", device=d)
        return jsonify(error=f"{type(e).__name__}: {e}"), 500


@bp.route("/operations/health", endpoint="health")
def health_page():
    u = current_user()
    devs = site_filter(Device.query, Device).order_by(Device.name).all()
    user_sites_list = Site.query.order_by(Site.code).all() if (u and u.is_group_admin) else (u.assigned_sites if u else [])
    return render_template("health.html", devices=devs, sites=user_sites_list)


@bp.route("/api/operations/health/stream", methods=["POST"])
def api_health_stream():
    data = request.get_json(silent=True) or {}
    dev_ids = scannable_ids(scoped_devices(data))
    app = current_app._get_current_object()
    log_event(f"Hardware health streaming scan started ({len(dev_ids)} switches)", "device", user=session.get("user"))
    return Response(
        stream_with_context(health.stream_scan_health(app, dev_ids)),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@bp.route("/api/devices/<int:dev_id>/health")
def api_device_health(dev_id):
    d = db.get_or_404(Device, dev_id)
    check_device_access(d)
    try:
        health_data = health.query_device_health(d)
        log_event(f"Single-device hardware health inspected: {d.name}", "device", device=d)
        return jsonify(health_data)
    except Exception as e:
        log_event(f"Hardware health check failed for {d.name}: {e}", "device", "ERROR", device=d)
        return jsonify(error=f"{type(e).__name__}: {e}"), 500


@bp.route("/operations/vlan-audit", endpoint="vlan_audit")
def vlan_audit_page():
    u = current_user()
    devs = site_filter(Device.query, Device).order_by(Device.name).all()
    user_sites_list = Site.query.order_by(Site.code).all() if (u and u.is_group_admin) else (u.assigned_sites if u else [])
    active_site_id = selected_site_id()
    return render_template("vlan_audit.html", devices=devs, sites=user_sites_list, active_site_id=active_site_id)


@bp.route("/api/operations/vlan-audit/data")
def api_vlan_audit_data():
    """Return VLAN Matrix & Port Audit aggregated from Vault backups."""
    app = current_app._get_current_object()
    result = vlan_audit.get_vlan_port_audit(app, [d.id for d in report_devices(request.args)])
    return jsonify(result)


@bp.route("/api/operations/vlan-audit/stream", methods=["POST"])
def api_vlan_audit_stream():
    """Probe live operational port status (connected vs dormant vs disabled) in parallel over SSH."""
    data = request.get_json(silent=True) or {}
    dev_ids = scannable_ids(scoped_devices(data))
    app = current_app._get_current_object()
    log_event(f"Live port audit started ({len(dev_ids)} switches)", "device", user=session.get("user"))
    return Response(
        stream_with_context(vlan_audit.stream_live_port_audit(app, dev_ids)),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@bp.route("/operations/vlan-audit/export.csv")
def vlan_audit_export_csv():
    """Export complete switchport inventory & VLAN assignments as CSV."""
    app = current_app._get_current_object()
    data = vlan_audit.get_vlan_port_audit(app, [d.id for d in report_devices(request.args)])

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([
        "Switch Name", "Switch IP", "Site", "Role", "Interface", "Description",
        "Mode", "VLAN ID", "VLAN Name", "Admin Status", "Oper Status", "Is Uplink"
    ])
    for p in data.get("ports", []):
        w.writerow([
            p.get("switch_name"), p.get("switch_ip"), p.get("site_code"), p.get("switch_role"),
            p.get("port"), p.get("description"), p.get("mode"), p.get("vlan_id") or "Trunk",
            p.get("vlan_name"), p.get("admin_status"), p.get("oper_status"),
            "Yes" if p.get("is_uplink") else "No"
        ])

    filename = f"vlan_port_audit_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
