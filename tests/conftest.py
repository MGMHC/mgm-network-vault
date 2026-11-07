"""Shared fixtures: an app on a throw-away data dir, a fresh empty DB seeded by the tests, and
logged-in clients per role.

The environment must be prepared BEFORE `network_vault` is imported (util.py resolves its data and
backup folders at import time), which is why it happens at the top of this module.
"""
import atexit
import hashlib
import os
import re
import shutil
import tempfile
import threading
from datetime import timedelta
from pathlib import Path

import pytest

_ROOT = Path(tempfile.mkdtemp(prefix="nv_tests_"))
atexit.register(shutil.rmtree, _ROOT, ignore_errors=True)
for _name in ("NETWORK_VAULT_DATA",):
    os.environ.pop(_name, None)
os.environ["NETWORK_VAULT_DATA_DIR"] = str(_ROOT / "data")
os.environ["NETWORK_VAULT_BACKUP_DIR"] = str(_ROOT / "backups")
os.environ["NETWORK_VAULT_SECURE_COOKIES"] = "false"           # tests use plain HTTP
os.environ["NETWORK_VAULT_CONFIG"] = str(_ROOT / "no-config.toml")  # ignore the developer's config.toml

PASSWORD = "Passw0rd!x"
LLDP_HEADER = "Local Interface    Parent Interface    Chassis Id          Port info          System Name\n"


def lldp_row(local, parent, name):
    return f"{local:<19}{parent:<20}54:4b:8c:aa:bb:01   ge-0/0/47          {name}\n"


@pytest.fixture(scope="session")
def app():
    from network_vault import create_app
    from network_vault import util
    assert str(util.BASE_DIR).startswith(str(_ROOT)), "tests must never touch a real data folder"
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture(scope="session")
def seed(app):
    """Users, sites, credentials, devices and backups. Returns plain ids/names (no ORM objects)."""
    from network_vault.models import Backup, Credential, Device, Site, User, db, now as models_now
    from network_vault.util import BACKUP_DIR, encrypt

    with app.app_context():
        hc = Site.query.filter_by(code="MGMHC").one()
        ml = Site.query.filter_by(code="MGM-Malar").one()
        hc_id, ml_id = hc.id, ml.id

        def user(name, role, site_id=None):
            u = User(username=name, role=role, site_id=site_id, must_change=False)
            u.set_password(PASSWORD)
            db.session.add(u)

        user("t_admin", "group-admin")
        user("t_siteadmin", "site-admin", hc_id)
        user("t_rw", "read-write", hc_id)
        user("t_ro", "read-only", hc_id)
        db.session.commit()

        creds = {}
        for key, sid in (("hc", hc_id), ("ml", ml_id)):
            c = Credential(site_id=sid, name=f"cred-{key}", username="u", password_enc=encrypt("p"))
            db.session.add(c)
            db.session.flush()
            creds[key] = c.id

        def device(name, site, role, platform="juniper_junos", group="", host=None, cred=True):
            d = Device(name=name, host=host, site_id=site,
                       role=role, platform=platform, group=group, enabled=True,
                       credential_id=creds["hc" if site == hc_id else "ml"] if cred else None)
            db.session.add(d)
            db.session.flush()
            return d

        devs = {
            "dist": device("T-DIST", hc_id, "distribution", host="10.0.0.1"),
            "core": device("T-CORE", hc_id, "core", platform="cisco_ios", host="10.0.0.2"),
            "a": device("T-ACC-A", hc_id, "access", group="Level-10", host="10.0.1.1"),
            "b": device("T-ACC-B", hc_id, "access", group="Level-10", host="10.0.1.2"),
            "mal": device("T-MAL-ACC", ml_id, "access", host="10.0.2.1"),
            **{f"x{i}": device(f"T-ACC-X{i}", hc_id, "access", group="Level-3", host=f"10.0.3.{i}") for i in range(1, 7)},
            "fw": device("T-FW", hc_id, "firewall", platform="sonicwall_sonicos", host="10.0.0.9"),
        }
        db.session.commit()

        old_conf = "hostname T-ACC-A\nset system host-name T-ACC-A\n## Last commit: 2026-01-01\nset vlans v10 vlan-id 10\n"
        new_conf = "hostname T-ACC-A\nset system host-name T-ACC-A\n## Last commit: 2026-02-02\nset vlans v10 vlan-id 10\nset vlans v20 vlan-id 20\n"
        now = models_now()
        backups = []
        for i, text in enumerate((old_conf, new_conf)):
            rel = f"T-ACC-A/T-ACC-A_{i}.conf"
            (BACKUP_DIR / "T-ACC-A").mkdir(parents=True, exist_ok=True)
            (BACKUP_DIR / rel).write_text(text, encoding="utf-8")
            b = Backup(device_id=devs["a"].id, path=rel, status="success", size=len(text), trigger="manual",
                       sha256=hashlib.sha256(text.encode()).hexdigest(),
                       created_at=now - timedelta(days=2 - i))
            db.session.add(b)
            backups.append(b)
        db.session.commit()
        return {
            "hc_id": hc_id, "ml_id": ml_id,
            "dev": {k: (d.id, d.name) for k, d in devs.items()},
            "backups": [b.id for b in backups],
            "old_conf": old_conf, "new_conf": new_conf,
        }


