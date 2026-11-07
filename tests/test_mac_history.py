"""MAC/IP history: scheduled sweep, storage, retention, search, site limits, scheduler wiring, pages."""
import threading
from datetime import timedelta

import pytest

from network_vault import engine, machistory, scheduler as sched
from network_vault.models import Device, MacSighting, Setting, db, now

JUNOS_MAC = "show ethernet-switching table | no-more"
JUNOS_ARP = "show arp no-resolve | no-more"
CISCO_ARP = "show ip arp"

PC = "84:a9:3e:76:ea:b4"
PC_CLEAN = "84a93e76eab4"
PHONE = "00:15:65:11:22:33"


def mac_line(mac, port, vlan="v120"):
    return f"   {vlan}   {mac}   D   -   {port}   0   0\n"


@pytest.fixture(autouse=True)
def clean_state(app, seed):
    def wipe():
        with app.app_context():
            MacSighting.query.delete()
            Setting.query.filter(Setting.key.like("mac_history_%")).delete(synchronize_session=False)
            db.session.commit()
    wipe()
    yield
    wipe()


@pytest.fixture()
def names(seed):
    d = seed["dev"]
    out = {k: v[1] for k, v in d.items()}
    out["ids"] = {k: v[0] for k, v in d.items()}
    return out


@pytest.fixture()
def sweep(app, switches, monkeypatch):
    monkeypatch.setattr(machistory, "MAC_HISTORY_WORKERS", 2)
    switches.reset()
    return lambda: machistory.run_sweep(app)


def rows(app):
    with app.app_context():
        return [(r.mac, r.device.name, r.port, r.vlan, r.ip, r.first_seen, r.last_seen)
                for r in MacSighting.query.order_by(MacSighting.id).all()]


def add_row(app, mac, dev_id, port, ip=None, last=None, first=None, vlan="v120"):
    with app.app_context():
        t = last or now()
        db.session.add(MacSighting(mac=mac, ip=ip, device_id=dev_id, port=port, vlan=vlan,
                                   first_seen=first or t, last_seen=t))
        db.session.commit()


# ------------------------------------------------------------------ sweep

def test_sweep_stores_edge_ports_and_skips_uplinks(app, sweep, switches, names):
    switches.script[(names["a"], JUNOS_MAC)] = (
        mac_line(PC, "ge-0/0/14.0") + mac_line(PHONE, "ge-0/0/14.0") + mac_line("aa:bb:cc:00:00:01", "ae12.0"))
    # a port with three or more MACs is a downstream switch, not an edge port
    switches.script[(names["b"], JUNOS_MAC)] = "".join(
        mac_line(f"aa:bb:cc:00:01:0{i}", "ge-0/0/20.0") for i in range(3)) + mac_line("aa:bb:cc:00:02:01", "ge-0/0/21.0")
    summary = sweep()
    got = {(r[1], r[2]) for r in rows(app)}
    assert got == {(names["a"], "ge-0/0/14"), (names["b"], "ge-0/0/21")}
    assert summary["new"] == 3 and summary["failed"] == 0
    assert {r[0] for r in rows(app) if r[1] == names["a"]} == {PC_CLEAN, "001565112233"}


def test_sweep_only_sends_read_only_show_commands(app, sweep, switches, names):
    switches.script[(names["a"], JUNOS_MAC)] = mac_line(PC, "ge-0/0/14.0")
    sweep()
    cmds = switches.cmds()
    assert cmds and all(c.startswith("show ") for c in cmds)
    assert JUNOS_ARP in switches.cmds(names["dist"]) and CISCO_ARP in switches.cmds(names["core"])
    assert JUNOS_ARP not in switches.cmds(names["a"])           # ARP only on distribution/core
    assert names["fw"] not in switches.queried()                # firewalls are not switches


def test_repeat_sighting_updates_last_seen_not_a_new_row(app, sweep, switches, names, monkeypatch):
    switches.script[(names["a"], JUNOS_MAC)] = mac_line(PC, "ge-0/0/14.0")
    sweep()
    first = rows(app)
    later = now() + timedelta(hours=4)
    monkeypatch.setattr(machistory, "now", lambda: later)
    summary = sweep()
    after = rows(app)
    assert len(first) == len(after) == 1
    assert after[0][5] == first[0][5] and after[0][6] == later
    assert summary["new"] == 0 and summary["updated"] == 1


