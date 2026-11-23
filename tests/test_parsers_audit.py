from network_vault import health, lifecycle, optics, vlan_audit


def test_cisco_vlan_name_not_overwritten_by_switchport_line():
    cfg = "vlan 10\n name Users\n!\ninterface Gi1/0/1\n switchport access vlan 10\n switchport mode access\n"
    names = {v["id"]: v["name"] for v in vlan_audit.parse_cisco_vlans_and_ports(cfg)["vlans"]}
    assert names[10] == "Users"


def test_junos_optics_space_inf_is_loss_of_light():
    txt = ("Physical interface: xe-0/1/0\n"
           "    Receiver signal average optical power     :  0.0000 mW / - Inf dBm\n"
           "    Laser output power                        :  0.5000 mW / -3.01 dBm\n")
    p = optics.parse_junos_optics(txt)[0]
    assert p["rx_dbm"] == -40.0
    assert p["status"] != "ok"


def test_cisco_ios_temp_ignores_thresholds():
    env = ("Inlet Temperature Value: 34 Degree Celsius\n"
           "Temperature State: GREEN\n"
           "Yellow Threshold : 46 Degree Celsius\n"
           "Red Threshold    : 56 Degree Celsius\n")
    assert health.parse_cisco_ios_health("", "", env)["temp_c"] == 34


def test_cisco_ios_stack_fan_fault_wins():
    env = "SW 1 FAN is OK\nSW 2 FAN is FAULTY\n"
    assert health.parse_cisco_ios_health("", "", env)["fans_status"] == "bad"


def test_c1300_fan_fault_wins():
    env = "unit 1 fan is ok\nunit 2 fan is fault\n"
    assert health.parse_cisco_c1300_health("", env)["fans_status"] == "bad"


def test_firmware_newer_or_equal_is_compliant():
    f = lifecycle.check_firmware_compliance
    assert f("23.4R2-S5", "23.4R2-S3.9")[0] == "match"
    assert f("12.3R12-S21.1", "12.3R12-S21")[0] == "match"
    assert f("23.4R2-S3", "23.4R2-S5")[0] == "diverged"

def test_cisco_ios_hotspot_sensor_name_is_not_a_fault():
    env = ("SYSTEM TEMPERATURE is OK\nInlet Temperature Value: 34 Degree Celsius\n"
           "Hotspot Temperature Value: 43 Degree Celsius\nFAN 1 is OK\nPOWER SUPPLY 1A is FAULTY\n")
    res = health.parse_cisco_ios_health("", "", env)
    assert res["temp_status"] == "ok" and res["fans_status"] == "ok"


def test_old_sonicos_is_not_compliant_with_newer_target():
    status, _ = lifecycle.check_firmware_compliance("SonicOS Enhanced 6.5.4.15-116n", "SonicOS 8.2.2-8015")
    assert status == "diverged"
