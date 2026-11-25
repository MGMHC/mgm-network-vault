"""Tests for OUI endpoint fingerprinting (MAC/IP history & IPAM) and L2 Topology Map."""
import json

from network_vault import machistory, netutil, switch_tables, topology
from network_vault.models import Device, IPAddress, MacSighting, TopologyLink, db, now


def test_oui_lookup_and_endpoint_classification():
    # Medical catalog override
    ep_med = netutil.classify_endpoint("00:07:f6:11:22:33")
    assert ep_med["vendor"] == "GE Healthcare"
    assert ep_med["category"] == "medical"
    assert ep_med["randomized"] is False

    # IEEE netaddr fallback (VMware OUI not in OUI_CATALOG or in catalog -> VM)
    ep_vm = netutil.classify_endpoint("00:50:56:aa:bb:cc")
    assert "VMware" in ep_vm["vendor"]
    assert ep_vm["category"] == "vm"

    # IEEE netaddr fallback for an OUI outside OUI_CATALOG (00:00:0c = Cisco Systems, Inc.)
    ep_ieee = netutil.classify_endpoint("00:00:0c:12:34:56")
    assert "Cisco" in ep_ieee["vendor"]
    assert ep_ieee["category"] == "network"

    # Locally administered / randomized MAC (bit 1 of first octet set, e.g. 0xda)
    ep_rand = netutil.classify_endpoint("da:a1:19:11:22:33")
    assert ep_rand["randomized"] is True
    assert ep_rand["category"] == "randomized"


def test_mac_history_and_ipam_enrichment(app, admin, seed):
    dev_id = seed["dev"]["a"][0]
    hc_id = seed["hc_id"]
    with app.app_context():
        s = MacSighting(mac="001565aabbcc", ip="10.99.1.50", device_id=dev_id, port="ge-0/0/5", vlan="v10",
                        first_seen=now(), last_seen=now())
        ip_rec = IPAddress(address="10.99.1.50/24", site_id=hc_id, status="active")
        db.session.add_all([s, ip_rec])
        db.session.commit()
        d = machistory.row_dict(s)
        assert d["vendor"] == "Yealink IP Phones"
        assert d["device_type"] == "phone"

    try:
        r_page = admin.get("/operations/mac-history")
        assert r_page.status_code == 200
        assert "Yealink IP Phones" in r_page.get_data(as_text=True)

        r_ipam = admin.get("/api/ipam/data")
        assert r_ipam.status_code == 200
        ips = r_ipam.get_json()["ip_addresses"]
        hit = next(x for x in ips if x["address"] == "10.99.1.50/24")
        assert hit["vendor"] == "Yealink IP Phones"
        assert hit["device_type"] == "phone"
        assert hit["mac"] == "00:15:65:aa:bb:cc"
    finally:
        with app.app_context():
            MacSighting.query.filter_by(mac="001565aabbcc").delete()
            IPAddress.query.filter_by(address="10.99.1.50/24").delete()
            db.session.commit()


def test_query_all_lldp_neighbors_junos_and_cisco(app, switches, seed):
    switches.reset()
    dist_name = seed["dev"]["dist"][1]
    core_name = seed["dev"]["core"][1]

    switches.script[(dist_name, "show lldp neighbors | no-more")] = (
        "Local Interface    Parent Interface    Chassis Id          Port info          System Name\n"
        f"xe-0/0/0           ae0                 54:4b:8c:aa:bb:01   Gi1/0/1            {core_name}\n"
        "ge-0/0/10          -                   00:1a:1e:11:22:33   eth0               AP-WARD-01\n"
    )
    switches.script[(core_name, "show lldp neighbors detail")] = (
        "Local Intf: Gi1/0/1\n"
        "Chassis id: 544b.8caa.bb02\n"
        "Port id: xe-0/0/0\n"
        f"System Name: {dist_name}\n"
        "Management Addresses:\n    IP: 10.0.0.1\n"
    )

    with app.app_context():
        dist = db.session.get(Device, seed["dev"]["dist"][0])
        core = db.session.get(Device, seed["dev"]["core"][0])
        j_nbs = switch_tables.query_all_lldp_neighbors(dist)
        c_nbs = switch_tables.query_all_lldp_neighbors(core)

    assert len(j_nbs) == 2
    assert j_nbs[0]["local_port"] == "xe-0/0/0"
    assert j_nbs[0]["parent"] == "ae0"
    assert j_nbs[0]["system_name"] == core_name
    assert len(c_nbs) == 1
    assert c_nbs[0]["local_port"] == "Gi1/0/1"
    assert c_nbs[0]["system_name"] == dist_name
    assert c_nbs[0]["mgmt_ip"] == "10.0.0.1"


def test_topology_discovery_and_deduplication(app, admin, switches, seed):
    switches.reset()
    dist_name = seed["dev"]["dist"][1]
    core_name = seed["dev"]["core"][1]
    a_name = seed["dev"]["a"][1]

    switches.script[(dist_name, "show lldp neighbors | no-more")] = (
        "Local Interface    Parent Interface    Chassis Id          Port info          System Name\n"
        f"xe-0/0/0           ae0                 54:4b:8c:aa:bb:01   Gi1/0/1            {core_name}\n"
        f"ge-0/0/10          -                   54:4b:8c:aa:bb:02   ge-0/0/47          {a_name}\n"
    )
    switches.script[(core_name, "show lldp neighbors detail")] = (
        "Local Intf: Gi1/0/1\n"
        "Port id: xe-0/0/0\n"
        f"System Name: {dist_name}\n"
    )

    try:
        resp = admin.post("/api/operations/topology/stream", json={"site_id": seed["hc_id"]},
                          headers=admin.csrf_headers)
        assert resp.status_code == 200
        events = [json.loads(line) for line in resp.get_data(as_text=True).splitlines() if line.strip()]
        done = next(e for e in events if e["type"] == "done")
        graph = done["graph"]
        # dist <-> core should be deduplicated into 1 edge, plus dist <-> a = 2 edges
        assert graph["stats"]["links"] == 2
        assert graph["stats"]["lags"] == 1

        cached = admin.get("/api/operations/topology/data").get_json()
        assert cached["ok"] is True
        assert cached["stats"]["links"] == 2
    finally:
        with app.app_context():
            TopologyLink.query.delete()
            db.session.commit()
