"""Config normalisation / change hash (engine.py) and lifecycle rules (lifecycle.py)."""
import datetime

import pytest

from network_vault import engine, lifecycle

JUNOS = """## Last commit: 2026-03-01 10:00:00 UTC by admin
## Last changed: 2026-03-01 10:00:00 UTC
set system host-name sw1
set vlans v10 vlan-id 10
"""
CISCO = """Building configuration...
Current configuration : 1234 bytes
! Last configuration change at 10:00:00 UTC Mon Mar 1 2026
! NVRAM config last updated at 10:00:00 UTC Mon Mar 1 2026
hostname sw1
ntp clock-period 17179869
vlan 10
"""


def test_normalized_drops_volatile_lines_junos():
    assert engine.normalized(JUNOS) == ["set system host-name sw1", "set vlans v10 vlan-id 10"]


def test_normalized_drops_volatile_lines_cisco():
    assert engine.normalized(CISCO) == ["hostname sw1", "vlan 10"]


def test_normalized_handles_empty():
    assert engine.normalized("") == [] and engine.normalized(None) == []


def test_hash_ignores_timestamps_only_changes():
    later = JUNOS.replace("2026-03-01 10:00:00", "2026-05-09 23:59:59")
    assert engine.config_hash(JUNOS) == engine.config_hash(later)


def test_hash_changes_on_real_change():
    assert engine.config_hash(JUNOS) != engine.config_hash(JUNOS + "set vlans v20 vlan-id 20\n")


def test_hash_is_stable_sha256_hex():
    h = engine.config_hash(JUNOS)
    assert h == engine.config_hash(JUNOS) and len(h) == 64 and int(h, 16) >= 0


def test_hash_of_empty_config_is_defined():
    assert engine.config_hash("") == engine.config_hash(None)


def test_clean_strips_trailing_blank_and_vc_prompt_lines():
    out = engine.clean("\r\n\nset a b   \nset c d\n\n{master:0}\n")
    assert out == "set a b\nset c d\n"


# ------------------------------------------------------------------ lifecycle

def test_catalog_match_by_model_prefix():
    entry = lifecycle.match_lifecycle_entry("EX2200-48P-4G")
    assert entry and "EX2200" in entry["family"]
    assert lifecycle.match_lifecycle_entry("Totally-Unknown-Box") is None
    assert lifecycle.match_lifecycle_entry("", "ex3300-48p")["family"].startswith("Juniper EX3300")


FULL = {"eosw_date": "2021-01-01", "eosec_date": "2022-01-01", "eol_date": "2024-01-01"}


@pytest.mark.parametrize("today,key", [
    (datetime.date(2020, 6, 1), "supported"),  # far from any milestone
    (datetime.date(2021, 6, 1), "eosw"),         # past software maintenance
    (datetime.date(2022, 6, 1), "eosec"),        # past security support
    (datetime.date(2024, 1, 1), "eol"),          # end-of-life day itself
    (datetime.date(2030, 1, 1), "eol"),
])
def test_lifecycle_status_progression(today, key):
    assert lifecycle.calculate_lifecycle_status(FULL, today)[0] == key


def test_lifecycle_eos_soon_inside_final_year():
    assert lifecycle.calculate_lifecycle_status({"eol_date": "2024-01-01"}, datetime.date(2023, 6, 1))[0] == "eos_soon"


def test_lifecycle_unknown_without_entry():
    assert lifecycle.calculate_lifecycle_status(None)[0] == "unknown"


def test_lifecycle_days_remaining_counts_down():
    status, label, days = lifecycle.calculate_lifecycle_status({"eol_date": "2024-01-01"}, datetime.date(2023, 12, 22))
    assert status == "eos_soon" and days == 10 and "10 days" in label


def test_lifecycle_days_negative_after_eol():
    status, _label, days = lifecycle.calculate_lifecycle_status({"eol_date": "2024-01-01"}, datetime.date(2024, 1, 11))
    assert status == "eol" and days == -10


@pytest.mark.parametrize("running,target,state", [
    ("", "15.2", "unknown"), ("15.2", "", "unknown"),
    ("15.2", " 15.2 ", "match"), ("15.2", "15.2", "match"), ("12.3", "15.2", "diverged"),
])
def test_firmware_compliance(running, target, state):
    assert lifecycle.check_firmware_compliance(running, target)[0] == state


def test_analyze_device_lifecycle_runs_on_devices(app, seed):
    from network_vault.models import Device
    with app.app_context():
        out = lifecycle.analyze_device_lifecycle(Device.query.all())
    assert isinstance(out, dict) and out
