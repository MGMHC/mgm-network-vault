"""Who changed the config: Juniper 'show system commit' and the Cisco running-config header line."""
from network_vault.alerts import parse_commit
from network_vault.engine import parse_ios_last_change

IOS_CONFIG = """Building configuration...

Current configuration : 20480 bytes
!
! Last configuration change at 15:05:28 IST Sat Nov 7 2026 by mgmadmin
! NVRAM config last updated at 15:05:41 IST Sat Nov 7 2026 by mgmadmin
!
version 16.12
"""


def test_ios_last_change_line_gives_author():
    last = parse_ios_last_change(IOS_CONFIG)
    assert last == "15:05:28 IST Sat Nov 7 2026 by mgmadmin"
    assert parse_commit(last) == ("mgmadmin", "")


def test_ios_change_without_user_or_header_is_none():
    assert parse_ios_last_change("! Last configuration change at 15:05:28 IST Sat Nov 7 2026\n") \
        == "15:05:28 IST Sat Nov 7 2026"
    assert parse_commit("15:05:28 IST Sat Nov 7 2026") == ("unknown", "")
    assert parse_ios_last_change("hostname x\n") is None


def test_juniper_commit_still_parses_user_and_method():
    assert parse_commit("2026-11-05 10:54:02 IST by mgm-admin via cli commit synchronize") == ("mgm-admin", "cli")