def test_moved_mac_gets_a_new_row(app, sweep, switches, names):
    switches.script[(names["a"], JUNOS_MAC)] = mac_line(PC, "ge-0/0/14.0")
    sweep()
    switches.script[(names["a"], JUNOS_MAC)] = mac_line(PC, "ge-0/0/15.0")
    sweep()
    switches.script[(names["a"], JUNOS_MAC)] = ""
    switches.script[(names["b"], JUNOS_MAC)] = mac_line(PC, "ge-0/0/3.0")
    sweep()
    places = sorted((r[1], r[2]) for r in rows(app))
    assert places == sorted([(names["a"], "ge-0/0/14"), (names["a"], "ge-0/0/15"), (names["b"], "ge-0/0/3")])


def test_ip_is_attached_from_arp(app, sweep, switches, names):
    switches.script[(names["a"], JUNOS_MAC)] = mac_line(PC, "ge-0/0/14.0")
    switches.script[(names["dist"], JUNOS_ARP)] = f"{PC}   10.12.0.77   irb.120   [ae1.0]   none\n"
    sweep()
    assert [r[4] for r in rows(app)] == ["10.12.0.77"]
    # the IP stays when a later sweep has no ARP answer, and follows when it changes
    switches.script[(names["dist"], JUNOS_ARP)] = ""
    sweep()
    assert rows(app)[0][4] == "10.12.0.77"
    switches.script[(names["dist"], JUNOS_ARP)] = f"{PC}   10.12.0.99   irb.120   [ae1.0]   none\n"
    sweep()
    assert rows(app)[0][4] == "10.12.0.99"


def test_one_switch_failing_does_not_stop_the_others(app, sweep, switches, names, monkeypatch):
    switches.script[(names["a"], JUNOS_MAC)] = mac_line(PC, "ge-0/0/14.0")
    switches.script[(names["b"], JUNOS_MAC)] = mac_line(PHONE, "ge-0/0/9.0")
    real = engine.connect

    def flaky(device, *a, **k):
        if device.name == names["a"]:
            raise TimeoutError("ssh timed out")
        return real(device, *a, **k)
    monkeypatch.setattr(engine, "connect", flaky)
    summary = sweep()
    assert summary["failed"] == 1
    assert [(r[1], r[2]) for r in rows(app)] == [(names["b"], "ge-0/0/9")]
    with app.app_context():
        from network_vault.models import LogEntry
        msgs = [x.message for x in LogEntry.query.filter_by(category="history").all()]
    assert any(names["a"] in m and "skipped" in m for m in msgs)
    assert any("sweep finished" in m for m in msgs)


def test_two_sweeps_never_run_at_once(app, sweep, switches):
    assert machistory._SWEEP_LOCK.acquire(blocking=False)
    try:
        assert machistory.is_running()
        assert sweep() is None
        assert switches.queried() == []
    finally:
        machistory._SWEEP_LOCK.release()


# ------------------------------------------------------------------ scheduler / settings

def test_defaults_are_off_4h_90d(app):
    from network_vault.models import get_setting
    with app.app_context():
        assert get_setting("mac_history_enabled") == "0"
        assert get_setting("mac_history_interval_hours") == "4"
        assert get_setting("mac_history_retention_days") == "90"


def test_toggle_off_means_no_sweep_and_no_job(app, switches, monkeypatch):
    switches.reset()
    assert machistory.sweep_if_enabled(app) is None
    assert switches.queried() == []
    monkeypatch.setattr(sched, "_app", app)
    sched.sync_schedules()
    assert sched.scheduler.get_job("mac-history") is None


def test_toggle_on_registers_job_with_interval_and_off_removes_it(app, switches, monkeypatch):
    from network_vault.models import set_setting
    monkeypatch.setattr(sched, "_app", app)
    with app.app_context():
        set_setting("mac_history_enabled", "1")
        set_setting("mac_history_interval_hours", "6")
        db.session.commit()
    try:
        sched.sync_schedules()
        job = sched.scheduler.get_job("mac-history")
        assert job is not None and job.trigger.interval == timedelta(hours=6)
        switches.reset()
        assert machistory.sweep_if_enabled(app) is not None
        assert switches.queried()
        with app.app_context():
            set_setting("mac_history_enabled", "0")
            db.session.commit()
        sched.sync_schedules()
        assert sched.scheduler.get_job("mac-history") is None
    finally:
        job = sched.scheduler.get_job("mac-history")
        if job:
            job.remove()


