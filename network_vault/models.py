from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash
import netaddr

db = SQLAlchemy()


def now():
    return datetime.now().replace(microsecond=0)


PLATFORMS = {
    "juniper_junos": "Juniper Junos (EX2200 / EX2300 / EX3300)",
    "cisco_c1300": "Cisco Catalyst 1300 / CBS / SG",
    "cisco_ios": "Cisco IOS / IOS-XE",
    "sonicwall_sonicos": "SonicWall Firewall (SonicOS / NSa)",
}

MODELS = ["Auto", "EX2200", "EX2300", "EX3300", "C1300", "NSa", "Other"]  # Auto = detect from device
ROLES = ["access", "distribution", "core", "firewall"]

USER_ROLES = ["group-admin", "site-admin", "read-write", "read-only"]


user_sites = db.Table(
    "user_sites",
    db.Column("user_id", db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
    db.Column("site_id", db.Integer, db.ForeignKey("site.id", ondelete="CASCADE"), primary_key=True),
)


class Site(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False)   # e.g. MGMHC, MGMCI, MGM-Malar, MGM-Sevenhills
    name = db.Column(db.String(128), nullable=False)               # e.g. MGM Healthcare (Main)
    description = db.Column(db.String(256), default="")
    created_at = db.Column(db.DateTime, default=now)

    devices = db.relationship("Device", backref="site", lazy="dynamic", cascade="all, delete-orphan")
    credentials = db.relationship("Credential", backref="site", lazy="dynamic", cascade="all, delete-orphan")
    schedules = db.relationship("Schedule", backref="site", lazy="dynamic", cascade="all, delete-orphan")
    users = db.relationship("User", backref="site", lazy=True)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(128), nullable=True)
    password_hash = db.Column(db.String(256), nullable=False)
    must_change = db.Column(db.Boolean, default=False)
    role = db.Column(db.String(24), default="group-admin")  # group-admin | site-admin | read-write | read-only
    site_id = db.Column(db.Integer, db.ForeignKey("site.id"), nullable=True)  # primary / fallback site
    last_login_at = db.Column(db.DateTime, nullable=True)

    assigned_sites = db.relationship(
        "Site",
        secondary=user_sites,
        lazy="subquery",
        backref=db.backref("tagged_users", lazy=True),
    )

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

    @property
    def is_group_admin(self):
        return self.role == "group-admin"

    @property
    def is_site_admin(self):
        return self.role in ("group-admin", "site-admin")

    @property
    def can_write(self):
        return self.role in ("group-admin", "site-admin", "read-write")

    @property
    def allowed_site_ids(self):
        if self.is_group_admin:
            return None  # all sites allowed
        ids = {s.id for s in self.assigned_sites}
        if self.site_id:
            ids.add(self.site_id)
        return ids

    def has_site_access(self, site_id):
        if self.is_group_admin:
            return True
        if not site_id:
            return True
        allowed = self.allowed_site_ids
        return (site_id in allowed) if allowed is not None else True


class Credential(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey("site.id"), nullable=True, index=True)
    name = db.Column(db.String(64), nullable=False)
    username = db.Column(db.String(64), nullable=False)
    password_enc = db.Column(db.Text, nullable=False)
    secret_enc = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=now)

    devices = db.relationship("Device", backref="credential", lazy="dynamic")


