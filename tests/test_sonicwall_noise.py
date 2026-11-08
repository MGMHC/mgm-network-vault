"""SonicOS exports differ on every run (clock, uptime, secrets re-encrypted with a fresh salt) without
anyone changing the firewall; only a real change - which moves last-modified-by - may count."""
from network_vault import engine
from network_vault.alerts import parse_commit

FW = """firmware-version "SonicOS 8.2.2-8015"
system-time "11/07/2026 02:02:38.816"
system-uptime "11 Days, 10 Hours, 18 Minutes, 37 Seconds"
last-modified-by "dinesh 10.10.42.65:X26:V99 CLI 11/05/2026 12:46:38"
cli ftp password default 6,3574a986246305989ffbe33bc2f38174
radius server server1 shared-secret 6,aa11bb22cc33dd44ee55ff66 confirm-secret 6,0011223344556677
time
    use-ntp
    time 02:02:41
    date 2026:11:07
    exit
checksum 348459493
"""
NEXT_DAY = (FW.replace("11/07/2026 02:02:38.816", "11/08/2026 09:54:48.304")
              .replace("11 Days, 10 Hours, 18 Minutes, 37", "12 Days, 18 Hours, 10 Minutes, 47")
              .replace("3574a986246305989ffbe33bc2f38174", "9f1c0e2d7b6a5948372615049382716a")
              .replace("aa11bb22cc33dd44ee55ff66", "77ab12cd34ef56ab78cd90ef")
              .replace("time 02:02:41", "time 09:54:50").replace("date 2026:11:07", "date 2026:11:08")
              .replace("checksum 348459493", "checksum 348484609"))


def test_daily_export_noise_is_not_a_change():
    assert engine.config_hash(FW) == engine.config_hash(NEXT_DAY)


def test_user_change_moves_last_modified_by_and_counts():
    edited = NEXT_DAY.replace("dinesh 10.10.42.65:X26:V99 CLI 11/05/2026 12:46:38",
                              "admin 10.10.42.192:X26:V99 GUI 11/08/2026 09:40:00")
    assert engine.config_hash(FW) != engine.config_hash(edited)


def test_encrypted_values_are_masked_in_diffs():
    assert "6,<encrypted>" in "\n".join(engine.normalized(FW))
    assert "3574a986" not in "\n".join(engine.normalized(FW))


def test_last_modified_by_gives_author_and_method():
    last = engine.parse_sonicos_last_modified(FW)
    assert last == "11/05/2026 12:46:38 by dinesh via CLI from 10.10.42.65"
    assert parse_commit(last) == ("dinesh", "CLI")
    assert engine.parse_sonicos_last_modified("model x\n") is None


def test_other_platform_lines_starting_with_time_are_kept():
    assert engine.normalized("set system time-zone Asia/Kolkata\nclock timezone IST 5 30\n") == \
        ["set system time-zone Asia/Kolkata", "clock timezone IST 5 30"]


def test_github_mask_hides_sonicos_secrets():
    from network_vault.gitsync import mask
    masked = mask(FW, juniper=False)
    assert not engine.ENCRYPTED.search(masked)
    assert "shared-secret 6,<masked> confirm-secret 6,<masked>" in masked
