"""Backup schedule targets: several groups / roles / platforms at once, and excluded devices."""
import json

import pytest


def _ids(seed, *keys):
    return {seed["dev"][k][0] for k in keys}


@pytest.fixture()
def resolve(app):
    from network_vault.models import Schedule
    from network_vault.scheduler import resolve_targets

    def run(**kw):
        with app.app_context():
            return set(resolve_targets(Schedule(name="t", **kw)))
    return run


# ----------------------------------------------------------------------- resolving targets

def test_older_schedule_with_one_plain_value_still_works(resolve, seed):
    assert resolve(target="role", target_value="firewall") == _ids(seed, "fw")


def test_several_roles(resolve, seed):
    got = resolve(target="role", target_value=json.dumps(["core", "firewall"]))
    assert got == _ids(seed, "core", "fw")


def test_several_groups(resolve, seed):
    got = resolve(target="group", target_value=json.dumps(["Level-10", "Level-3"]))
    assert got == _ids(seed, "a", "b", "x1", "x2", "x3", "x4", "x5", "x6")


def test_several_platforms(resolve, seed):
    got = resolve(target="platform", target_value=json.dumps(["cisco_ios", "sonicwall_sonicos"]))
    assert got == _ids(seed, "core", "fw")


def test_exclude_from_a_group(resolve, seed):
    excl = f"{seed['dev']['x1'][0]},{seed['dev']['x2'][0]}"
    got = resolve(target="group", target_value=json.dumps(["Level-3"]), exclude_devices=excl)
    assert got == _ids(seed, "x3", "x4", "x5", "x6")


def test_exclude_from_all_devices(resolve, seed):
    everything = resolve(target="all")
    got = resolve(target="all", exclude_devices=str(seed["dev"]["core"][0]))
    assert got == everything - _ids(seed, "core")


def test_no_values_picked_backs_up_nothing_not_everything(resolve):
    assert resolve(target="role", target_value="") == set()
    assert resolve(target="group", target_value="[]") == set()


def test_garbage_in_stored_values_is_ignored(resolve, seed):
    assert resolve(target="role", target_value="[not json") == set()
    assert resolve(target="all", exclude_devices="x, ,12abc") == resolve(target="all")


# ----------------------------------------------------------------------- saving from the form

def _save(client, **fields):
    data = {"csrf_token": client.csrf, "name": "T multi", "frequency": "daily", "time": "02:00", "enabled": "on"}
    data.update(fields)
    return client.post("/schedules/save", data=data, follow_redirects=True)


@pytest.fixture()
def cleanup(app):
    yield
    from network_vault.models import Schedule, db
    with app.app_context():
        Schedule.query.filter(Schedule.name.like("T multi%")).delete(synchronize_session=False)
        db.session.commit()


def _stored(app):
    from network_vault.models import Schedule
    with app.app_context():
        s = Schedule.query.filter_by(name="T multi").one()
        return s.target, s.target_values, s.excluded_ids


def test_save_several_roles_with_exclusions(app, writer, seed, cleanup):
    a = seed["dev"]["a"][0]
    r = _save(writer, target="role", target_role=["access", "core"], exclude_devices=[str(a)])
    assert r.status_code == 200 and "Schedule saved" in r.get_data(as_text=True)
    assert _stored(app) == ("role", ["access", "core"], [a])


def test_save_ignores_unknown_values_and_rejects_an_empty_pick(app, writer, cleanup):
    r = _save(writer, target="role", target_role=["bogus"])
    assert "Pick at least one role" in r.get_data(as_text=True)
    from network_vault.models import Schedule
    with app.app_context():
        assert Schedule.query.filter_by(name="T multi").count() == 0
    _save(writer, target="platform", target_platform=["cisco_ios", "nope"])
    assert _stored(app)[1] == ["cisco_ios"]


def test_selected_devices_target_does_not_keep_exclusions(app, writer, seed, cleanup):
    a, b = seed["dev"]["a"][0], seed["dev"]["b"][0]
    _save(writer, target="devices", target_devices=[str(a)], exclude_devices=[str(b)])
    assert _stored(app)[2] == []


# ----------------------------------------------------------------------- page

def test_form_offers_multi_select_and_exclude(admin):
    html = admin.get("/schedules").get_data(as_text=True)
    for name in ("target_group", "target_role", "target_platform", "target_devices", "exclude_devices"):
        assert f'name="{name}" multiple data-lg-multi' in html, name


def test_table_and_dashboard_list_every_value_and_exclusions(app, admin, writer, seed, cleanup):
    _save(writer, target="role", target_role=["core", "firewall"], exclude_devices=[str(seed["dev"]["fw"][0])])
    html = admin.get("/schedules").get_data(as_text=True)
    assert "core, firewall" in html and "excluding" in html and "T-FW" in html
    assert "core, firewall" in admin.get("/").get_data(as_text=True)


def test_empty_date_shows_a_dash_not_garbled_text():
    from network_vault.web import fmt_dt
    assert fmt_dt(None) == "—"
