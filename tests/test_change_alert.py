"""Config-changed email alert: backups -> planned emails (engine.start_backup_job -> alerts -> mailer).

Backups run through the real engine with a stubbed `collect`; mail goes to a stub, never to SMTP.
"""
import pytest

from network_vault import alerts, engine
from network_vault.models import AlertMessage, Backup, Device, User, db, get_setting, set_setting

SECRET = '"$9$abcDEF123secretXYZ"'
BASE_SET = "set system host-name {n}\nset vlans v10 vlan-id 10\n"


class Mailbox:
    def __init__(self):
        self.sent = []
        self.fail = False

    def send(self, to, subject, html, text=None, inline_images=None):
        if self.fail:
            raise RuntimeError("smtp exploded")
        self.sent.append({"to": to, "subject": subject, "html": html, "text": text or ""})
        return True, None

    def to(self):
        return sorted(m["to"] for m in self.sent)


@pytest.fixture()
def mail(app, seed, monkeypatch):
    """Alert settings on, two recipients (group admin, HC site admin), synchronous sending, stub mailer."""
    box = Mailbox()
    monkeypatch.setattr(alerts, "send_email", box.send)
    monkeypatch.setattr(alerts, "_spawn", lambda fn, *a: fn(*a))  # same code path, no thread
    with app.app_context():
        saved = {k: get_setting(k) for k in ("alert_change_enabled", "alert_change_manual", "alert_change_diff_lines",
                                              "smtp_enabled", "app_url")}
        set_setting("alert_change_enabled", "1")
        set_setting("alert_change_manual", "1")
        set_setting("alert_change_diff_lines", "40")
        set_setting("smtp_enabled", "1")
        set_setting("app_url", "https://vault.example")
        User.query.filter_by(username="t_admin").one().email = "ga@example.com"
        User.query.filter_by(username="t_siteadmin").one().email = "sa@example.com"
        AlertMessage.query.filter_by(trigger="config_changed").update({"enabled": True, "site_ids_json": "[]"})
        db.session.commit()
    yield box
    with app.app_context():
        for k, v in saved.items():
            set_setting(k, v)
        for name in ("t_admin", "t_siteadmin"):
            User.query.filter_by(username=name).one().email = None
        db.session.commit()


@pytest.fixture()
def backup(app, seed, monkeypatch):
    """backup(dev_key, set_config, trigger, last_commit, fail) -> job result after a full backup job."""
    state = {}

    def fake_collect(device):
        if state.get("fail"):
            raise RuntimeError("ssh timeout")
        sc = state["set_config"]
        return {"config": sc, "set_config": sc, "info": "", "facts": {}, "vc": None,
                "last_commit": state.get("last_commit")}

    monkeypatch.setattr(engine, "collect", fake_collect)

    def run(dev_key, set_config=None, trigger="schedule", last_commit=None, fail=False):
        state.update(set_config=set_config, last_commit=last_commit, fail=fail)
        dev_id, _name = seed["dev"][dev_key]
        engine.start_backup_job(app, [dev_id], trigger=trigger, wait=True)
        with app.app_context():
            return Backup.query.filter_by(device_id=dev_id).order_by(Backup.id).all()
    return run


def _baseline(backup, key, name):
    return backup(key, BASE_SET.format(n=name))


def test_change_emails_the_sites_recipients_with_masked_diff_and_compare_link(mail, backup, seed):
    _baseline(backup, "x1", "T-ACC-X1")
    assert mail.sent == []  # first backup is a baseline, never a change
    backups = backup("x1", BASE_SET.format(n="T-ACC-X1") + f"set system radius-server 10.1.1.1 secret {SECRET}\n"
                     "set vlans v20 vlan-id 20\n", last_commit="2026-11-07 10:00:00 UTC by alice via cli commit")
    assert mail.to() == ["ga@example.com", "sa@example.com"]
    m = mail.sent[0]
    assert "T-ACC-X1" in m["subject"]
    assert "set vlans v20 vlan-id 20" in m["html"] and "10.1.1.1" in m["html"]
    assert "abcDEF123secretXYZ" not in m["html"] + m["text"] and "&lt;masked&gt;" in m["html"]
    assert "alice" in m["html"] and "MGMHC" in m["html"]
    prev, new = backups[0], backups[1]
    link = f"https://vault.example/compare?a={prev.id}&b={new.id}"
    assert link in m["text"] and link in m["html"].replace("&amp;", "&")


def test_only_recipients_covering_the_site_get_it(mail, backup, seed):
    """Malar device: the HC-only site admin is not a recipient, the group admin is."""
    _baseline(backup, "mal", "T-MAL-ACC")
    backup("mal", BASE_SET.format(n="T-MAL-ACC") + "set vlans v30 vlan-id 30\n")
    assert mail.to() == ["ga@example.com"]


