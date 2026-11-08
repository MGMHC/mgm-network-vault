"""Raw syslog files are kept for the log retention period, then deleted."""
import os
import time

from network_vault import syslog_listener


def test_old_syslog_files_are_deleted(tmp_path):
    old, new, other = tmp_path / "syslog-SW1-2026-01-01.log", tmp_path / "syslog-SW1-2026-11-08.log", tmp_path / "keep.txt"
    for p in (old, new, other):
        p.write_text("x")
    ten_days_ago = time.time() - 10 * 86400
    for p in (old, other):
        os.utime(p, (ten_days_ago, ten_days_ago))
    assert syslog_listener.purge_old_files(tmp_path, days=7) == 1
    assert not old.exists() and new.exists() and other.exists()


def test_missing_folder_or_zero_days_deletes_nothing(tmp_path):
    assert syslog_listener.purge_old_files(tmp_path / "nope", days=7) == 0
    (tmp_path / "syslog-a.log").write_text("x")
    assert syslog_listener.purge_old_files(tmp_path, days=0) == 0
