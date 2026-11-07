"""DCIM rack elevations: rack groups, racks, mounted devices and reservations (page and JSON API)."""
from flask import render_template, request, jsonify
from .models import db, Site, Device, RackGroup, Rack, RackReservation
from .web import bp, check_site, current_user, require_write, selected_site_id, site_filter, site_from, site_records



@bp.route('/racks')
def racks():
    """DCIM Visual Rack Elevations & Equipment Cabinets."""
    u = current_user()
    devs = site_filter(Device.query, Device).order_by(Device.name).all()
    user_sites_list = Site.query.order_by(Site.code).all() if (u and u.is_group_admin) else (u.assigned_sites if u else [])
    active_site_id = selected_site_id()
    return render_template('racks.html', devices=devs, sites=user_sites_list, active_site_id=active_site_id)

@bp.route('/api/racks')
def api_racks_list():
    group_id = request.args.get('group_id')

    q = site_records(Rack.query, Rack, request.args)

    if group_id and group_id != 'all':
        try:
            gid = int(group_id)
            q = q.filter(Rack.group_id == gid)
        except (ValueError, TypeError):
            pass

    racks = q.order_by(Rack.name).all()
    groups = RackGroup.query.order_by(RackGroup.name).all()
    sites = Site.query.order_by(Site.name).all()

    total_racks = len(racks)
    total_units = sum(r.u_height for r in racks)
    occupied_units = sum(r.calculate_utilization()['occupied'] for r in racks)
    reserved_units = sum(r.calculate_utilization()['reserved'] for r in racks)
    overall_util = round(((occupied_units + reserved_units) / total_units) * 100, 1) if total_units > 0 else 0

    return jsonify({
        'ok': True,
        'kpis': {
            'total_racks': total_racks,
            'total_units': total_units,
            'occupied_units': occupied_units,
            'reserved_units': reserved_units,
            'overall_utilization': overall_util
        },
        'racks': [r.to_dict() for r in racks],
        'groups': [g.to_dict() for g in groups],
        'sites': [{'id': s.id, 'name': s.name, 'code': s.code} for s in sites]
    })

@bp.route('/api/racks/<int:rack_id>')
def api_rack_detail(rack_id):
    r = db.session.get(Rack, rack_id)
    if not r or not current_user().has_site_access(r.site_id):
        return jsonify({'error': 'Rack not found'}), 404

    face = request.args.get('face', 'front')
    front_elevation = r.get_elevation('front')
    rear_elevation = r.get_elevation('rear')

    unmounted = Device.query.filter(
        (Device.rack_id.is_(None)) | (Device.rack_position.is_(None))
    ).order_by(Device.name).all()

    return jsonify({
        'ok': True,
        'rack': r.to_dict(),
        'face': face,
        'elevation_front': front_elevation,
        'elevation_rear': rear_elevation,
        'unmounted_devices': [{
            'id': d.id,
            'name': d.name,
            'model': d.model or d.hw_model or d.platform,
            'role': d.role,
            'host': d.host,
            'status': d.status
        } for d in unmounted]
    })

@bp.route('/api/racks', methods=['POST'])
@require_write
def api_save_rack():
    data = request.json or {}
    rid = data.get('id')
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Rack name is required'}), 400

    u_height = int(data.get('u_height') or 42)
    group_id = data.get('group_id')
    group_id = int(group_id) if group_id else None
    site_id = site_from(data)

    if rid:
        r = db.session.get(Rack, rid)
        if not r:
            return jsonify({'error': 'Rack not found'}), 404
        check_site(r.site_id)
    else:
        r = Rack()
        db.session.add(r)

    r.name = name
    r.facility_id = (data.get('facility_id') or '').strip()
    r.status = data.get('status') or 'active'
    r.role = data.get('role') or 'network'
    r.u_height = u_height
    r.desc_units = bool(data.get('desc_units', True))
    r.outer_width = int(data.get('outer_width') or 600)
    r.outer_depth = int(data.get('outer_depth') or 1000)
    r.group_id = group_id
    r.site_id = site_id
    r.comments = (data.get('comments') or '').strip()

    db.session.commit()
    return jsonify({'ok': True, 'rack': r.to_dict()})

