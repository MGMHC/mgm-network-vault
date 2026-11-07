"""Port Locator building blocks: MAC helpers, LLDP parsing, neighbour matching, filtered MAC/ARP queries."""
from types import SimpleNamespace as N

import pytest

import locator_samples as S
from network_vault import engine, netutil, portclass, switch_tables, vlan_audit

MAC = "84a93e76eab4"
COLON = "84:a9:3e:76:ea:b4"
CISCO = "84a9.3e76.eab4"


# ------------------------------------------------------------------ pure helpers

@pytest.mark.parametrize("raw", [COLON, CISCO, "84-A9-3E-76-EA-B4", "84A93E76EAB4", " 84a93e76eab4 "])
def test_clean_mac_accepts_every_notation(raw):
    assert netutil.clean_mac(raw) == MAC


@pytest.mark.parametrize("raw", ["", None, "84:a9:3e", "zz:a9:3e:76:ea:b4", "84a93e76eab4ff"])
def test_clean_mac_rejects_bad_input(raw):
    assert netutil.clean_mac(raw) == ""


def test_format_mac_variants():
    assert netutil.format_mac(MAC) == COLON
    assert netutil.format_mac(MAC, "cisco") == CISCO
    assert netutil.format_mac(MAC, "dash") == "84-a9-3e-76-ea-b4"
    assert netutil.format_mac("short") == "short"


@pytest.mark.parametrize("value,ok", [("10.1.2.3", True), ("256.1.1.1", False), ("10.1.2", False), ("a.b.c.d", False), ("", False)])
def test_is_ip(value, ok):
    assert netutil.is_ip(value) is ok


@pytest.mark.parametrize("port", ["ae12", "ae7.0", "Po1", "port-channel3", "lag1", "bundle-ether2"])
def test_lag_names_are_uplinks(port):
    assert portclass.is_uplink_port(port) is True


@pytest.mark.parametrize("port", ["ge-0/0/14", "Gi1/0/7", "", "—"])
def test_plain_ports_are_not_uplinks(port):
    assert portclass.is_uplink_port(port) is False


def test_many_learned_macs_means_uplink():
    assert portclass.is_uplink_port("ge-0/0/14", learned_mac_count=3) is True


def test_trunk_config_snippet_means_uplink():
    assert portclass.is_uplink_port("ge-0/0/1", port_config_snippet="set interfaces ge-0/0/1 port-mode trunk") is True


# ------------------------------------------------------------------ LLDP table parsing

def test_junos_lldp_table_plain_rows():
    rows = {r["local"]: r for r in switch_tables._parse_junos_lldp_table(S.JUNOS_LLDP)}
    assert rows["ge-0/0/20"]["name"] == "CoreSwitchA"
    assert rows["ge-0/0/20"]["parent"] == "-"
    assert rows["ge-0/0/46"]["parent"] == "ae12"


def test_junos_lldp_long_port_description_does_not_eat_system_name():
    rows = {r["local"]: r for r in switch_tables._parse_junos_lldp_table(S.JUNOS_LLDP)}
    assert rows["ge-0/0/16.0"]["name"] == "4F-RACK2-ACC-VC2-S1-S2-90.15"
    assert rows["ge-0/0/16.0"]["port"] == "UPLINK_TO_DISTRIBUTION"
    assert rows["ge-1/0/13.0"]["name"] == "7F-RACK1-ACC-SW7_90.18"


def test_junos_lldp_without_header_or_empty():
    assert switch_tables._parse_junos_lldp_table("") == []
    assert switch_tables._parse_junos_lldp_table("nothing useful") == []


def test_junos_lldp_table_without_parent_column():
    out = "Local Interface    Chassis Id          Port info          System Name\nge-0/0/1           aa:bb:cc:dd:ee:ff   ge-0/0/2           peer-sw\n"
    rows = switch_tables._parse_junos_lldp_table(out)
    assert rows[0]["name"] == "peer-sw" and rows[0]["parent"] == ""


def test_base_port():
    assert netutil.base_port("ge-0/0/14.0") == "ge-0/0/14"
    assert netutil.base_port("Gi1/0/14") == "gi1/0/14"


def test_cisco_po_member(switches):
    switches.script[("sw", "show etherchannel summary")] = S.ETHERCHANNEL_SUMMARY
    conn = N(name="sw")
    assert switch_tables._cisco_po_member(conn, "Po1") == "Gi1/0/49"
    assert switch_tables._cisco_po_member(conn, "Po9") is None
    assert switch_tables._cisco_po_member(conn, "Gi1/0/1") is None


# ------------------------------------------------------------------ neighbour matching

CANDS = [
    N(id=1, name="4F-RACK2-ACC-VC2", hostname=None, host="90.90.90.15", site_id=1),
    N(id=2, name="1F-RACK1-ACC-VC3", hostname=None, host="90.90.90.9", site_id=1),
    N(id=3, name="4F-RACK2-ACC-VC1", hostname=None, host="90.90.90.14", site_id=1),
    N(id=4, name="SwitchX", hostname="Switch", host="10.0.0.1", site_id=1),
    N(id=5, name="SwitchY", hostname="Switch", host="10.0.0.2", site_id=1),
]