def test_settings_form_saves_and_validates(admin, app):
    from network_vault.models import get_setting
    body = {"csrf_token": admin.csrf, "mac_history_enabled": "1", "mac_history_interval_hours": "2",
            "mac_history_retention_days": "30"}
    assert admin.post("/settings", data=body).status_code == 302
    with app.app_context():
        assert (get_setting("mac_history_enabled"), get_setting("mac_history_interval_hours"),
                get_setting("mac_history_retention_days")) == ("1", "2", "30")
    bad = dict(body, mac_history_interval_hours="0", mac_history_retention_days="-5")
    admin.post("/settings", data=bad)
    with app.app_context():
        assert get_setting("mac_history_interval_hours") == "2"
        assert get_setting("mac_history_retention_days") == "30"
    admin.post("/settings", data={"csrf_token": admin.csrf})
    with app.app_context():
        assert get_setting("mac_history_enabled") == "0"
    page = admin.get("/settings").get_data(as_text=True)
    assert "mac_history_interval_hours" in page and "mac_history_retention_days" in page


def test_site_admin_cannot_change_history_settings(site_admin, app):
    from network_vault.models import get_setting
    site_admin.post("/settings", data={"csrf_token": site_admin.csrf, "mac_history_enabled": "1"})
    with app.app_context():
        assert get_setting("mac_history_enabled") == "0"


# ------------------------------------------------------------------ retention

def test_purge_removes_only_old_rows(app, names):
    ids = names["ids"]
    add_row(app, "aaaaaaaaaaa1", ids["a"], "ge-0/0/1", last=now() - timedelta(days=91))
    add_row(app, "aaaaaaaaaaa2", ids["a"], "ge-0/0/2", last=now() - timedelta(days=89))
    add_row(app, "aaaaaaaaaaa3", ids["a"], "ge-0/0/3")
    with app.app_context():
        assert machistory.purge_old() == 1
        assert sorted(r.mac for r in MacSighting.query.all()) == ["aaaaaaaaaaa2", "aaaaaaaaaaa3"]
        assert machistory.purge_old(days=30) == 1


def test_housekeeping_purges_history(app, names, monkeypatch):
    monkeypatch.setattr(sched, "_app", app)
    add_row(app, "aaaaaaaaaaa1", names["ids"]["a"], "ge-0/0/1", last=now() - timedelta(days=400))
    sched.housekeeping()
    assert rows(app) == []


# ------------------------------------------------------------------ search / pages / access

@pytest.fixture()
def history(app, names):
    ids = names["ids"]
    old = now() - timedelta(days=3)
    add_row(app, PC_CLEAN, ids["a"], "ge-0/0/14", ip="10.12.0.77", first=old, last=old)
    add_row(app, PC_CLEAN, ids["b"], "ge-0/0/3", ip="10.12.0.77")
    add_row(app, "001565112233", ids["a"], "ge-0/0/22", ip="10.12.0.5")
    add_row(app, "001565aabbcc", ids["mal"], "ge-0/0/7", ip="10.20.0.9")
    return names


def page_for(client, q):
    return client.get("/operations/mac-history", query_string={"q": q}).get_data(as_text=True)


@pytest.mark.parametrize("q", [PC, PC.upper(), "84-a9-3e-76-ea-b4", "84a9.3e76.eab4", PC_CLEAN])
def test_search_by_mac_any_format(admin, history, q):
    html = page_for(admin, q)
    assert html.count(f'<td class="mono">{PC}</td>') == 2 and PHONE not in html


def test_search_by_ip_switch_and_port(admin, history):
    html = page_for(admin, "10.12.0.5")
    assert PHONE in html and PC not in html
    html = page_for(admin, history["b"])
    assert html.count(f'<td class="mono">{PC}</td>') == 1 and PHONE not in html
    html = page_for(admin, "ge-0/0/22")
    assert PHONE in html and PC not in html
    assert "Nothing matches" in page_for(admin, "no-such-thing")


def test_results_newest_first(admin, history):
    html = page_for(admin, PC)
    assert html.index("ge-0/0/3") < html.index("ge-0/0/14")


def test_empty_search_lists_recent_history(admin, history):
    assert "10.20.0.9" in page_for(admin, "")


def test_site_limited_user_only_sees_their_sites(readonly, admin, history):
    mine = page_for(readonly, "001565")
    assert PHONE in mine and "00:15:65:aa:bb:cc" not in mine
    assert history["mal"] not in page_for(readonly, "")
    assert "00:15:65:aa:bb:cc" in page_for(admin, "001565")
    assert readonly.get("/api/operations/mac-history/00:15:65:aa:bb:cc").get_json()["rows"] == []
    assert len(admin.get("/api/operations/mac-history/00:15:65:aa:bb:cc").get_json()["rows"]) == 1


