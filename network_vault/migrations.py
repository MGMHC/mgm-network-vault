"""Bring an existing SQLite database up to date with the models, and seed a new one.

Runs inside create_app's app context on every start."""
from sqlalchemy import inspect, text

from . import portable
from .alert_render import DEFAULTS
from .models import AlertMessage, Backup, Device, Setting, Site, User, db, log_event
from .util import read_backup_file


def run(base_dir):
    db.create_all()
    _schema_and_data_fixes()
    _seed_admin()
    _log_restore(base_dir)


def _schema_and_data_fixes():
    """Tiny migration: bring an existing SQLite database up to date with the models.

    Schema steps (new columns, missing indexes) are idempotent and run on every start.
    One-off data fixes run once each and are recorded in the setting table, so later edits
    made in the UI are never overwritten by a restart."""
    insp = inspect(db.engine)
    with db.engine.begin() as conn:
        for table in db.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing:
                    ddl = col.type.compile(dialect=db.engine.dialect)
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}'))
        # Backfill: any user with NULL or old 'admin' role gets 'group-admin'
        conn.execute(text("UPDATE \"user\" SET role = 'group-admin' WHERE role IS NULL OR role = '' OR role = 'admin'"))

    # Create indexes declared on the models that an older database is missing (create_all only
    # creates indexes together with new tables). Unique indexes are skipped: existing duplicate
    # rows would make them fail.
    for table in db.metadata.sorted_tables:
        for idx in table.indexes:
            if idx.unique:
                continue
            try:
                idx.create(bind=db.engine, checkfirst=True)
            except Exception as e:  # noqa: BLE001 - never block start-up on an index
                log_event(f"Could not create index {idx.name}: {e}", "system", "WARN", user="system")

    def run_once(key, fn):
        flag = f"migration:{key}"
        if db.session.get(Setting, flag):
            return
        fn()
        db.session.add(Setting(key=flag, value="done"))
        db.session.commit()

    def backfill_last_login():
        with db.engine.begin() as conn:
            conn.execute(text("""
                UPDATE "user"
                SET last_login_at = (
                    SELECT max(ts)
                    FROM log_entry
                    WHERE log_entry.category = 'auth'
                      AND log_entry.message = 'User logged in'
                      AND log_entry.user = "user".username
                )
                WHERE last_login_at IS NULL
            """))

    def seed_default_sites():
        default_sites = [
            ("MGMHC", "MGM Healthcare (Main)", "Primary healthcare center"),
            ("MGMCI", "MGM Cancer Institute", "Cancer care specialty center"),
            ("MGM-Malar", "MGM Malar Hospital", "Malar multispeciality center"),
            ("MGM-Sevenhills", "MGM Sevenhills Hospital", "Sevenhills tertiary care center"),
        ]
        for code, name, desc in default_sites:
            if not Site.query.filter_by(code=code).first():
                db.session.add(Site(code=code, name=name, description=desc))
        db.session.commit()

    def floor_to_level():
        # 'Floor-X' or 'Floor X' -> 'Level-X'
        with db.engine.begin() as conn:
            for i in range(1, 30):
                conn.execute(text(f"UPDATE device SET \"group\" = 'Level-{i}' WHERE \"group\" = 'Floor-{i}' OR \"group\" = 'Floor {i}'"))
                conn.execute(text(f"UPDATE schedule SET target_value = 'Level-{i}' WHERE target = 'group' AND (target_value = 'Floor-{i}' OR target_value = 'Floor {i}')"))

    def malar_fixups():
        # Move Malar devices and credentials to the MGM-Malar site and set the Cisco IOS platform
        with db.engine.begin() as conn:
            conn.execute(text("UPDATE credential SET site_id = (SELECT id FROM site WHERE code = 'MGM-Malar') WHERE name = 'Mgm-Malar-SW'"))
            conn.execute(text("UPDATE device SET site_id = (SELECT id FROM site WHERE code = 'MGM-Malar') WHERE name LIKE '%MALAR%'"))
            conn.execute(text("UPDATE device SET platform = 'cisco_ios', model = 'C1000-48T-4X-L' WHERE name = 'MGM-MALAR-B-1ST-FLOOR'"))
            conn.execute(text("UPDATE device SET platform = 'cisco_ios', model = 'Catalyst 2960-S' WHERE name = 'MGM-MALAR-B-5TH-IT'"))
            conn.execute(text("UPDATE device SET platform = 'cisco_ios', model = 'C1000-48T-4X-L' WHERE name = 'MGM-MALAR-B-4TH-FLOOR'"))
            conn.execute(text("UPDATE device SET platform = 'cisco_ios', model = 'C1000-48T-4X-L' WHERE name = 'MGM-MALAR-B-5TH-FLOOR'"))

    run_once("backfill_last_login", backfill_last_login)
    run_once("seed_default_sites", seed_default_sites)

    # Every start: anything created without a site goes to the primary site (MGMHC) if it exists
    primary_site = Site.query.filter_by(code="MGMHC").first()
    if primary_site:
        with db.engine.begin() as conn:
            for tbl in ("device", "credential", "schedule"):
                conn.execute(text(f"UPDATE {tbl} SET site_id = :sid WHERE site_id IS NULL"), {"sid": primary_site.id})

    def seed_alert_messages():
        # One "Network team" message per alert trigger, worded like the defaults (Alerts page)
        for trig, d in DEFAULTS.items():
            if not AlertMessage.query.filter_by(trigger=trig).first():
                db.session.add(AlertMessage(trigger=trig, name="Network team", subject=d["subject"], intro=d["intro"],
                                            outro=d["outro"], recovery_subject=d.get("recovery_subject", ""),
                                            recovery_intro=d.get("recovery_intro", "")))
        db.session.commit()

    run_once("seed_alert_messages", seed_alert_messages)

    def backfill_hostnames():
        # Real switch hostnames (what LLDP neighbours report) from each switch's latest backup info file
        from .engine import parse_hostname  # engine pulls in netmiko; only this one-off needs it
        for d in Device.query.filter(Device.hostname.is_(None)).all():
            b = (Backup.query.filter(Backup.device_id == d.id, Backup.info_path.isnot(None))
                 .order_by(Backup.created_at.desc()).first())
            try:
                h = parse_hostname(read_backup_file(b.info_path)) if b else None
            except Exception:  # noqa: BLE001 - a missing or unreadable file just leaves it empty
                h = None
            if h:
                d.hostname = h[:128]
        db.session.commit()

    run_once("backfill_hostnames", backfill_hostnames)
    run_once("floor_to_level", floor_to_level)
    run_once("malar_fixups", malar_fixups)


def _seed_admin():
    if User.query.first():
        return
    u = User(username="admin", must_change=True, role="group-admin")
    u.set_password("admin")
    db.session.add(u)
    db.session.commit()
    log_event("Created default user 'admin' (password 'admin' - change on first login)", "auth", user="system")


def _log_restore(base_dir):
    m = portable.pop_restored(base_dir)
    if m:
        log_event(f"Restored from export of {m.get('source_host')} created {m.get('created')}: "
                  f"{m['counts'].get('device', 0)} devices, {m['counts'].get('backup', 0)} backups. "
                  f"Previous data kept in {m.get('previous_data_kept_in')}", "system", "WARN", user="system")
