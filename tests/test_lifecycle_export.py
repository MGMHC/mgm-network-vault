"""Lifecycle CSV export: a Juniper Virtual Chassis exports one row per member switch."""
import csv
import io
import json

VC = [
    {"id": "0", "status": "Prsnt", "serial": "CU0218350076", "model": "ex2200-48t-4g", "prio": "255", "role": "Master", "this": True},
    {"id": "1", "status": "Prsnt", "serial": "CU0218350155", "model": "ex2200-48t-4g", "prio": "255", "role": "Backup", "this": False},
    {"id": "2", "status": "Prsnt", "serial": "CV0218360226", "model": "ex2200-24p-4g", "prio": "128", "role": "Linecard", "this": False},
]


def _rows(admin):
    r = admin.get("/operations/lifecycle/export.csv")
    assert r.status_code == 200
    return list(csv.DictReader(io.StringIO(r.data.decode("utf-8-sig"))))


def test_vc_stack_exports_every_member(app, admin):
    from network_vault.models import Device, db
    with app.app_context():
        d = Device.query.filter_by(name="T-DIST").one()
        d.serial, d.vc_members_json = "CU0218350076", json.dumps(VC)
        db.session.commit()
    try:
        rows = [r for r in _rows(admin) if r["Switch Name"] == "T-DIST"]
        assert [(r["VC Member"], r["VC Role"], r["Serial Number"]) for r in rows] == [
            ("0", "Master", "CU0218350076"), ("1", "Backup", "CU0218350155"), ("2", "Linecard", "CV0218360226")]
        assert rows[2]["Detected Model"] == "ex2200-24p-4g"
    finally:
        with app.app_context():
            d = Device.query.filter_by(name="T-DIST").one()
            d.serial = d.vc_members_json = None
            db.session.commit()


def test_standalone_switch_exports_one_row(admin):
    rows = [r for r in _rows(admin) if r["Switch Name"] == "T-ACC-A"]
    assert len(rows) == 1 and rows[0]["VC Member"] == "" and rows[0]["VC Role"] == ""


def test_lifecycle_page_lists_every_member_serial(app, admin):
    from network_vault.models import Device, db
    with app.app_context():
        d = Device.query.filter_by(name="T-DIST").one()
        d.serial, d.vc_members_json = "CU0218350076", json.dumps(VC)
        db.session.commit()
    try:
        html = admin.get("/operations/lifecycle").data.decode()
        for m in VC:
            assert m["serial"] in html
        assert "1 Backup" in html and "ex2200-24p-4g" in html
    finally:
        with app.app_context():
            d = Device.query.filter_by(name="T-DIST").one()
            d.serial = d.vc_members_json = None
            db.session.commit()


def test_fetch_with_nothing_in_scope_reports_and_ends(writer):
    import json as _json
    r = writer.post("/api/operations/lifecycle/fetch", json={"device_ids": [999999]}, headers=writer.csrf_headers)
    lines = [_json.loads(l) for l in r.get_data(as_text=True).splitlines()]
    assert [l["type"] for l in lines] == ["init", "complete"] and lines[0]["total"] == 0
