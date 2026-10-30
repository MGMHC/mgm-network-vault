import json
import netaddr
from flask import request, jsonify, abort
from .models import db, Site, Device, VRF, VLANGroup, VLAN, Prefix, IPAddress, FHRPGroup, FHRPGroupAssignment

def register_ipam_views(bp, _current_user, _active_site_id, _site_filter, require_write):

    # -------------------------------------------------------------------------
    # API: Full IPAM Data summary
    # -------------------------------------------------------------------------
    @bp.route('/api/ipam/data')
    def api_ipam_data():
        u = _current_user()
        site_id = request.args.get('site_id')
        if not site_id or site_id == 'all':
            site_id = _active_site_id()

        def filter_site(q, model):
            if site_id and site_id != 'all':
                try:
                    sid = int(site_id)
                    return q.filter((model.site_id == sid) | (model.site_id.is_(None)))
                except (ValueError, TypeError):
                    pass
            return q

        vrfs = filter_site(VRF.query.order_by(VRF.name), VRF).all()
        vlan_groups = filter_site(VLANGroup.query.order_by(VLANGroup.name), VLANGroup).all()
        vlans = filter_site(VLAN.query.order_by(VLAN.vid), VLAN).all()
        prefixes = filter_site(Prefix.query.order_by(Prefix.prefix), Prefix).all()
        ips = filter_site(IPAddress.query.order_by(IPAddress.address), IPAddress).all()
        fhrps = filter_site(FHRPGroup.query.order_by(FHRPGroup.group_id), FHRPGroup).all()

        # Build subnet hierarchy trees
        prefix_dicts = []
        for p in prefixes:
            p_dict = p.to_dict()
            p_dict['children'] = []
            prefix_dicts.append(p_dict)

        def safe_net(addr_str):
            if not addr_str:
                return None
            try:
                return netaddr.IPNetwork(addr_str)
            except Exception:
                return None

        # Hierarchy nesting by immediate container
        hierarchy = []
        for i, p1 in enumerate(prefix_dicts):
            net1 = safe_net(p1['prefix'])
            parent = None
            best_len = -1
            if net1:
                for j, p2 in enumerate(prefix_dicts):
                    if i != j:
                        net2 = safe_net(p2['prefix'])
                        if net2 and net1 in net2 and net1 != net2:
                            if net2.prefixlen > best_len:
                                best_len = net2.prefixlen
                                parent = p2
            if parent:
                parent['children'].append(p1)
            else:
                hierarchy.append(p1)

        # KPI Metrics
        total_prefixes = len(prefixes)
        total_ips = len(ips)
        total_vlans = len(vlans)
        total_vrfs = len(vrfs)
        total_fhrps = len(fhrps)

        return jsonify({
            'ok': True,
            'kpis': {
                'prefixes': total_prefixes,
                'ips': total_ips,
                'vlans': total_vlans,
                'vrfs': total_vrfs,
                'fhrp_groups': total_fhrps
            },
            'vrfs': [v.to_dict() for v in vrfs],
            'vlan_groups': [g.to_dict() for g in vlan_groups],
            'vlans': [v.to_dict() for v in vlans],
            'prefixes': prefix_dicts,
            'hierarchy': hierarchy,
            'ip_addresses': [ip.to_dict() for ip in ips],
            'fhrp_groups': [f.to_dict() for f in fhrps]
        })

    # -------------------------------------------------------------------------
    # CRUD: VRFs
    # -------------------------------------------------------------------------
    @bp.route('/api/ipam/vrfs', methods=['POST'])
    @require_write
    def api_ipam_save_vrf():
        data = request.json or {}
        vrf_id = data.get('id')
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'error': 'VRF name is required'}), 400

        rd = (data.get('rd') or '').strip()
        desc = (data.get('description') or '').strip()
        site_id = data.get('site_id')
        site_id = int(site_id) if site_id and site_id != 'all' else None

        if vrf_id:
            v = db.session.get(VRF, vrf_id)
            if not v:
                return jsonify({'error': 'VRF not found'}), 404
        else:
            v = VRF()
            db.session.add(v)

        v.name = name
        v.rd = rd
        v.description = desc
        v.site_id = site_id
        db.session.commit()
        return jsonify({'ok': True, 'vrf': v.to_dict()})

    @bp.route('/api/ipam/vrfs/<int:vid>/delete', methods=['POST'])
    @require_write
    def api_ipam_delete_vrf(vid):
        v = db.session.get(VRF, vid)
        if v:
            db.session.delete(v)
            db.session.commit()
        return jsonify({'ok': True})

    # -------------------------------------------------------------------------
    # CRUD: VLAN Groups
    # -------------------------------------------------------------------------
    @bp.route('/api/ipam/vlan-groups', methods=['POST'])
    @require_write
    def api_ipam_save_vlan_group():
        data = request.json or {}
        gid = data.get('id')
        name = (data.get('name') or '').strip()
        slug = (data.get('slug') or '').strip() or name.lower().replace(' ', '-')
        desc = (data.get('description') or '').strip()
        min_vid = int(data.get('min_vid') or 1)
        max_vid = int(data.get('max_vid') or 4094)
        site_id = data.get('site_id')
        site_id = int(site_id) if site_id and site_id != 'all' else None

        if gid:
            g = db.session.get(VLANGroup, gid)
            if not g:
                return jsonify({'error': 'Group not found'}), 404
        else:
            g = VLANGroup()
            db.session.add(g)

        g.name = name
        g.slug = slug
        g.description = desc
        g.min_vid = min_vid
        g.max_vid = max_vid
        g.site_id = site_id
        db.session.commit()
        return jsonify({'ok': True, 'group': g.to_dict()})

    @bp.route('/api/ipam/vlan-groups/<int:gid>/delete', methods=['POST'])
    @require_write
    def api_ipam_delete_vlan_group(gid):
        g = db.session.get(VLANGroup, gid)
        if g:
            db.session.delete(g)
            db.session.commit()
        return jsonify({'ok': True})

    # -------------------------------------------------------------------------
    # CRUD: VLANs (with QinQ S-VLAN / C-VLAN)
    # -------------------------------------------------------------------------
    @bp.route('/api/ipam/vlans', methods=['POST'])
    @require_write
    def api_ipam_save_vlan():
        data = request.json or {}
        vlan_id = data.get('id')
        vid = int(data.get('vid') or 0)
        name = (data.get('name') or '').strip()
        if vid < 1 or vid > 4094 or not name:
            return jsonify({'error': 'Valid VLAN ID (1-4094) and Name are required'}), 400

        status = data.get('status') or 'active'
        role = (data.get('role') or '').strip()
        desc = (data.get('description') or '').strip()
        group_id = data.get('group_id')
        group_id = int(group_id) if group_id else None
        site_id = data.get('site_id')
        site_id = int(site_id) if site_id and site_id != 'all' else None

        # QinQ parameters
        qinq_role = data.get('qinq_role') or None
        qinq_svlan_id = data.get('qinq_svlan_id')
        qinq_svlan_id = int(qinq_svlan_id) if qinq_svlan_id else None

        if vlan_id:
            v = db.session.get(VLAN, vlan_id)
            if not v:
                return jsonify({'error': 'VLAN not found'}), 404
        else:
            v = VLAN()
            db.session.add(v)

        v.vid = vid
        v.name = name
        v.status = status
        v.role = role
        v.description = desc
        v.group_id = group_id
        v.site_id = site_id
        v.qinq_role = qinq_role
        v.qinq_svlan_id = qinq_svlan_id
        db.session.commit()
        return jsonify({'ok': True, 'vlan': v.to_dict()})

    @bp.route('/api/ipam/vlans/<int:vid>/delete', methods=['POST'])
    @require_write
    def api_ipam_delete_vlan(vid):
        v = db.session.get(VLAN, vid)
        if v:
            db.session.delete(v)
            db.session.commit()
        return jsonify({'ok': True})

    # -------------------------------------------------------------------------
    # CRUD: Prefixes (IPv4 / IPv6 Subnet Hierarchies)
    # -------------------------------------------------------------------------
    @bp.route('/api/ipam/prefixes', methods=['POST'])
    @require_write
    def api_ipam_save_prefix():
        data = request.json or {}
        prefix_id = data.get('id')
        prefix_str = (data.get('prefix') or '').strip()
        if not prefix_str:
            return jsonify({'error': 'Subnet Prefix string is required (e.g. 10.10.0.0/24)'}), 400

        try:
            net = netaddr.IPNetwork(prefix_str)
            clean_prefix = str(net.cidr)
            family = net.version
        except Exception as e:
            return jsonify({'error': f'Invalid CIDR Prefix format: {e}'}), 400

        status = data.get('status') or 'active'
        role = (data.get('role') or '').strip()
        is_pool = bool(data.get('is_pool'))
        desc = (data.get('description') or '').strip()
        
        site_id = data.get('site_id')
        site_id = int(site_id) if site_id and site_id != 'all' else None
        
        vrf_id = data.get('vrf_id')
        vrf_id = int(vrf_id) if vrf_id else None

        vlan_id = data.get('vlan_id')
        vlan_id = int(vlan_id) if vlan_id else None

        if prefix_id:
            p = db.session.get(Prefix, prefix_id)
            if not p:
                return jsonify({'error': 'Prefix not found'}), 404
        else:
            p = Prefix()
            db.session.add(p)

        p.prefix = clean_prefix
        p.family = family
        p.status = status
        p.role = role
        p.is_pool = is_pool
        p.description = desc
        p.site_id = site_id
        p.vrf_id = vrf_id
        p.vlan_id = vlan_id
        db.session.commit()
        return jsonify({'ok': True, 'prefix': p.to_dict()})

    @bp.route('/api/ipam/prefixes/<int:pid>/delete', methods=['POST'])
    @require_write
    def api_ipam_delete_prefix(pid):
        p = db.session.get(Prefix, pid)
        if p:
            db.session.delete(p)
            db.session.commit()
        return jsonify({'ok': True})

    # -------------------------------------------------------------------------
    # CRUD: IP Addresses
    # -------------------------------------------------------------------------
    @bp.route('/api/ipam/ip-addresses', methods=['POST'])
    @require_write
    def api_ipam_save_ip():
        data = request.json or {}
        ip_id = data.get('id')
        addr_str = (data.get('address') or '').strip()
        if not addr_str:
            return jsonify({'error': 'IP Address is required'}), 400

        try:
            ip_obj = netaddr.IPNetwork(addr_str) if '/' in addr_str else netaddr.IPAddress(addr_str)
            family = ip_obj.version
            clean_addr = str(ip_obj)
        except Exception as e:
            return jsonify({'error': f'Invalid IP address format: {e}'}), 400

        status = data.get('status') or 'active'
        role = (data.get('role') or '').strip()
        dns_name = (data.get('dns_name') or '').strip()
        desc = (data.get('description') or '').strip()
        
        site_id = data.get('site_id')
        site_id = int(site_id) if site_id and site_id != 'all' else None
        
        vrf_id = data.get('vrf_id')
        vrf_id = int(vrf_id) if vrf_id else None

        device_id = data.get('device_id')
        device_id = int(device_id) if device_id else None

        interface_name = (data.get('interface_name') or '').strip()

        fhrp_group_id = data.get('fhrp_group_id')
        fhrp_group_id = int(fhrp_group_id) if fhrp_group_id else None

        # Automatically determine parent prefix if not explicitly provided
        prefix_id = data.get('prefix_id')
        prefix_id = int(prefix_id) if prefix_id else None
        if not prefix_id:
            # find enclosing prefix in same site/VRF
            raw_ip = netaddr.IPAddress(clean_addr.split('/')[0])
            cand = Prefix.query.filter_by(site_id=site_id, vrf_id=vrf_id, family=family).all()
            for cp in cand:
                net = cp.network
                if net and raw_ip in net:
                    prefix_id = cp.id
                    break

        if ip_id:
            ip = db.session.get(IPAddress, ip_id)
            if not ip:
                return jsonify({'error': 'IP Address not found'}), 404
        else:
            ip = IPAddress()
            db.session.add(ip)

        ip.address = clean_addr
        ip.family = family
        ip.status = status
        ip.role = role
        ip.dns_name = dns_name
        ip.description = desc
        ip.site_id = site_id
        ip.vrf_id = vrf_id
        ip.prefix_id = prefix_id
        ip.device_id = device_id
        ip.interface_name = interface_name
        ip.fhrp_group_id = fhrp_group_id
        db.session.commit()
        return jsonify({'ok': True, 'ip_address': ip.to_dict()})

    @bp.route('/api/ipam/ip-addresses/<int:ipid>/delete', methods=['POST'])
    @require_write
    def api_ipam_delete_ip(ipid):
        ip = db.session.get(IPAddress, ipid)
        if ip:
            db.session.delete(ip)
            db.session.commit()
        return jsonify({'ok': True})

    # -------------------------------------------------------------------------
    # CRUD: FHRP Groups & Member Assignments
    # -------------------------------------------------------------------------
    @bp.route('/api/ipam/fhrp-groups', methods=['POST'])
    @require_write
    def api_ipam_save_fhrp():
        data = request.json or {}
        fid = data.get('id')
        protocol = (data.get('protocol') or 'vrrp').lower()
        group_id = int(data.get('group_id') or 1)
        auth_type = data.get('auth_type') or 'none'
        auth_key = data.get('auth_key') or ''
        virtual_ip = (data.get('virtual_ip') or '').strip()
        desc = (data.get('description') or '').strip()
        
        site_id = data.get('site_id')
        site_id = int(site_id) if site_id and site_id != 'all' else None

        if fid:
            f = db.session.get(FHRPGroup, fid)
            if not f:
                return jsonify({'error': 'FHRP Group not found'}), 404
        else:
            f = FHRPGroup()
            db.session.add(f)

        f.protocol = protocol
        f.group_id = group_id
        f.auth_type = auth_type
        f.auth_key = auth_key
        f.virtual_ip = virtual_ip
        f.description = desc
        f.site_id = site_id
        db.session.commit()

        # Handle member devices if provided
        members = data.get('members')
        if isinstance(members, list):
            # delete existing assignments and replace
            FHRPGroupAssignment.query.filter_by(group_id=f.id).delete()
            for m in members:
                dev_id = m.get('device_id')
                if dev_id:
                    assign = FHRPGroupAssignment(
                        group_id=f.id,
                        device_id=int(dev_id),
                        interface_name=(m.get('interface_name') or '').strip(),
                        priority=int(m.get('priority') or 100)
                    )
                    db.session.add(assign)
            db.session.commit()

        return jsonify({'ok': True, 'fhrp_group': f.to_dict()})

    @bp.route('/api/ipam/fhrp-groups/<int:fid>/delete', methods=['POST'])
    @require_write
    def api_ipam_delete_fhrp(fid):
        f = db.session.get(FHRPGroup, fid)
        if f:
            db.session.delete(f)
            db.session.commit()
        return jsonify({'ok': True})
