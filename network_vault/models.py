"""Database models."""
import json
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


def now():
    return datetime.now().replace(microsecond=0)


PLATFORMS = {
    "juniper_junos": "Juniper Junos (EX2200 / EX2300 / EX3300)",
    "cisco_c1300": "Cisco Catalyst 1300 / CBS / SG",
    "cisco_ios": "Cisco IOS / IOS-XE",
}

MODELS = ["Auto", "EX2200", "EX2300", "EX3300", "C1300", "Other"]  # Auto = detect from the switch
ROLES = ["access", "distribution", "core"]


USER_ROLES = ["group-admin", "site-admin", "read-write", "read-only"]


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
    password_hash = db.Column(db.String(256), nullable=False)
    must_change = db.Column(db.Boolean, default=False)
    role = db.Column(db.String(24), default="group-admin")  # group-admin | site-admin | read-write | read-only
    site_id = db.Column(db.Integer, db.ForeignKey("site.id"), nullable=True)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

    @property
    def is_group_admin(self):
        r = self.role or "group-admin"
        return r in ("group-admin", "admin")

    @property
    def is_site_admin(self):
        r = self.role or "group-admin"
        return r in ("group-admin", "admin", "site-admin")

    @property
    def is_admin(self):
        return self.is_site_admin

    @property
    def can_write(self):
        """Can add/edit/delete devices, credentials, schedules, settings."""
        r = self.role or "group-admin"
        return r in ("group-admin", "admin", "site-admin", "read-write")

    @property
    def can_backup(self):
        """Can trigger backups and reachability checks."""
        r = self.role or "group-admin"
        return r in ("group-admin", "admin", "site-admin", "read-write")

    def has_site_access(self, site_id):
        """Returns True if user has access to the specified site_id (or all sites if group-admin)."""
        if self.is_group_admin:
            return True
        return self.site_id == site_id


class Credential(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey("site.id"), nullable=True, index=True)
    name = db.Column(db.String(64), nullable=False)
    username = db.Column(db.String(64), nullable=False)
    password_enc = db.Column(db.Text, nullable=False)
    secret_enc = db.Column(db.Text)  # enable secret (Cisco)
    devices = db.relationship("Device", backref="credential", lazy=True)


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
    serial = db.Column(db.String(128))
    vc_members_json = db.Column(db.Text)
    last_backup_at = db.Column(db.DateTime)
    last_backup_status = db.Column(db.String(16))  # ok | failed
    last_backup_error = db.Column(db.Text)

    backups = db.relationship("Backup", backref="device", lazy="dynamic",
                              cascade="all, delete-orphan", order_by="Backup.created_at.desc()")

    @property
    def vc_members(self):
        try:
            return json.loads(self.vc_members_json or "[]")
        except ValueError:
            return []

    @property
    def vc_degraded(self):
        return self.is_vc and any(m.get("status", "").lower() != "prsnt" for m in self.vc_members)

    @property
    def model_label(self):
        return self.hw_model or ("not detected yet" if self.model == "Auto" else self.model)

    @property
    def is_juniper(self):
        return self.platform.startswith("juniper")


class Backup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("device.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=now, index=True)
    trigger = db.Column(db.String(32), default="manual")  # manual | schedule:<name>
    path = db.Column(db.String(512), nullable=False)      # main config (relative to backup dir)
    set_path = db.Column(db.String(512))                  # Junos "display set" format
    info_path = db.Column(db.String(512))                 # version / VC / commit history
    size = db.Column(db.Integer, default=0)
    sha256 = db.Column(db.String(64))
    changed = db.Column(db.Boolean, default=True)
    lines_added = db.Column(db.Integer, default=0)
    lines_removed = db.Column(db.Integer, default=0)
    last_commit = db.Column(db.String(256))               # e.g. "2026-10-04 11:22:33 IST by admin via cli"
    pinned = db.Column(db.Boolean, default=False)         # excluded from retention cleanup
    git_commit = db.Column(db.String(40))                 # commit in the GitHub mirror (if enabled)

    @property
    def previous(self):
        return (Backup.query.filter(Backup.device_id == self.device_id, Backup.created_at < self.created_at)
                .order_by(Backup.created_at.desc()).first())


class Schedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey("site.id"), nullable=True, index=True)
    name = db.Column(db.String(64), nullable=False)
    frequency = db.Column(db.String(16), default="daily")  # hourly | daily | weekly | monthly | cron
    time = db.Column(db.String(5), default="02:00")
    day_of_week = db.Column(db.String(32), default="sun")
    day_of_month = db.Column(db.Integer, default=1)
    cron = db.Column(db.String(64), default="")
    target = db.Column(db.String(16), default="all")       # all | group | role | devices
    target_value = db.Column(db.String(512), default="")
    enabled = db.Column(db.Boolean, default=True)
    last_run = db.Column(db.DateTime)
    last_result = db.Column(db.String(128))


class Job(db.Model):
    """One backup/check run over one or more devices (for progress tracking)."""
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(16), default="backup")
    trigger = db.Column(db.String(64), default="manual")
    started = db.Column(db.DateTime, default=now)
    finished = db.Column(db.DateTime)
    total = db.Column(db.Integer, default=0)
    done = db.Column(db.Integer, default=0)
    ok = db.Column(db.Integer, default=0)
    failed = db.Column(db.Integer, default=0)
    unchanged = db.Column(db.Integer, default=0)
    status = db.Column(db.String(16), default="running")


class LogEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ts = db.Column(db.DateTime, default=now, index=True)
    level = db.Column(db.String(8), default="INFO", index=True)
    category = db.Column(db.String(16), default="system", index=True)
    device_id = db.Column(db.Integer, index=True)
    device_name = db.Column(db.String(64))
    user = db.Column(db.String(64))
    message = db.Column(db.String(512))
    details = db.Column(db.Text)


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