@pytest.mark.parametrize("system_name,expected", [
    ("4F-RACK2-ACC-VC2-S1-S2-90.15", 1),   # IP tail in name
    ("1F-RACK1-ACC-SW7_90.9", 2),          # IP tail, name differs from Vault
    ("4F-RACK2-ACC-VC1-S9", 3),            # Vault-name prefix
    ("Switch", None),                      # duplicate hostname is ambiguous
    ("4F-RACK2-ACC-VC", None),             # no partial-word prefix match
])
def test_match_neighbor_fallbacks(system_name, expected):
    m = switch_tables.match_neighbor({"system_name": system_name}, CANDS, site_id=1)
    assert (m.id if m else None) == expected


def test_duplicate_hostname_resolved_by_mgmt_ip():
    m = switch_tables.match_neighbor({"system_name": "Switch", "mgmt_ip": "10.0.0.2"}, CANDS, site_id=1)
    assert m.id == 5


def test_match_neighbor_strips_domain_and_matches_name_or_ip():
    cands = [N(id=1, name="DIST", hostname="dsw-agg", host="1.1.1.1", site_id=1),
             N(id=2, name="ACC", hostname=None, host="2.2.2.2", site_id=1)]
    assert switch_tables.match_neighbor({"system_name": "dsw-agg.mgm.local"}, cands).id == 1
    assert switch_tables.match_neighbor({"system_name": "acc"}, cands).id == 2
    assert switch_tables.match_neighbor({"system_name": "nope", "mgmt_ip": "2.2.2.2"}, cands).id == 2
    assert switch_tables.match_neighbor({"system_name": "nope", "mgmt_ip": "9.9.9.9"}, cands) is None
    assert switch_tables.match_neighbor(None, cands) is None


# ------------------------------------------------------------------ device queries (stubbed SSH)

@pytest.fixture()
def devs(app, seed):
    """Detached copies of a Juniper and a Cisco switch (queries only read plain attributes)."""
    from network_vault.models import Device
    with app.app_context():
        j = Device.query.filter_by(name=seed["dev"]["dist"][1]).one()
        c = Device.query.filter_by(name=seed["dev"]["core"][1]).one()
        return N(id=j.id, name=j.name, host=j.host, port=22, platform=j.platform, is_juniper=True,
                 legacy_ssh=False, credential_id=j.credential_id, role=j.role, hostname=None, location="", group=""), \
               N(id=c.id, name=c.name, host=c.host, port=22, platform=c.platform, is_juniper=False,
                 legacy_ssh=False, credential_id=c.credential_id, role=c.role, hostname=None, location="", group="")


def test_junos_empty_filtered_result_is_trusted(devs, switches):
    j, _ = devs
    assert switch_tables.query_mac_table(j, MAC) == []
    assert "show ethernet-switching table | no-more" not in switches.cmds()


def test_junos_filter_error_falls_back_to_full_table(devs, switches):
    j, _ = devs
    switches.script[(j.name, f"show ethernet-switching table | match {COLON} | no-more")] = RuntimeError("syntax error")
    switches.script[(j.name, "show ethernet-switching table | no-more")] = f"   v120   {COLON}   D   -   ge-0/0/14.0   0   0\n"
    r = switch_tables.query_mac_table(j, MAC)
    assert len(r) == 1 and r[0]["interface"] == "ge-0/0/14"


def test_junos_vlan_name_that_looks_like_a_port_is_not_the_interface(devs, switches):
    j, _ = devs
    switches.script[(j.name, f"show ethernet-switching table | match {COLON} | no-more")] = \
        f"   Telephony   {COLON}   D   -   ge-0/0/9.0   0   0\n"
    r = switch_tables.query_mac_table(j, MAC)
    assert r[0]["interface"] == "ge-0/0/9" and r[0]["vlan"] == "Telephony"


def test_cisco_empty_filtered_result_is_trusted(devs, switches):
    _, c = devs
    assert switch_tables.query_mac_table(c, MAC) == []
    assert "show mac address-table" not in switches.cmds()


def test_cisco_filter_error_falls_back(devs, switches):
    _, c = devs
    for cmd in (f"show mac address-table address {CISCO}", f"show mac-address-table address {CISCO}"):
        switches.script[(c.name, cmd)] = RuntimeError("% Invalid input")
    switches.script[(c.name, f"show mac address-table | include {CISCO}|{COLON}")] = f" 120    {CISCO}    DYNAMIC     Gi1/0/14\n"
    r = switch_tables.query_mac_table(c, MAC)
    assert len(r) == 1 and r[0]["interface"] == "Gi1/0/14"