def _login_client(app, username):
    from network_vault.models import User, password_version
    client = app.test_client()
    with app.app_context():
        u = User.query.filter_by(username=username).one()
        pwv = password_version(u)
    with client.session_transaction() as s:
        s["user"] = username
        s["pwv"] = pwv
        s["csrf"] = f"csrf-{username}"
    client.csrf = f"csrf-{username}"
    client.csrf_headers = {"X-CSRF-Token": client.csrf}
    return client


@pytest.fixture()
def anon(app, seed):
    return app.test_client()


@pytest.fixture()
def admin(app, seed):
    return _login_client(app, "t_admin")


@pytest.fixture()
def site_admin(app, seed):
    return _login_client(app, "t_siteadmin")


@pytest.fixture()
def writer(app, seed):
    return _login_client(app, "t_rw")


@pytest.fixture()
def readonly(app, seed):
    """Read-only user limited to the MGMHC site."""
    return _login_client(app, "t_ro")


@pytest.fixture()
def login_as(app, seed):
    return lambda username: _login_client(app, username)


@pytest.fixture()
def switches(monkeypatch):
    """Fake SSH: scripted output per (switch name, exact command); never opens a connection."""
    from network_vault import engine

    class FakeSwitches:
        def __init__(self):
            self.script = {}
            self.sent = []
            self.delay = 0.0
            self._lock = threading.Lock()

        def reset(self):
            self.script.clear()
            self.sent.clear()
            self.delay = 0.0

        def cmds(self, name=None):
            return [c for n, c in self.sent if (name is None or n == name) and c != "CONNECT"]

        def queried(self):
            return sorted({n for n, c in self.sent if c == "CONNECT"})

    fake = FakeSwitches()

    class Conn:
        def __init__(self, dev):
            self.name = dev.name

        def disconnect(self):
            pass

    def connect(device, *a, **k):
        with fake._lock:
            fake.sent.append((device.name, "CONNECT"))
        return Conn(device)

    def run(conn, cmd):
        import time
        with fake._lock:
            fake.sent.append((conn.name, cmd))
        if fake.delay:
            time.sleep(fake.delay)
        v = fake.script.get((conn.name, cmd), "")
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(engine, "connect", connect)
    monkeypatch.setattr(engine, "run", run)
    return fake


def page_attr(html, attr):
    m = re.search(r"<html[^>]*>", html)
    tag = m.group(0) if m else ""
    found = re.search(rf'{attr}="([^"]*)"', tag)
    return found.group(1) if found else None
