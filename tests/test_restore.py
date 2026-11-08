"""Export -> restore round trip: backup files must land where the app reads them (config backup_dir)."""
import sqlite3

from network_vault import portable

PASS = "correct horse battery"
TABLES = ("site", "device", "backup", "schedule", "credential", "user", "log_entry")


def _data_dir(path):
    path.mkdir()
    con = sqlite3.connect(path / portable.DB_FILE)
    for t in TABLES:
        con.execute(f'CREATE TABLE "{t}" (id INTEGER)')
    con.commit()
    con.close()
    (path / portable.SECRET_KEY_FILE).write_text("k1")
    (path / portable.FLASK_KEY_FILE).write_text("k2")
    return path


def test_restore_puts_backups_in_the_configured_backup_dir(tmp_path):
    src, src_bk = _data_dir(tmp_path / "src"), tmp_path / "src-backups"
    (src_bk / "SW1").mkdir(parents=True)
    (src_bk / "SW1" / "SW1_1.conf").write_text("hostname SW1")
    out = tmp_path / "x.nvault"
    portable.export_archive(src, src_bk, out, PASS)

    dst, dst_bk = _data_dir(tmp_path / "dst"), tmp_path / "dst-backups"
    (dst_bk / "OLD").mkdir(parents=True)
    (dst_bk / "OLD" / "old.conf").write_text("old")
    portable.stage_restore(dst, out, PASS)
    done = portable.apply_pending_restore(dst, dst_bk)

    assert (dst_bk / "SW1" / "SW1_1.conf").read_text() == "hostname SW1"
    assert not (dst / "backups").exists()
    kept = tmp_path / done["previous_backups_kept_in"]
    assert (kept / "OLD" / "old.conf").read_text() == "old"


def test_restore_with_default_backup_dir_keeps_backups_in_data(tmp_path):
    src = _data_dir(tmp_path / "src")
    (src / "backups" / "SW1").mkdir(parents=True)
    (src / "backups" / "SW1" / "a.conf").write_text("x")
    out = tmp_path / "x.nvault"
    portable.export_archive(src, src / "backups", out, PASS)
    dst = _data_dir(tmp_path / "dst")
    portable.stage_restore(dst, out, PASS)
    portable.apply_pending_restore(dst, dst / "backups")
    assert (dst / "backups" / "SW1" / "a.conf").read_text() == "x"
