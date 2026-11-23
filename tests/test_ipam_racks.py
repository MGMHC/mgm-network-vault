"""IPAM prefix nesting / IP parent choice / status allow-list, and rack site scoping."""
import pytest

from network_vault.models import IPAddress, Prefix, Rack, db


@pytest.fixture()
def prefixes(app, seed):
    with app.app_context():
        ids = []
        for cidr in ("10.0.0.0/8", "10.1.1.0/24"):
            p = Prefix(prefix=cidr, family=4, site_id=seed["hc_id"])
            db.session.add(p)
            db.session.flush()
            ids.append(p.id)
        db.session.commit()
    yield ids
    with app.app_context():
        IPAddress.query.filter_by(address="10.1.1.5").delete()
        for i in ids:
            db.session.delete(db.session.get(Prefix, i))
        db.session.commit()


def test_ip_attaches_to_most_specific_prefix(admin, prefixes, seed):
    r = admin.post("/api/ipam/ip-addresses", json={"address": "10.1.1.5", "site_id": seed["hc_id"]},
                   headers=admin.csrf_headers)
    assert r.get_json()["ip_address"]["prefix_id"] == prefixes[1]


def test_hierarchy_nests_inside_same_vrf_only(admin, prefixes, seed):
    h = admin.get(f"/api/ipam/data?site_id={seed['hc_id']}").get_json()["hierarchy"]
    top = [p for p in h if p["id"] == prefixes[0]]
    assert top and [c["id"] for c in top[0]["children"]] == [prefixes[1]]


@pytest.mark.parametrize("url", ["/api/ipam/prefixes", "/api/ipam/vlans", "/api/ipam/ip-addresses", "/api/racks"])
def test_bad_status_rejected(admin, url):
    body = {"prefix": "10.9.0.0/24", "vid": 99, "name": "x", "address": "10.9.0.1", "status": "<img onerror=1>"}
    assert admin.post(url, json=body, headers=admin.csrf_headers).status_code == 400


def test_rack_detail_hides_other_sites_unmounted_devices(app, writer, seed):
    with app.app_context():
        rack = Rack(name="HC-R1", site_id=seed["hc_id"])
        db.session.add(rack)
        db.session.commit()
        rid = rack.id
    names = [d["name"] for d in writer.get(f"/api/racks/{rid}").get_json()["unmounted_devices"]]
    assert "T-MAL-ACC" not in names and "T-DIST" in names
    with app.app_context():
        db.session.delete(db.session.get(Rack, rid))
        db.session.commit()
