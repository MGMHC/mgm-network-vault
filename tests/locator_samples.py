"""Canned switch output used by the Port Locator tests (shapes taken from real EX3300 / Catalyst output)."""

JUNOS_LLDP = """Local Interface    Parent Interface    Chassis Id          Port info          System Name
ge-0/0/46          ae12                54:4b:8c:aa:bb:01   ge-0/0/47          10F-RACK1-ACC-SW3-90.26
ge-1/0/46          ae12                54:4b:8c:aa:bb:01   ge-1/0/47          10F-RACK1-ACC-SW3-90.26
ge-0/0/20          -                   00:11:22:33:44:55   Gi1/0/1            CoreSwitchA
ge-0/0/16.0        ae11.0              b0:33:a6:7c:40:00   UPLINK_TO_DISTRIBUTION 4F-RACK2-ACC-VC2-S1-S2-90.15
ge-1/0/13.0        -                   b0:33:a6:7c:0b:c0   uplink-to-distribution-switch 7F-RACK1-ACC-SW7_90.18
xe-0/0/3           ae7                 54:4b:8c:aa:bb:07   xe-0/0/1           mystery-box
"""

JUNOS_LLDP_DETAIL = """LLDP Neighbor Information:
Local Information:
Index: 9 Time to live: 120 Time mark: Fri Nov  6 10:00:00 2026 Age: 20 secs
Local Interface    : xe-0/0/3
Parent Interface   : ae7
Neighbour Information:
Chassis type       : Mac address
Chassis ID         : 54:4b:8c:aa:bb:07
System name        : mystery-box
Management Information:
  Address Type      : IPv4(1)
  Address           : 90.90.90.33
"""

CISCO_LLDP_DETAIL = """------------------------------------------------
Local Intf: Gi1/0/48
Chassis id: 5c71.0d00.1111
Port id: ge-0/0/12
System Name: dsw-agg.mgm.local

Management Addresses:
    IP: 90.90.90.2
"""

CDP_DETAIL = """-------------------------
Device ID: MALAR-CSW
Entry address(es):
  IP address: 10.20.0.1
Platform: Juniper,  Capabilities: Router Switch
Interface: GigabitEthernet1/0/48,  Port ID (outgoing port): xe-0/0/2
"""

ETHERCHANNEL_SUMMARY = """Group  Port-channel  Protocol    Ports
------+-------------+-----------+-----------------------------------------------
1      Po1(SU)         LACP      Gi1/0/49(P)  Gi1/0/50(P)
2      Po2(SD)         LACP      Gi1/0/51(D)
"""