class Device(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey("site.id"), nullable=True, index=True)
    name = db.Column(db.String(64), nullable=False)
    host = db.Column(db.String(128), nullable=False)
    port = db.Column(db.Integer, default=22)
    platform = db.Column(db.String(32), nullable=False, default="juniper_junos")
    model = db.Column(db.String(32), default="Auto")
    role = db.Column(db.String(16), default="access")
    group = db.Column(db.String(64), default="")
    location = db.Column(db.String(128), default="")
    is_vc = db.Column(db.Boolean, default=False)
    legacy_ssh = db.Column(db.Boolean, default=False)
    enabled = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text, default="")
    credential_id = db.Column(db.Integer, db.ForeignKey("credential.id"))

    # reachability
    status = db.Column(db.String(16), default="unknown")  # up | down | ssh-down | unknown
    ping_ms = db.Column(db.Float)
    last_checked = db.Column(db.DateTime)
    status_since = db.Column(db.DateTime)

    # facts gathered during backup
    sw_version = db.Column(db.String(128))
    hw_model = db.Column(db.String(128))
    serial = db.Column(db.String(64))
    vc_members_json = db.Column(db.Text)

    last_backup_at = db.Column(db.DateTime)
    last_backup_status = db.Column(db.String(16))  # success | error | changed
    last_backup_error = db.Column(db.Text)

    backups = db.relationship("Backup", backref="device", cascade="all, delete-orphan", lazy="dynamic")

    # DCIM Rack elevation fields
    rack_id = db.Column(db.Integer, db.ForeignKey("dcim_rack.id", ondelete="SET NULL"), nullable=True, index=True)
    rack_position = db.Column(db.Integer, nullable=True)  # Top RU unit position
    rack_face = db.Column(db.String(10), default="front")  # front | rear | both
    rack_height = db.Column(db.Integer, default=1)  # Height in RUs (1, 2, 3, 4...)

    @property
    def platform_label(self):
        return PLATFORMS.get(self.platform, self.platform)

    @property
    def vc_members(self):
        if not self.vc_members_json:
            return []
        try:
            return json.loads(self.vc_members_json)
        except Exception:
            return []

    @property
    def vc_degraded(self):
        if not getattr(self, 'is_vc', False) or not self.vc_members:
            return False
        return any(m.get('status', '').lower() not in ('prsnt', 'present', 'master', 'backup') for m in self.vc_members)


class Backup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("device.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=now, index=True)
    status = db.Column(db.String(16), default="success")  # success | changed | error
    config_hash = db.Column(db.String(64))
    lines_added = db.Column(db.Integer, default=0)
    lines_deleted = db.Column(db.Integer, default=0)
    lines_removed = db.Column(db.Integer, default=0)
    error_message = db.Column(db.Text)
    trigger = db.Column(db.String(32), default="manual")  # manual | schedule | webhook | git-sync
    pinned = db.Column(db.Boolean, default=False)
    commit_sha = db.Column(db.String(40), index=True)
    commit_author = db.Column(db.String(128))
    commit_message = db.Column(db.Text)
    changed = db.Column(db.Boolean, default=False)
    path = db.Column(db.String(256))
    set_path = db.Column(db.String(256))
    info_path = db.Column(db.String(256))
    size = db.Column(db.Integer, default=0)
    sha256 = db.Column(db.String(64))
    last_commit = db.Column(db.String(64))
    git_commit = db.Column(db.String(64))


class Schedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey("site.id"), nullable=True, index=True)
    name = db.Column(db.String(64), nullable=False)
    frequency = db.Column(db.String(32), default="daily")
    time = db.Column(db.String(16), default="02:00")
    day_of_week = db.Column(db.String(64), default="sun")
    day_of_month = db.Column(db.Integer, default=1)
    cron = db.Column(db.String(64), default="")
    target = db.Column(db.String(16), default="all")  # all | group | role | platform | list
    target_value = db.Column(db.String(128), default="")
    enabled = db.Column(db.Boolean, default=True)
    last_run = db.Column(db.DateTime)
    last_result = db.Column(db.String(32))
    next_run = db.Column(db.DateTime)


class Job(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(32), nullable=False)  # backup_batch | reachability | detect
    status = db.Column(db.String(16), default="running")  # running | success | partial | error
    created_at = db.Column(db.DateTime, default=now)
    finished_at = db.Column(db.DateTime)
    total = db.Column(db.Integer, default=0)
    completed = db.Column(db.Integer, default=0)
    errors = db.Column(db.Integer, default=0)
    details_json = db.Column(db.Text)

    @property
    def details(self):
        try:
            return json.loads(self.details_json or "{}")
        except Exception:
            return {}

    def set_details(self, d):
        self.details_json = json.dumps(d)


class LogEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ts = db.Column(db.DateTime, default=now, index=True)
    level = db.Column(db.String(10), default="INFO")  # INFO | WARN | ERROR
    category = db.Column(db.String(32), default="general")
    device_id = db.Column(db.Integer, db.ForeignKey("device.id", ondelete="SET NULL"), nullable=True)
    device_name = db.Column(db.String(128), nullable=True)
    user = db.Column(db.String(64), default="")
    message = db.Column(db.Text, nullable=False)
    details = db.Column(db.Text, nullable=True)

    device = db.relationship("Device", backref="logs")


class Setting(db.Model):
    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.String(512))


DEFAULT_SETTINGS = {
    "retention_count": "60",        # keep at most N backups per device (0 = unlimited)
    "retention_days": "365",        # delete backups older than N days (0 = never)
    "skip_unchanged": "0",          # 1 = don't store a new file when config is identical
    "ping_interval_min": "5",       # reachability poll interval (0 = disabled)
    "log_retention_days": "180",
    "max_workers": "8",             # parallel SSH sessions
    "ssh_timeout": "30",
    "read_timeout": "180",
    # SMTP / Password Reset
    "smtp_enabled": "1",
    "smtp_host": "smtp.office365.com",
    "smtp_port": "587",
    "smtp_encryption": "starttls",   # starttls | ssl | plain
    "smtp_user": "donotreply@mgmhealthcare.in",
    "smtp_pass_enc": "",
    "smtp_from_name": "MGM Networkvault",
    "smtp_from": "donotreply@mgmhealthcare.in",
    "app_url": "",                  # external base URL (e.g. https://networkvault.mgmhealthcare.in)
}


def get_setting(key):
    s = db.session.get(Setting, key)
    return s.value if s else DEFAULT_SETTINGS.get(key)


def get_int(key):
    try:
        return int(get_setting(key))
    except (TypeError, ValueError):
        return int(DEFAULT_SETTINGS[key])


def set_setting(key, value):
    s = db.session.get(Setting, key)
    if s:
        s.value = str(value)
    else:
        db.session.add(Setting(key=key, value=str(value)))


# =============================================================================
# IPAM Models: VRFs, VLAN Groups, VLANs (with QinQ), Prefixes, IP Addresses, FHRP Groups
# =============================================================================

class VRF(db.Model):
    __tablename__ = 'ipam_vrf'
    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('site.id', ondelete='CASCADE'), nullable=True)
    name = db.Column(db.String(64), nullable=False)
    rd = db.Column(db.String(32), nullable=True)  # Route Distinguisher e.g. 65000:100
    description = db.Column(db.String(256), default='')
    enforce_unique = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=now)

    site = db.relationship('Site', backref=db.backref('vrfs', lazy='dynamic', cascade='all, delete-orphan'))
    prefixes = db.relationship('Prefix', backref='vrf', lazy='dynamic')
    ip_addresses = db.relationship('IPAddress', backref='vrf', lazy='dynamic')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'rd': self.rd or '',
            'description': self.description or '',
            'site_id': self.site_id,
            'site_name': self.site.name if self.site else 'Global (All Sites)',
            'site_code': self.site.code if self.site else 'GLOBAL',
            'prefix_count': self.prefixes.count(),
            'ip_count': self.ip_addresses.count(),
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else ''
        }


class VLANGroup(db.Model):
    __tablename__ = 'ipam_vlan_group'
    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('site.id', ondelete='CASCADE'), nullable=True)
    name = db.Column(db.String(64), nullable=False)
    slug = db.Column(db.String(64), nullable=False)
    description = db.Column(db.String(256), default='')
    min_vid = db.Column(db.Integer, default=1)
    max_vid = db.Column(db.Integer, default=4094)
    created_at = db.Column(db.DateTime, default=now)

    site = db.relationship('Site', backref=db.backref('vlan_groups', lazy='dynamic', cascade='all, delete-orphan'))
    vlans = db.relationship('VLAN', backref='group', lazy='dynamic')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'description': self.description or '',
            'min_vid': self.min_vid,
            'max_vid': self.max_vid,
            'site_id': self.site_id,
            'site_name': self.site.name if self.site else 'Global (All Sites)',
            'site_code': self.site.code if self.site else 'GLOBAL',
            'vlan_count': self.vlans.count()
        }


