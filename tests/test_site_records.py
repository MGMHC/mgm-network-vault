"""IPAM and rack records follow the device rule: users change only records of sites they can access."""
import pytest

from network_vault.models import VLAN, Rack, db


@pytest.fixture()
def malar_records(app, seed):
    with app.app_context():
        vlan = VLAN(vid=3001, name="malar-only", site_id=seed["ml_id"])
        rack = Rack(name="MALAR-R1", site_id=seed["ml_id"])
        db.session.add_all([vlan, rack])
        db.session.commit()
        ids = vlan.id, rack.id
    yield ids
    with app.app_context():
        for model, rid in zip((VLAN, Rack), ids):
            row = db.session.get(model, rid)
            if row:
                db.session.delete(row)
        db.session.commit()


def test_writer_cannot_change_another_sites_ipam_or_racks(writer, malar_records, seed):
    vlan_id, rack_id = malar_records
    h = writer.csrf_headers
    assert writer.post(f"/api/ipam/vlans/{vlan_id}/delete", headers=h).status_code == 403
    assert writer.post(f"/api/racks/{rack_id}/delete", headers=h).status_code == 403
    moved = writer.post("/api/racks", json={"id": rack_id, "name": "MALAR-R1", "site_id": seed["hc_id"]}, headers=h)
    assert moved.status_code == 403
    assert writer.post("/api/ipam/vlans", json={"vid": 3002, "name": "x", "site_id": seed["ml_id"]},
                       headers=h).status_code == 403


def test_group_admin_can_change_any_site(admin, malar_records):
    vlan_id, _ = malar_records
    assert admin.post(f"/api/ipam/vlans/{vlan_id}/delete", headers=admin.csrf_headers).status_code == 200


def test_writer_sees_only_own_sites_ipam_and_racks(writer, malar_records):
    vlan_id, rack_id = malar_records
    vlans = writer.get("/api/ipam/data?site_id=all").get_json()["vlans"]
    assert vlan_id not in [v["id"] for v in vlans]
    assert writer.get(f"/api/racks/{rack_id}").status_code == 404
