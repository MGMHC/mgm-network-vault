from network_vault import vlan_audit


def test_live_port_status_is_saved_and_found_under_any_port_spelling(app):
    # The live scan reports long names; the audit parses short ones from the saved config.
    vlan_audit._save_live_statuses(9001, {"GigabitEthernet1/0/7": {"status": "connected"}})
    cache = vlan_audit._load_live_cache()
    assert cache[vlan_audit._live_key(9001, "Gi1/0/7")] == "connected"
    assert cache[vlan_audit._live_key(9001, "gi1/0/7")] == "connected"