def test_cisco_rejected_command_text_moves_to_next_form(devs, switches):
    _, c = devs
    switches.script[(c.name, f"show mac address-table address {CISCO}")] = "                ^\n% Invalid input detected at '^' marker.\n"
    switches.script[(c.name, f"show mac-address-table address {CISCO}")] = "% Invalid input detected at '^' marker.\n"
    switches.script[(c.name, f"show mac address-table | include {CISCO}|{COLON}")] = f"  10    {CISCO}    DYNAMIC     Gi1/0/7\n"
    r = switch_tables.query_mac_table(c, target_mac_clean=MAC)
    assert len(r) == 1 and r[0]["interface"] == "Gi1/0/7"


def test_arp_empty_filtered_result_does_not_fetch_full_arp(devs, switches):
    j, _ = devs
    assert switch_tables.query_arp(j, target_ip="10.1.1.5") == []
    assert "show arp no-resolve | no-more" not in switches.cmds()


def test_lldp_neighbor_via_ae_parent_and_direct(devs, switches):
    j, _ = devs
    switches.script[(j.name, "show lldp neighbors | no-more")] = S.JUNOS_LLDP
    nb = switch_tables.query_lldp_neighbor(j, "ae12")
    assert nb["system_name"] == "10F-RACK1-ACC-SW3-90.26" and nb["parent"] == "ae12"
    assert switch_tables.query_lldp_neighbor(j, "ge-0/0/20")["system_name"] == "CoreSwitchA"
    nb = switch_tables.query_lldp_neighbor(j, "ae11")
    assert nb["system_name"] == "4F-RACK2-ACC-VC2-S1-S2-90.15"
    assert switch_tables.query_lldp_neighbor(j, "ge-9/9/9") is None


def test_junos_lldp_detail_gives_mgmt_ip(devs, switches):
    j, _ = devs
    switches.script[(j.name, "show lldp neighbors | no-more")] = S.JUNOS_LLDP
    switches.script[(j.name, "show lldp neighbors interface xe-0/0/3 | no-more")] = S.JUNOS_LLDP_DETAIL
    assert switch_tables.query_lldp_neighbor(j, "ae7", want_ip=True)["mgmt_ip"] == "90.90.90.33"


def test_cisco_lldp_detail_and_cdp_fallback(devs, switches):
    _, c = devs
    switches.script[(c.name, "show lldp neighbors Gi1/0/48 detail")] = S.CISCO_LLDP_DETAIL
    nb = switch_tables.query_lldp_neighbor(c, "Gi1/0/48")
    assert nb["system_name"] == "dsw-agg.mgm.local" and nb["mgmt_ip"] == "90.90.90.2"
    switches.script[(c.name, "show lldp neighbors Gi1/0/48 detail")] = RuntimeError("% LLDP is not enabled")
    switches.script[(c.name, "show cdp neighbors Gi1/0/48 detail")] = S.CDP_DETAIL
    nb = switch_tables.query_lldp_neighbor(c, "Gi1/0/48")
    assert nb["system_name"] == "MALAR-CSW" and nb["mgmt_ip"] == "10.20.0.1"


# ------------------------------------------------------------------ misc parsers

def test_parse_hostname():
    assert engine.parse_hostname("fpc0:\n------\nHostname: dsw-agg\nModel: ex3300-48p\n") == "dsw-agg"
    assert engine.parse_hostname("Cisco IOS Software...\nMALAR-SW1 uptime is 3 weeks\n") == "MALAR-SW1"
    assert engine.parse_hostname("nothing here") is None


def test_parse_junos_vlans_and_ports():
    cfg = "\n".join([
        "set vlans v10 vlan-id 10",
        "set vlans v10 description Staff",
        "set vlans v20 vlan-id 20",
        "set interfaces ge-0/0/1 unit 0 family ethernet-switching vlan members v10",
        "set interfaces ge-0/0/2 unit 0 family ethernet-switching vlan members v20",
        "set interfaces ge-0/0/3 description UPLINK",
    ])
    out = vlan_audit.parse_junos_vlans_and_ports(cfg)
    assert {v["id"] for v in out["vlans"]} == {10, 20}
    assert next(v for v in out["vlans"] if v["id"] == 10)["desc"] == "Staff"
    names = [p["interface"] for p in out["ports"]]
    assert "ge-0/0/1" in names and "ge-0/0/2" in names


def test_expand_junos_range():
    assert vlan_audit.expand_junos_range("ge-0/0/0", "ge-0/0/3") == ["ge-0/0/0", "ge-0/0/1", "ge-0/0/2", "ge-0/0/3"]
    assert vlan_audit.expand_junos_range("ge-0/0/0", "xe-0/0/3") == ["ge-0/0/0", "xe-0/0/3"]


@pytest.mark.parametrize("raw,short", [("GigabitEthernet1/0/1", "Gi1/0/1"), ("gi1/0/2", "Gi1/0/2"),
                                       ("TenGigabitEthernet1/1/1", "Te1/1/1"), ("Port-channel5", "Po5")])
def test_norm_port(raw, short):
    assert netutil.norm_port(raw) == short
