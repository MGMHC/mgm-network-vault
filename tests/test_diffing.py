"""Backup comparison helpers (diffing.py) and the compare page built on them."""
from network_vault import diffing

OLD = "hostname sw1\nvlan 10\nvlan 20\ninterface Gi1\n"
NEW = "hostname sw1\nvlan 10\nvlan 30\ninterface Gi1\nno shutdown\n"


def test_stats_counts_added_and_removed_lines():
    assert diffing.stats(OLD, NEW) == (2, 1)


def test_stats_identical_and_empty():
    assert diffing.stats(OLD, OLD) == (0, 0)
    assert diffing.stats("", "") == (0, 0)
    assert diffing.stats("", "a\nb\n") == (2, 0)


def test_side_by_side_marks_replace_and_insert():
    rows = diffing.side_by_side(OLD, NEW)
    kinds = [r[0] for r in rows]
    assert kinds == ["equal", "equal", "replace", "equal", "insert"]
    replace = rows[2]
    assert (replace[1], replace[2], replace[3], replace[4]) == (3, "vlan 20", 3, "vlan 30")
    insert = rows[4]
    assert insert[1] is None and insert[4] == "no shutdown"


def test_side_by_side_delete_row():
    rows = diffing.side_by_side("a\nb\n", "a\n")
    assert rows[-1][0] == "delete" and rows[-1][2] == "b" and rows[-1][3] is None


def test_side_by_side_collapses_long_unchanged_runs():
    old = "\n".join(f"line{i}" for i in range(40)) + "\nold\n"
    new = "\n".join(f"line{i}" for i in range(40)) + "\nnew\n"
    rows = diffing.side_by_side(old, new, context=3)
    skips = [r for r in rows if r[0] == "skip"]
    assert len(skips) == 1 and "34 unchanged lines" in skips[0][2]
    assert len(rows) < 15
    assert len(diffing.side_by_side(old, new, context=None)) == 41


def test_unified_diff_text():
    text = diffing.unified(OLD, NEW, "a.conf", "b.conf")
    assert text.startswith("--- a.conf\n+++ b.conf")
    assert "-vlan 20" in text and "+vlan 30" in text and "+no shutdown" in text


def test_compare_page_shows_the_change(admin, seed):
    a, b = seed["backups"]
    html = admin.get(f"/compare?a={a}&b={b}").get_data(as_text=True)
    assert "vlan-id 20" in html


def test_compare_page_unified_mode_and_swapped_order(admin, seed):
    a, b = seed["backups"]
    r = admin.get(f"/compare?a={b}&b={a}&mode=unified")
    assert r.status_code == 200
    assert "+set vlans v20 vlan-id 20" in r.get_data(as_text=True)


def test_compare_download_is_a_diff(admin, seed):
    a, b = seed["backups"]
    r = admin.get(f"/compare.diff?a={a}&b={b}")
    assert r.status_code == 200 and b"vlan-id 20" in r.data


def test_compare_blocked_for_other_sites_backup(readonly, app, seed):
    """A site-limited user cannot diff a backup that belongs to another site's device."""
    from network_vault.models import Backup, db, now
    with app.app_context():
        other = Backup(device_id=seed["dev"]["mal"][0], path="T-MAL/x.conf", trigger="manual", sha256="0" * 64,
                       created_at=now())
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    try:
        r = readonly.get(f"/compare?a={seed['backups'][0]}&b={other_id}")
        assert r.status_code == 403
    finally:
        with app.app_context():
            db.session.delete(db.session.get(Backup, other_id))
            db.session.commit()
