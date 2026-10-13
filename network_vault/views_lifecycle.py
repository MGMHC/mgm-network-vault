"""HTTP routes for Hardware & Firmware EOL/EOS Lifecycle Dashboard."""
import csv
import io
from flask import Blueprint, Response, jsonify, render_template, request, session
from sqlalchemy import or_

from . import lifecycle
from .models import Device, Site, User, db

bp_lifecycle = Blueprint("lifecycle", __name__)
BOM = "\ufeff"


def _current_user():
    uname = session.get("user")
    return User.query.filter_by(username=uname).first() if uname else None


def _active_site_id():
    u = _current_user()
    if not u:
        return None
    if u.is_group_admin:
        sid = session.get("active_site_id")
        if sid == "all" or sid is None:
            return None
        try:
            return int(sid)
        except (ValueError, TypeError):
            return None
    allowed = u.allowed_site_ids
    if not allowed:
        return None
    if len(allowed) == 1:
        return allowed[0]
    sid = session.get("active_site_id")
    if sid == "all":
        return None
    try:
        val = int(sid)
        return val if val in allowed else allowed[0]
    except (ValueError, TypeError):
        return allowed[0]


def _site_filter(q):
    sid = _active_site_id()
    u = _current_user()
    if sid is not None:
        return q.filter(Device.site_id == sid)
    if u and not u.is_group_admin:
        allowed = u.allowed_site_ids or []
        return q.filter(Device.site_id.in_(allowed))
    return q


@bp_lifecycle.route("/operations/lifecycle")
def dashboard():
    q = _site_filter(Device.query.filter(Device.enabled.is_(True)))
    devices = q.all()
    analysis = lifecycle.analyze_device_lifecycle(devices)
    all_sites = Site.query.order_by(Site.code).all()
    return render_template("lifecycle.html", analysis=analysis, sites=all_sites)


@bp_lifecycle.route("/operations/lifecycle/export.csv")
def export_csv():
    q = _site_filter(Device.query.filter(Device.enabled.is_(True)))
    devices = q.all()
    analysis = lifecycle.analyze_device_lifecycle(devices)

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([
        "Switch Name", "IP Address", "Site", "Group / Floor", "Role",
        "Hardware Model", "Detected Model", "Serial Number", "Running Software",
        "Recommended Software", "Firmware Status", "Lifecycle Status",
        "End of Sale (EOS)", "End of SW Maintenance (EoSW)", "End of Security Support (EoSec)",
        "Last Date of Support (EOL)", "Notes"
    ])

    for it in analysis["items"]:
        d = it["device"]
        site_name = d.site.code if d.site else "N/A"
        entry = it["entry"] or {}
        w.writerow([
            d.name, d.host, site_name, d.group, d.role,
            d.model, it["hw_model"], it["serial"], it["sw_version"],
            it["recommended_sw"], it["fw_label"], it["status_label"],
            it["eos_date"], entry.get("eosw_date", "N/A"), it["eosec_date"],
            it["eol_date"], it["notes"]
        ])

    return Response(
        BOM + out.getvalue(),
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=lifecycle_eol_audit_report.csv"}
    )