def test_read_only_user_cannot_run_sweep_and_has_no_button(readonly, history, monkeypatch):
    called = threading.Event()
    monkeypatch.setattr(machistory, "run_sweep", lambda app: called.set())
    r = readonly.post("/operations/mac-history/sweep", data={"csrf_token": readonly.csrf})
    assert r.status_code == 403 and not called.wait(0.3)
    assert "Run sweep now" not in readonly.get("/operations/mac-history").get_data(as_text=True)


def test_writer_can_run_sweep_now(writer, history, monkeypatch):
    called = threading.Event()
    monkeypatch.setattr(machistory, "run_sweep", lambda app: called.set())
    assert "Run sweep now" in writer.get("/operations/mac-history").get_data(as_text=True)
    r = writer.post("/operations/mac-history/sweep", data={"csrf_token": writer.csrf})
    assert r.status_code == 302 and called.wait(3)


def test_page_shows_last_sweep_and_menu_entry(admin, sweep, switches, names):
    switches.script[(names["a"], JUNOS_MAC)] = mac_line(PC, "ge-0/0/14.0")
    sweep()
    html = admin.get("/operations/mac-history").get_data(as_text=True)
    assert "Last sweep" in html and "1 new" in html and PC in html
    assert 'href="/operations/mac-history"' in html           # Operations menu entry


def test_page_before_any_sweep(admin):
    html = admin.get("/operations/mac-history").get_data(as_text=True)
    assert "No sweep has run yet" in html and "Automatic sweeps are off" in html


def test_port_locator_has_seen_before_and_api_returns_last_locations(admin, history):
    page = admin.get("/operations/port-locator").get_data(as_text=True)
    assert 'id="seenBeforeWrap"' in page and "Seen before" in page and "loadSeenBefore" in page
    data = admin.get(f"/api/operations/mac-history/{PC}").get_json()
    assert [r["port"] for r in data["rows"]] == ["ge-0/0/3", "ge-0/0/14"]
    assert data["rows"][0]["ip"] == "10.12.0.77" and data["rows"][0]["mac"] == PC
    assert admin.get("/api/operations/mac-history/nonsense").status_code == 400
    assert admin.get("/api/operations/mac-history/de:ad:be:ef:00:01").get_json()["rows"] == []


def test_anonymous_is_redirected(anon):
    assert anon.get("/operations/mac-history").status_code == 302


def test_deleting_a_device_removes_its_history(app, writer, seed, history):
    with app.app_context():
        d = Device(name="T-TMP", host="10.9.9.9", site_id=seed["hc_id"], role="access", platform="juniper_junos",
                   enabled=True)
        db.session.add(d)
        db.session.commit()
        tmp = d.id
    add_row(app, "0000000000aa", tmp, "ge-0/0/1")
    writer.post(f"/devices/{tmp}/delete", data={"csrf_token": writer.csrf})
    with app.app_context():
        assert MacSighting.query.filter_by(device_id=tmp).count() == 0


# ------------------------------------------------------------------ review fixes

def _block_on(monkeypatch, name, gate):
    real = engine.connect

    def connect(device, *a, **k):
        if device.name == name:
            gate.wait(20)
        return real(device, *a, **k)
    monkeypatch.setattr(engine, "connect", connect)


def _history_logs(app):
    with app.app_context():
        from network_vault.models import LogEntry
        return [x.message for x in LogEntry.query.filter_by(category="history").all()]


def test_hung_switch_is_cut_off_and_the_sweep_finishes(app, sweep, switches, names, monkeypatch):
    monkeypatch.setattr(machistory, "SWITCH_TIMEOUT", 0.3)
    monkeypatch.setattr(machistory, "POLL_SECONDS", 0.05)
    switches.script[(names["a"], JUNOS_MAC)] = mac_line(PC, "ge-0/0/14.0")
    switches.script[(names["b"], JUNOS_MAC)] = mac_line(PHONE, "ge-0/0/9.0")
    gate = threading.Event()
    _block_on(monkeypatch, names["a"], gate)
    try:
        summary = sweep()
        assert summary is not None and summary["failed"] == 1
        assert not machistory.is_running()                       # lock released
        assert [(r[1], r[2]) for r in rows(app)] == [(names["b"], "ge-0/0/9")]
        assert any(names["a"] in m and "timed out" in m for m in _history_logs(app))
    finally:
        gate.set()
    import time
    time.sleep(0.3)                                              # the late answer is ignored
    assert [(r[1], r[2]) for r in rows(app)] == [(names["b"], "ge-0/0/9")]
    assert sweep() is not None                                   # and later sweeps are not blocked


