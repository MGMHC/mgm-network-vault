"""SonicWall HA: both units of an active/standby pair are read from the config's high-availability block."""
import json

from network_vault.engine import parse_ha

HA_CONFIG = """
high-availability
    heartbeat-interval 1000

    mode active-standby
        primary-serial 18C241C5D602
        secondary-serial 18C241C5FA9E
        stateful-synchronization
        exit
"""


def test_parse_ha_returns_both_units_with_active_state():
    members = parse_ha(HA_CONFIG, active_serial="18C241C5D602", model="NSa 4800")
    assert [(m["id"], m["serial"], m["role"], m["model"], m["this"]) for m in members] == [
        ("Primary", "18C241C5D602", "Primary (Active)", "NSa 4800", True),
        ("Secondary", "18C241C5FA9E", "Secondary (Standby)", "NSa 4800", False),
    ]


def test_parse_ha_after_failover_marks_secondary_active():
    roles = [m["role"] for m in parse_ha(HA_CONFIG, active_serial="18C241C5FA9E", model="")]
    assert roles == ["Primary (Standby)", "Secondary (Active)"]


def test_parse_ha_without_ha_or_with_unset_peer_is_empty():
    assert parse_ha("interface X0\n    exit\n", "18C241C5D602", "") == []
    unset = HA_CONFIG.replace("18C241C5FA9E", "000000000000")
    assert parse_ha(unset, "18C241C5D602", "") == []


def test_ha_members_are_not_reported_degraded(app):
    from network_vault.models import Device
    d = Device(name="x", platform="sonicwall_sonicos", is_vc=True)
    d.vc_members_json = json.dumps(parse_ha(HA_CONFIG, "18C241C5D602", "NSa 4800"))
    assert not d.vc_degraded


HA_STATUS = """=========================
High Availability Status:
=========================

HA Mode:                        Active / Standby
Status:                         Primary ACTIVE
Primary State:                  ACTIVE
Secondary State:                STANDBY
Found Peer:                     Yes
Settings Synchronized:          Yes
"""


def _degraded(members):
    from network_vault.models import Device
    d = Device(name="x", platform="sonicwall_sonicos", is_vc=True)
    d.vc_members_json = json.dumps(members)
    return d.vc_degraded


def test_live_ha_status_gives_each_units_state(app):
    members = parse_ha(HA_CONFIG, "18C241C5D602", "NSa 4800", HA_STATUS)
    assert [(m["role"], m["status"]) for m in members] == [("Primary (Active)", "Prsnt"),
                                                           ("Secondary (Standby)", "Prsnt")]
    assert not _degraded(members)


def test_live_ha_status_wins_over_serial_guess():
    status = HA_STATUS.replace("Primary State:                  ACTIVE", "Primary State:                  STANDBY") \
                      .replace("Secondary State:                STANDBY", "Secondary State:                ACTIVE")
    assert [m["role"] for m in parse_ha(HA_CONFIG, "18C241C5D602", "", status)] == \
        ["Primary (Standby)", "Secondary (Active)"]


def test_lost_peer_is_degraded(app):
    members = parse_ha(HA_CONFIG, "18C241C5D602", "", HA_STATUS.replace("Found Peer:                     Yes",
                                                                       "Found Peer:                     No"))
    assert [m["status"] for m in members] == ["Prsnt", "Missing"]
    assert _degraded(members)


def test_peer_not_ready_is_degraded(app):
    members = parse_ha(HA_CONFIG, "18C241C5D602", "", HA_STATUS.replace("STANDBY", "IDLE"))
    assert [(m["role"], m["status"]) for m in members][1] == ("Secondary (Idle)", "Idle")
    assert _degraded(members)
