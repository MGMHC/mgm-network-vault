"""Every main page renders for an admin; detail pages carry the parent back link (v1.18.0)."""
import re

import pytest

MAIN_PAGES = ["/", "/devices", "/devices/new", "/backups", "/schedules", "/logs", "/credentials", "/settings",
              "/sites", "/ipam", "/racks", "/alerts", "/github", "/account", "/migrate",
              "/operations/port-locator", "/operations/optics", "/operations/health",
              "/operations/vlan-audit", "/operations/lifecycle"]

BACK_LINK = re.compile(r'<a class="back-link" href="([^"]+)">.*?</svg>([^<]*)</a>', re.S)


@pytest.mark.parametrize("path", MAIN_PAGES)
def test_main_page_returns_200_for_admin(admin, path):
    assert admin.get(path).status_code == 200, path


def test_login_page_renders(anon):
    assert anon.get("/login").status_code == 200


def test_static_assets_served(anon):
    assert anon.get("/static/app.css").status_code == 200
    assert anon.get("/static/theme.css").status_code == 200


def test_top_level_pages_have_no_back_link_or_back_button(admin):
    for path in ("/", "/devices"):
        html = admin.get(path).get_data(as_text=True)
        assert "back-link" not in html
        assert "back-btn" not in html and "history.back()" not in html


def test_brand_group_identical_across_pages(admin):
    def nav(html):
        m = re.search(r'<div class="nav-brand-group">.*?</div>', html, re.S)
        return m.group(0) if m else ""
    a, b = (nav(admin.get(p).get_data(as_text=True)) for p in ("/", "/devices"))
    assert a and a == b


def _back(admin, path):
    r = admin.get(path)
    assert r.status_code == 200, path
    m = BACK_LINK.search(r.get_data(as_text=True))
    assert m, f"no back link on {path}"
    return m.group(1), m.group(2).strip()


def test_device_detail_back_link(admin, seed):
    assert _back(admin, f"/devices/{seed['dev']['a'][0]}") == ("/devices", "Devices")


def test_device_new_back_link(admin):
    assert _back(admin, "/devices/new") == ("/devices", "Devices")


def test_device_edit_back_link_goes_to_device(admin, seed):
    dev_id, name = seed["dev"]["a"]
    assert _back(admin, f"/devices/{dev_id}/edit") == (f"/devices/{dev_id}", name)


def test_backup_view_back_link(admin, seed):
    assert _back(admin, f"/backups/{seed['backups'][0]}") == ("/backups", "Backups")


def test_compare_back_link(admin, seed):
    a, b = seed["backups"]
    assert _back(admin, f"/compare?a={a}&b={b}") == ("/backups", "Backups")


def test_compare_without_backups_redirects(admin):
    assert admin.get("/compare").status_code == 302