def test_sweep_deadline_skips_switches_never_reached(app, sweep, switches, names, monkeypatch):
    monkeypatch.setattr(machistory, "MAC_HISTORY_WORKERS", 1)
    monkeypatch.setattr(machistory, "SWEEP_DEADLINE", 0.3)
    monkeypatch.setattr(machistory, "POLL_SECONDS", 0.05)
    gate = threading.Event()
    _block_on(monkeypatch, names["dist"], gate)                  # distribution/core are queried first
    try:
        summary = sweep()
        assert summary["failed"] == summary["switches"] - 1 and not machistory.is_running()  # T-CORE finished first
        assert any("sweep deadline" in m for m in _history_logs(app))
    finally:
        gate.set()


def test_command_failure_is_counted_as_empty_not_as_read(app, sweep, switches, names):
    switches.script[(names["a"], JUNOS_MAC)] = RuntimeError("channel closed")
    switches.script[(names["b"], JUNOS_MAC)] = mac_line(PHONE, "ge-0/0/9.0")
    switches.script[(names["x1"], JUNOS_MAC)] = "error: syntax error, expecting <command>\n"
    summary = sweep()
    assert summary["ok"] == 1 and summary["failed"] == 0
    assert summary["empty"] == summary["switches"] - 1
    logs = _history_logs(app)
    assert any(names["a"] in m and "no MAC table entries" in m for m in logs)
    assert any(names["x1"] in m and "no MAC table entries" in m for m in logs)
    with app.app_context():
        from network_vault.models import get_setting
        assert "returned no MAC table" in get_setting("mac_history_last_result")


def test_purge_is_skipped_and_logged_while_a_sweep_runs(app, names):
    add_row(app, "aaaaaaaaaaa1", names["ids"]["a"], "ge-0/0/1", last=now() - timedelta(days=400))
    assert machistory._SWEEP_LOCK.acquire(blocking=False)
    try:
        with app.app_context():
            assert machistory.purge_old() == 0
    finally:
        machistory._SWEEP_LOCK.release()
    assert len(rows(app)) == 1
    assert any("purge skipped" in m for m in _history_logs(app))
    with app.app_context():
        assert machistory.purge_old() == 1
    assert not machistory.is_running()


def test_a_device_that_blows_up_only_skips_that_device(app, names, monkeypatch):
    from types import SimpleNamespace

    def boom(*a, **k):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(machistory, "db", SimpleNamespace(session=SimpleNamespace(get=boom)))
    r = machistory._collect_switch(app, names["ids"]["a"])
    assert r["ok"] is False and "database is locked" in r["error"]
    monkeypatch.undo()
    assert machistory._collect_switch(app, 999999)["ok"] is False


def test_each_switch_is_committed_on_its_own(app, sweep, switches, names, monkeypatch):
    switches.script[(names["a"], JUNOS_MAC)] = mac_line(PC, "ge-0/0/14.0")
    switches.script[(names["b"], JUNOS_MAC)] = mac_line(PHONE, "ge-0/0/9.0")
    real = machistory._store_switch

    def store(r, ips, stamp):
        if r["name"] == names["a"]:
            raise RuntimeError("stale data")
        return real(r, ips, stamp)
    monkeypatch.setattr(machistory, "_store_switch", store)
    summary = sweep()
    assert summary["new"] == 1
    assert [(r[1], r[2]) for r in rows(app)] == [(names["b"], "ge-0/0/9")]
    assert any(names["a"] in m and "could not store" in m for m in _history_logs(app))


def test_ip_choice_is_deterministic_and_sticky(app, sweep, switches, names):
    switches.script[(names["a"], JUNOS_MAC)] = mac_line(PC, "ge-0/0/14.0")
    arp = lambda *ips: "".join(f"{PC}   {ip}   irb.120   [ae1.0]   none\n" for ip in ips)  # noqa: E731
    switches.script[(names["dist"], JUNOS_ARP)] = arp("10.12.0.9", "10.12.0.3")
    sweep()
    assert rows(app)[0][4] == "10.12.0.3"                        # first in sorted order
    switches.script[(names["dist"], JUNOS_ARP)] = arp("10.12.0.1", "10.12.0.9")
    switches.script[(names["core"], CISCO_ARP)] = ""
    sweep()
    assert rows(app)[0][4] == "10.12.0.1"                        # stored IP gone: first sorted
    switches.script[(names["dist"], JUNOS_ARP)] = arp("10.12.0.1", "10.12.0.0")
    sweep()
    assert rows(app)[0][4] == "10.12.0.1"                        # stored IP still there: kept