class VLAN(db.Model):
    __tablename__ = 'ipam_vlan'
    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('site.id', ondelete='CASCADE'), nullable=True)
    group_id = db.Column(db.Integer, db.ForeignKey('ipam_vlan_group.id', ondelete='SET NULL'), nullable=True)
    vid = db.Column(db.Integer, nullable=False)  # 1-4094
    name = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(24), default='active')  # active | reserved | deprecated
    role = db.Column(db.String(32), default='')  # server, voice, mgmt, dmz, access
    description = db.Column(db.String(256), default='')
    
    # QinQ Support
    qinq_role = db.Column(db.String(16), nullable=True)  # 'svlan' (Service Provider Outer) | 'cvlan' (Customer Inner) | None
    qinq_svlan_id = db.Column(db.Integer, db.ForeignKey('ipam_vlan.id', ondelete='SET NULL'), nullable=True)
    
    created_at = db.Column(db.DateTime, default=now)

    site = db.relationship('Site', backref=db.backref('vlans', lazy='dynamic', cascade='all, delete-orphan'))
    customer_vlans = db.relationship('VLAN', backref=db.backref('svlan', remote_side=[id]), lazy='dynamic')
    prefixes = db.relationship('Prefix', backref='vlan', lazy='dynamic')

    def to_dict(self):
        return {
            'id': self.id,
            'vid': self.vid,
            'name': self.name,
            'status': self.status,
            'role': self.role or '',
            'description': self.description or '',
            'group_id': self.group_id,
            'group_name': self.group.name if self.group else 'None',
            'site_id': self.site_id,
            'site_name': self.site.name if self.site else 'Global',
            'site_code': self.site.code if self.site else 'GLOBAL',
            'qinq_role': self.qinq_role or '',
            'qinq_svlan_id': self.qinq_svlan_id,
            'qinq_svlan_name': f'VLAN {self.svlan.vid} ({self.svlan.name})' if self.svlan else '',
            'prefix_count': self.prefixes.count()
        }


class Prefix(db.Model):
    __tablename__ = 'ipam_prefix'
    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('site.id', ondelete='CASCADE'), nullable=True)
    vrf_id = db.Column(db.Integer, db.ForeignKey('ipam_vrf.id', ondelete='SET NULL'), nullable=True)
    vlan_id = db.Column(db.Integer, db.ForeignKey('ipam_vlan.id', ondelete='SET NULL'), nullable=True)
    prefix = db.Column(db.String(50), nullable=False)  # e.g. 10.10.0.0/16 or 2001:db8::/32
    family = db.Column(db.Integer, default=4)  # 4 or 6
    status = db.Column(db.String(24), default='active')  # active | reserved | deprecated | container
    role = db.Column(db.String(32), default='')  # Management, Loopback, Access, Point-to-Point, VIP
    is_pool = db.Column(db.Boolean, default=False)
    description = db.Column(db.String(256), default='')
    created_at = db.Column(db.DateTime, default=now)

    site = db.relationship('Site', backref=db.backref('prefixes', lazy='dynamic', cascade='all, delete-orphan'))
    ip_addresses = db.relationship('IPAddress', backref='prefix_rel', lazy='dynamic')

    @property
    def network(self):
        try:
            return netaddr.IPNetwork(self.prefix)
        except Exception:
            return None

    def calculate_utilization(self):
        net = self.network
        if not net:
            return 0, 0, 0
        total_usable = net.size if net.version == 6 or net.prefixlen >= 31 else max(1, net.size - 2)
        ips_count = self.ip_addresses.count()
        pct = round((ips_count / total_usable) * 100, 1) if total_usable > 0 else 0
        return total_usable, ips_count, min(100.0, pct)

    def to_dict(self):
        total, used, pct = self.calculate_utilization()
        net = self.network
        return {
            'id': self.id,
            'prefix': self.prefix,
            'family': self.family,
            'status': self.status,
            'role': self.role or '',
            'is_pool': self.is_pool,
            'description': self.description or '',
            'site_id': self.site_id,
            'site_name': self.site.name if self.site else 'Global',
            'site_code': self.site.code if self.site else 'GLOBAL',
            'vrf_id': self.vrf_id,
            'vrf_name': self.vrf.name if self.vrf else 'Global',
            'vlan_id': self.vlan_id,
            'vlan_name': f'VLAN {self.vlan.vid} - {self.vlan.name}' if self.vlan else '',
            'vlan_vid': self.vlan.vid if self.vlan else None,
            'total_addresses': total,
            'allocated_addresses': used,
            'utilization_pct': pct,
            'broadcast': str(net.broadcast) if net and net.version == 4 else '',
            'netmask': str(net.netmask) if net else '',
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else ''
        }


