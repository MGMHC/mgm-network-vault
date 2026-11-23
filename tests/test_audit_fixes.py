"""Audit fixes: global settings, credential binding, backup-folder clashes, log scoping, usernames, scan cap."""
from network_vault import operations_views
from network_vault.models import Device, LogEntry, User, db, get_setting, set_setting
from network_vault.util import safe_name


def test_site_admin_cannot_change_global_retention(app, site_admin):
    with app.app_context():
        set_setting("retention_count", "30")
        db.session.commit()
    site_admin.post("/settings", data={"csrf_token": site_admin.csrf, "retention_count": "1",
                                       "log_retention_days": "1"})
    with app.app_context():
        assert get_setting("retention_count") == "30"


def test_unchanged_foreign_credential_cannot_follow_a_new_host(app, writer, seed):
    with app.app_context():
        foreign = db.session.get(Device, seed["dev"]["mal"][0]).credential_id
        d = Device(name="T-MOVED", host="10.0.9.1", site_id=seed["hc_id"], role="access",
                   platform="juniper_junos", credential_id=foreign, enabled=True)
        db.session.add(d)
        db.session.commit()
        did = d.id
    try:
        r = writer.post(f"/devices/{did}/edit", data={
            "csrf_token": writer.csrf, "name": "T-MOVED", "host": "203.0.113.5", "platform": "juniper_junos",
            "credential_id": str(foreign), "site_id": str(seed["hc_id"])})
        assert r.status_code == 403
        with app.app_context():
            assert db.session.get(Device, did).host == "10.0.9.1"
    finally:
        with app.app_context():
            db.session.delete(db.session.get(Device, did))
            db.session.commit()


def test_name_that_maps_to_an_existing_backup_folder_is_refused(app, writer, seed):
    r = writer.post("/devices/new", data={"csrf_token": writer.csrf, "name": "t-dist", "host": "10.0.9.2",
                                          "platform": "juniper_junos", "site_id": str(seed["hc_id"])})
    assert r.status_code == 200 and b"same backup folder" in r.data
    with app.app_context():
        assert Device.query.filter_by(name="t-dist").first() is None


def test_dot_names_stay_inside_the_backup_folder():
    assert safe_name("..") == "device" and safe_name(".") == "device"
    assert safe_name("core.sw1") == "core.sw1"


def test_other_users_login_events_are_hidden_below_group_admin(app, readonly, admin):
    with app.app_context():
        db.session.add(LogEntry(category="auth", user="someone-else", message="Failed login for 'zz-secret-typo'"))
        db.session.commit()
    assert b"zz-secret-typo" not in readonly.get("/logs?category=auth").data
    assert b"zz-secret-typo" in admin.get("/logs?category=auth").data


def test_username_with_quote_is_refused(app, admin):
    admin.post("/account", data={"csrf_token": admin.csrf, "action": "adduser", "username": "x');alert(1);//",
                                 "password": "LongEnough1!"})
    admin.post("/account", data={"csrf_token": admin.csrf, "action": "adduser", "username": "t.ok-user",
                                 "password": "LongEnough1!"})
    with app.app_context():
        assert User.query.filter(User.username.like("x')%")).first() is None
        ok = User.query.filter_by(username="t.ok-user").one()  # control: a plain name is still accepted
        db.session.delete(ok)
        db.session.commit()


def test_live_scans_are_capped(admin):
    taken = 0
    while operations_views._scan_slots.acquire(blocking=False):
        taken += 1
    try:
        r = admin.post("/api/operations/health/stream", json={}, headers=admin.csrf_headers)
        assert r.status_code == 429
    finally:
        for _ in range(taken):
            operations_views._scan_slots.release()