@pytest.mark.parametrize("trigger", ["manual", "syslog-commit"])
def test_manual_and_syslog_backups_alert_too(mail, backup, trigger):
    _baseline(backup, "x2", "T-ACC-X2")
    backup("x2", BASE_SET.format(n="T-ACC-X2") + "set vlans v40 vlan-id 40\n", trigger=trigger)
    assert len(mail.sent) == 2


def test_no_change_no_email(mail, backup):
    _baseline(backup, "x3", "T-ACC-X3")
    backup("x3", BASE_SET.format(n="T-ACC-X3"))
    assert mail.sent == []


def test_first_backup_no_email(mail, backup):
    assert len(_baseline(backup, "x4", "T-ACC-X4")) == 1
    assert mail.sent == []


def test_failed_backup_sends_no_change_email(mail, backup):
    _baseline(backup, "x5", "T-ACC-X5")
    backups = backup("x5", None, fail=True)
    assert len(backups) == 1
    assert [m for m in mail.sent if "changed" in m["subject"].lower()] == []


def test_toggle_off_no_email(app, mail, backup):
    with app.app_context():
        set_setting("alert_change_enabled", "0")
        db.session.commit()
    _baseline(backup, "x6", "T-ACC-X6")
    backup("x6", BASE_SET.format(n="T-ACC-X6") + "set vlans v50 vlan-id 50\n")
    assert mail.sent == []


def test_diff_over_the_cap_is_truncated_with_more_lines_note(app, mail, backup):
    with app.app_context():
        set_setting("alert_change_diff_lines", "150")
        db.session.commit()
    _baseline(backup, "a", "T-ACC-A")
    mail.sent.clear()  # the seeded history makes this baseline a change too
    big = BASE_SET.format(n="T-ACC-A") + "".join(f"set vlans big{i} vlan-id {1000 + i}\n" for i in range(200))
    backup("a", big)
    m = mail.sent[0]
    assert m["html"].count("set vlans big") == 150
    assert "50 more lines" in m["html"] and "(50 more lines)" in m["text"]
    assert "+200" in m["text"] or "+200 /" in m["text"]


def test_send_failure_does_not_fail_the_backup(app, mail, backup, seed):
    _baseline(backup, "b", "T-ACC-B")
    mail.fail = True
    backups = backup("b", BASE_SET.format(n="T-ACC-B") + "set vlans v60 vlan-id 60\n")
    assert len(backups) == 2 and backups[-1].changed
    with app.app_context():
        dev = db.session.get(Device, seed["dev"]["b"][0])
        assert dev.last_backup_status == "ok"


def test_render_failure_does_not_fail_the_backup(app, mail, backup, seed, monkeypatch):
    def boom(*a, **k):
        raise ValueError("template broke")
    monkeypatch.setattr(alerts, "render", boom)
    _baseline(backup, "dist", "T-DIST")
    backups = backup("dist", BASE_SET.format(n="T-DIST") + "set vlans v70 vlan-id 70\n")
    assert len(backups) == 2 and mail.sent == []


def test_dispatch_failure_does_not_break_the_backup_job(app, mail, backup, seed, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("dispatch broke")
    monkeypatch.setattr(alerts, "dispatch", boom)
    _baseline(backup, "x1", "T-ACC-X1")
    backups = backup("x1", BASE_SET.format(n="T-ACC-X1") + "set vlans v80 vlan-id 80\n")
    assert backups[-1].changed


def test_junos_comment_only_change_counts(app, seed, monkeypatch):
    """Junos leaves 'annotate' comments out of 'display set': a comment-only change differs only in the
    hierarchical config, which is what the hash covers. It must still count as a change."""
    conf = "system {\n    host-name T-ACC-X1;\n}\nvlans {\n    v10 {\n        vlan-id 10;\n    }\n}\n"
    state = {"config": conf}

    def fake_collect(device):
        return {"config": state["config"], "set_config": BASE_SET.format(n="T-ACC-X1"), "info": "",
                "facts": {}, "vc": None, "last_commit": None}

    monkeypatch.setattr(engine, "collect", fake_collect)
    dev_id, _ = seed["dev"]["x1"]
    engine.start_backup_job(app, [dev_id], wait=True)
    state["config"] = conf.replace("vlans {", "/* uplink VLANs */\nvlans {")
    engine.start_backup_job(app, [dev_id], wait=True)
    with app.app_context():
        assert Backup.query.filter_by(device_id=dev_id).order_by(Backup.id.desc()).first().changed