class IPAddress(db.Model):
    __tablename__ = 'ipam_ip_address'
    id = db.Column(db.Integer, primary_key=True)
    prefix_id = db.Column(db.Integer, db.ForeignKey('ipam_prefix.id', ondelete='SET NULL'), nullable=True)
    vrf_id = db.Column(db.Integer, db.ForeignKey('ipam_vrf.id', ondelete='SET NULL'), nullable=True)
    site_id = db.Column(db.Integer, db.ForeignKey('site.id', ondelete='CASCADE'), nullable=True)
    address = db.Column(db.String(50), nullable=False)  # e.g. 10.10.1.5/24 or 10.10.1.5
    family = db.Column(db.Integer, default=4)
    status = db.Column(db.String(24), default='active')  # active | reserved | deprecated | dhcp | slaac
    role = db.Column(db.String(32), default='')  # loopback | secondary | anycast | vip | vrrp | hsrp
    dns_name = db.Column(db.String(256), default='')
    description = db.Column(db.String(256), default='')
    
    device_id = db.Column(db.Integer, db.ForeignKey('device.id', ondelete='SET NULL'), nullable=True)
    interface_name = db.Column(db.String(64), default='')
    
    fhrp_group_id = db.Column(db.Integer, db.ForeignKey('ipam_fhrp_group.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(db.DateTime, default=now)

    site = db.relationship('Site', backref=db.backref('ip_addresses', lazy='dynamic', cascade='all, delete-orphan'))
    device = db.relationship('Device', backref=db.backref('assigned_ips', lazy='dynamic'))

    def to_dict(self):
        return {
            'id': self.id,
            'address': self.address,
            'family': self.family,
            'status': self.status,
            'role': self.role or '',
            'dns_name': self.dns_name or '',
            'description': self.description or '',
            'prefix_id': self.prefix_id,
            'prefix_str': self.prefix_rel.prefix if self.prefix_rel else '',
            'vrf_id': self.vrf_id,
            'vrf_name': self.vrf.name if self.vrf else 'Global',
            'site_id': self.site_id,
            'site_name': self.site.name if self.site else 'Global',
            'site_code': self.site.code if self.site else 'GLOBAL',
            'device_id': self.device_id,
            'device_name': self.device.name if self.device else '',
            'interface_name': self.interface_name or '',
            'fhrp_group_id': self.fhrp_group_id,
            'fhrp_group_name': f'{self.fhrp_group.protocol.upper()} Group {self.fhrp_group.group_id}' if self.fhrp_group else '',
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else ''
        }


class FHRPGroup(db.Model):
    __tablename__ = 'ipam_fhrp_group'
    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('site.id', ondelete='CASCADE'), nullable=True)
    protocol = db.Column(db.String(16), default='vrrp')  # vrrp | hsrp | glbp | carp
    group_id = db.Column(db.Integer, nullable=False)  # Virtual router identifier (0-255)
    auth_type = db.Column(db.String(24), default='none')  # none | plaintext | md5
    auth_key = db.Column(db.String(64), nullable=True)
    virtual_ip = db.Column(db.String(50), nullable=True)  # VIP address
    description = db.Column(db.String(256), default='')
    created_at = db.Column(db.DateTime, default=now)

    site = db.relationship('Site', backref=db.backref('fhrp_groups', lazy='dynamic', cascade='all, delete-orphan'))
    vip_addresses = db.relationship('IPAddress', backref='fhrp_group', lazy='dynamic')
    members = db.relationship('FHRPGroupAssignment', backref='group', lazy='dynamic', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'protocol': self.protocol.upper(),
            'group_id': self.group_id,
            'auth_type': self.auth_type,
            'virtual_ip': self.virtual_ip or '',
            'description': self.description or '',
            'site_id': self.site_id,
            'site_name': self.site.name if self.site else 'Global',
            'site_code': self.site.code if self.site else 'GLOBAL',
            'member_count': self.members.count(),
            'members': [m.to_dict() for m in self.members.all()]
        }


class FHRPGroupAssignment(db.Model):
    __tablename__ = 'ipam_fhrp_assignment'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('ipam_fhrp_group.id', ondelete='CASCADE'), nullable=False)
    device_id = db.Column(db.Integer, db.ForeignKey('device.id', ondelete='CASCADE'), nullable=False)
    interface_name = db.Column(db.String(64), default='')  # e.g. vlan.10 or ge-0/0/0
    priority = db.Column(db.Integer, default=100)  # 1-255 (Higher is Master)
    created_at = db.Column(db.DateTime, default=now)

    device = db.relationship('Device')

    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'device_name': self.device.name if self.device else '',
            'interface_name': self.interface_name or '',
            'priority': self.priority
        }