@bp.route('/api/racks/<int:rack_id>/delete', methods=['POST'])
@require_write
def api_delete_rack(rack_id):
    r = db.session.get(Rack, rack_id)
    if r:
        check_site(r.site_id)
        for d in r.devices:
            d.rack_id = None
            d.rack_position = None
        db.session.delete(r)
        db.session.commit()
    return jsonify({'ok': True})

@bp.route('/api/rack-groups', methods=['POST'])
@require_write
def api_save_rack_group():
    data = request.json or {}
    gid = data.get('id')
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Rack group name is required'}), 400

    site_id = site_from(data)

    if gid:
        g = db.session.get(RackGroup, gid)
        if not g:
            return jsonify({'error': 'Group not found'}), 404
        check_site(g.site_id)
    else:
        g = RackGroup()
        db.session.add(g)

    g.name = name
    g.description = (data.get('description') or '').strip()
    g.site_id = site_id
    db.session.commit()
    return jsonify({'ok': True, 'group': g.to_dict()})

@bp.route('/api/rack-groups/<int:gid>/delete', methods=['POST'])
@require_write
def api_delete_rack_group(gid):
    g = db.session.get(RackGroup, gid)
    if g:
        check_site(g.site_id)
        db.session.delete(g)
        db.session.commit()
    return jsonify({'ok': True})

@bp.route('/api/racks/mount', methods=['POST'])
@require_write
def api_mount_device():
    data = request.json or {}
    dev_id = data.get('device_id')
    rack_id = data.get('rack_id')
    position = data.get('position')
    face = data.get('face') or 'front'
    height = int(data.get('height') or 1)

    if not dev_id or not rack_id or position is None:
        return jsonify({'error': 'Device, Rack, and Unit Position are required'}), 400

    dev = db.session.get(Device, int(dev_id))
    if dev:
        check_site(dev.site_id)
    rack = db.session.get(Rack, int(rack_id))
    if rack:
        check_site(rack.site_id)
    if not dev or not rack:
        return jsonify({'error': 'Device or Rack not found'}), 404

    position = int(position)
    if position < 1 or position > rack.u_height:
        return jsonify({'error': f'Position must be between 1 and {rack.u_height}'}), 400

    dev.rack_id = rack.id
    dev.rack_position = position
    dev.rack_face = face
    dev.rack_height = height

    db.session.commit()
    return jsonify({'ok': True, 'rack': rack.to_dict(include_elevation=True, face=face)})

@bp.route('/api/racks/unmount', methods=['POST'])
@require_write
def api_unmount_device():
    data = request.json or {}
    dev_id = data.get('device_id')
    if not dev_id:
        return jsonify({'error': 'Device ID is required'}), 400

    dev = db.session.get(Device, int(dev_id))
    if dev:
        check_site(dev.site_id)
        dev.rack_id = None
        dev.rack_position = None
        db.session.commit()
    return jsonify({'ok': True})

@bp.route('/api/racks/reservations', methods=['POST'])
@require_write
def api_save_reservation():
    data = request.json or {}
    rack_id = data.get('rack_id')
    unit = data.get('unit')
    height = int(data.get('height') or 1)
    desc = (data.get('description') or '').strip()

    if not rack_id or unit is None or not desc:
        return jsonify({'error': 'Rack, Unit position, and Description are required'}), 400

    rack = db.session.get(Rack, int(rack_id))
    if not rack:
        return jsonify({'error': 'Rack not found'}), 404
    check_site(rack.site_id)

    u = current_user()
    res = RackReservation(
        rack_id=rack.id,
        unit=int(unit),
        height=height,
        description=desc,
        user=u.username if u else ''
    )
    db.session.add(res)
    db.session.commit()
    return jsonify({'ok': True, 'reservation': res.to_dict()})

@bp.route('/api/racks/reservations/<int:res_id>/delete', methods=['POST'])
@require_write
def api_delete_reservation(res_id):
    res = db.session.get(RackReservation, res_id)
    if res:
        check_site(db.session.get(Rack, res.rack_id).site_id)
        db.session.delete(res)
        db.session.commit()
    return jsonify({'ok': True})
