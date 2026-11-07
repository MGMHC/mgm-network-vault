"""Login enforcement and role / site limits."""
import pytest

from conftest import PASSWORD

PAGES = ["/", "/devices", "/backups", "/schedules", "/logs", "/credentials", "/settings",
         "/operations/port-locator", "/sites", "/ipam", "/racks"]


@pytest.mark.parametrize("path", PAGES)
def test_logged_out_pages_redirect_to_login(anon, path):
    r = anon.get(path)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_logged_out_api_post_is_redirected_not_executed(anon):
    r = anon.post("/api/backup", json={"all": True})
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_logged_out_theme_api_is_401(anon):
    assert anon.post("/api/account/theme", json={"look": "glass", "mode": "dark"}).status_code == 401


def test_login_page_is_public(anon):
    assert anon.get("/login").status_code == 200


def test_login_with_good_and_bad_password(anon, seed):
    bad = anon.post("/login", data={"username": "t_admin", "password": "nope"})
    assert bad.status_code == 200 and "Invalid username or password" in bad.get_data(as_text=True)
    good = anon.post("/login", data={"username": "t_admin", "password": PASSWORD})
    assert good.status_code == 302
    assert anon.get("/").status_code == 200


def test_open_redirect_in_login_next_is_ignored(anon, seed):
    r = anon.post("/login?next=//evil.example", data={"username": "t_admin", "password": PASSWORD})
    assert r.status_code == 302 and "evil.example" not in r.headers["Location"]


def test_session_dies_when_password_changes(admin, app):
    from network_vault.models import User, db
    with app.app_context():
        u = User.query.filter_by(username="t_admin").one()
        original = u.password_hash
        u.set_password("Another1!pass")
        db.session.commit()
    try:
        assert admin.get("/devices").status_code == 302
    finally:
        with app.app_context():
            u = User.query.filter_by(username="t_admin").one()
            u.password_hash = original
            db.session.commit()


def test_post_without_csrf_token_is_rejected(admin, seed):
    assert admin.post("/api/backup", json={"ids": []}).status_code == 400


def test_read_only_cannot_start_backup(readonly, seed):
    dev_id = seed["dev"]["a"][0]
    r = readonly.post("/api/backup", json={"ids": [dev_id]}, headers=readonly.csrf_headers)
    assert r.status_code == 403


def test_read_only_cannot_write(readonly, seed):
    dev_id = seed["dev"]["a"][0]
    assert readonly.get("/devices/new").status_code == 403
    assert readonly.get(f"/devices/{dev_id}/edit").status_code == 403
    r = readonly.post(f"/devices/{dev_id}/delete", headers=readonly.csrf_headers)
    assert r.status_code == 403
    r = readonly.post(f"/backups/{seed['backups'][0]}/pin", headers=readonly.csrf_headers)
    assert r.status_code == 403


def test_read_only_can_read(readonly, seed):
    assert readonly.get("/devices").status_code == 200
    assert readonly.get(f"/devices/{seed['dev']['a'][0]}").status_code == 200


def test_read_only_cannot_use_admin_pages(readonly):
    assert readonly.get("/alerts").status_code == 403


def test_group_admin_can_use_admin_pages(admin):
    assert admin.get("/alerts").status_code == 200


def test_site_limited_user_cannot_see_other_sites_device(readonly, seed):
    assert readonly.get(f"/devices/{seed['dev']['mal'][0]}").status_code == 403


def test_site_limited_user_device_list_hides_other_site(readonly, seed):
    html = readonly.get("/devices").get_data(as_text=True)
    assert seed["dev"]["a"][1] in html
    assert seed["dev"]["mal"][1] not in html


def test_site_limited_user_cannot_run_actions_on_other_site(writer, seed):
    r = writer.post("/api/backup", json={"ids": [seed["dev"]["mal"][0]]}, headers=writer.csrf_headers)
    assert r.status_code == 403


def test_site_limited_user_cannot_edit_other_sites_device(writer, seed):
    assert writer.get(f"/devices/{seed['dev']['mal'][0]}/edit").status_code == 403


def test_group_admin_sees_every_site(admin, seed):
    assert admin.get(f"/devices/{seed['dev']['mal'][0]}").status_code == 200


def test_locator_scope_is_limited_to_users_site(readonly, seed, switches):
    r = readonly.post("/api/operations/port-locator/stream",
                      json={"query": "84a93e76eab4", "site_id": seed["ml_id"]}, headers=readonly.csrf_headers)
    assert r.status_code == 403


def test_role_flags(app, seed):
    from network_vault.models import User
    with app.app_context():
        get = lambda n: User.query.filter_by(username=n).one()
        assert get("t_admin").is_group_admin and get("t_admin").allowed_site_ids is None
        sa, rw, ro = get("t_siteadmin"), get("t_rw"), get("t_ro")
        assert sa.is_admin and not sa.is_group_admin
        assert rw.can_write and rw.can_backup and not rw.is_admin
        assert not ro.can_write and not ro.can_backup
        assert ro.allowed_site_ids == [seed["hc_id"]]
        assert ro.has_site_access(seed["hc_id"]) and not ro.has_site_access(seed["ml_id"])