# =============================================================================
# DCIM Rack Elevations & Physical Cabinets
# =============================================================================

class RackGroup(db.Model):
    __tablename__ = 'dcim_rack_group'
    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('site.id', ondelete='CASCADE'), nullable=True, index=True)
    name = db.Column(db.String(64), nullable=False)  # e.g. "Server Room 1", "MDF Data Center", "IDF Closet 2F"
    description = db.Column(db.String(256), default='')
    created_at = db.Column(db.DateTime, default=now)

    site = db.relationship('Site', backref=db.backref('rack_groups', lazy=True))
    racks = db.relationship('Rack', backref='group', lazy='dynamic', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description or '',
            'site_id': self.site_id,
            'site_name': self.site.name if self.site else 'Global',
            'rack_count': self.racks.count()
        }


class Rack(db.Model):
    __tablename__ = 'dcim_rack'
    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('site.id', ondelete='CASCADE'), nullable=True, index=True)
    group_id = db.Column(db.Integer, db.ForeignKey('dcim_rack_group.id', ondelete='SET NULL'), nullable=True, index=True)
    name = db.Column(db.String(64), nullable=False)  # e.g. "MDF-RACK-01", "IDF-1-CAB"
    facility_id = db.Column(db.String(64), default='')  # Row 1, Pos 3
    status = db.Column(db.String(24), default='active')  # active | planned | reserved | deprecated
    role = db.Column(db.String(32), default='network')  # network | server | storage | idf | core
    u_height = db.Column(db.Integer, default=42)  # Typically 42, 48, 24, 12
    desc_units = db.Column(db.Boolean, default=True)  # True = 42 at top down to 1 at bottom
    outer_width = db.Column(db.Integer, default=600)  # mm (19" standard frame)
    outer_depth = db.Column(db.Integer, default=1000)  # mm
    comments = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=now)

    site = db.relationship('Site', backref=db.backref('racks', lazy=True))
    devices = db.relationship('Device', backref='rack', lazy='dynamic', foreign_keys='Device.rack_id')
    reservations = db.relationship('RackReservation', backref='rack', lazy='dynamic', cascade='all, delete-orphan')

    def calculate_utilization(self):
        total = self.u_height or 42
        occupied = 0
        for d in self.devices.all():
            if d.rack_position:
                occupied += (d.rack_height or 1)
        reserved = 0
        for r in self.reservations.all():
            reserved += (r.height or 1)
        used = occupied + reserved
        pct = round((used / total) * 100, 1) if total > 0 else 0
        return {
            'total': total,
            'occupied': occupied,
            'reserved': reserved,
            'available': max(0, total - used),
            'pct': min(100.0, pct)
        }

    def get_elevation(self, face='front'):
        """Generate elevation unit by unit from top to bottom."""
        total = self.u_height or 42
        dev_map = {}
        for d in self.devices.all():
            if d.rack_position and (d.rack_face in ('both', face)):
                h = d.rack_height or 1
                pos = d.rack_position  # Top unit
                for u in range(pos - h + 1, pos + 1):
                    dev_map[u] = {
                        'device': {
                            'id': d.id,
                            'name': d.name,
                            'model': d.model or d.hw_model or d.platform,
                            'role': d.role,
                            'host': d.host,
                            'status': d.status,
                            'height': h,
                            'position': pos,
                            'face': d.rack_face or 'front',
                            'last_backup_status': d.last_backup_status
                        },
                        'is_primary': (u == pos),
                        'span': h
                    }

        res_map = {}
        for r in self.reservations.all():
            pos = r.unit
            h = r.height or 1
            for u in range(pos - h + 1, pos + 1):
                res_map[u] = {
                    'reservation': {
                        'id': r.id,
                        'description': r.description,
                        'user': r.user,
                        'height': h,
                        'unit': pos
                    },
                    'is_primary': (u == pos),
                    'span': h
                }

        unit_range = range(total, 0, -1) if self.desc_units else range(1, total + 1)
        units = []
        for u in unit_range:
            slot = {'unit': u, 'occupied': False, 'reserved': False}
            if u in dev_map:
                slot['occupied'] = True
                slot['device'] = dev_map[u]['device']
                slot['is_primary'] = dev_map[u]['is_primary']
                slot['span'] = dev_map[u]['span']
            elif u in res_map:
                slot['reserved'] = True
                slot['reservation'] = res_map[u]['reservation']
                slot['is_primary'] = res_map[u]['is_primary']
                slot['span'] = res_map[u]['span']
            units.append(slot)
        return units

    def to_dict(self, include_elevation=False, face='front'):
        util = self.calculate_utilization()
        data = {
            'id': self.id,
            'name': self.name,
            'facility_id': self.facility_id or '',
            'status': self.status or 'active',
            'role': self.role or 'network',
            'u_height': self.u_height or 42,
            'desc_units': self.desc_units,
            'outer_width': self.outer_width or 600,
            'outer_depth': self.outer_depth or 1000,
            'site_id': self.site_id,
            'site_name': self.site.name if self.site else 'Global',
            'site_code': self.site.code if self.site else 'GLOBAL',
            'group_id': self.group_id,
            'group_name': self.group.name if self.group else 'Unassigned',
            'device_count': self.devices.count(),
            'utilization': util,
            'comments': self.comments or ''
        }
        if include_elevation:
            data['elevation'] = self.get_elevation(face)
        return data


class RackReservation(db.Model):
    __tablename__ = 'dcim_rack_reservation'
    id = db.Column(db.Integer, primary_key=True)
    rack_id = db.Column(db.Integer, db.ForeignKey('dcim_rack.id', ondelete='CASCADE'), nullable=False, index=True)
    unit = db.Column(db.Integer, nullable=False)  # Starting RU
    height = db.Column(db.Integer, default=1)  # Number of RUs
    description = db.Column(db.String(256), nullable=False)  # e.g. "Reserved for Future Spine Switch"
    user = db.Column(db.String(64), default='')
    created_at = db.Column(db.DateTime, default=now)

    def to_dict(self):
        return {
            'id': self.id,
            'rack_id': self.rack_id,
            'unit': self.unit,
            'height': self.height,
            'description': self.description,
            'user': self.user or '',
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else ''
        }
